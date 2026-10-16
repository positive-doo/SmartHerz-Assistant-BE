"""Offline failure checks and opt-in tests against the existing devplay MSSQL tables."""
import json
import os
import secrets
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit
from uuid import uuid4

import pyodbc
from fastapi import HTTPException
from fastapi.testclient import TestClient

import integration_key_links as links
import main


class KeyLinkFailureTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'INTEGRATION_KEY_SECRET': 'test-only-secret-' + 'x' * 32})
        environment.start()
        self.addCleanup(environment.stop)
        self.key_id = 'b' * 43
        self.key = links._partner_key(self.key_id)
        self.client = TestClient(main.app)
        self.payload = {'token': 'a' * 43, 'pin': '1234'}
        self.key_record_id = str(uuid4())

    def test_missing_secret_fails_before_sql_access(self):
        with patch.dict(os.environ, {'INTEGRATION_KEY_SECRET': ''}), patch.object(links, 'sql_connection') as sql:
            self.assertEqual(self.client.get('/integration/key').status_code, 200)
            self.assertEqual(self.client.post('/api/v1/integration/key-links/redeem', json=self.payload).status_code, 503)
            with self.assertRaises(HTTPException):
                links.create_link('http://127.0.0.1:8000', self.key_record_id)
            sql.assert_not_called()

    def test_sql_failure_is_generic_and_does_not_reveal_key(self):
        for error in [RuntimeError('private configuration'), pyodbc.Error('private connection details')]:
            with patch.object(links, 'sql_connection', side_effect=error):
                response = self.client.post('/api/v1/integration/key-links/redeem', json=self.payload)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(self.key, response.text)
                self.assertNotIn('private', response.text)
                for operation in [lambda: links.create_link('http://127.0.0.1:8000', self.key_record_id),
                                  lambda: links.require_partner_key(self.key)]:
                    with self.assertRaises(HTTPException) as raised:
                        operation()
                    self.assertEqual(raised.exception.status_code, 503)

    def test_failed_commit_never_returns_key(self):
        context = MagicMock()
        connection = context.__enter__.return_value
        cursor = connection.cursor.return_value
        cursor.execute.return_value = cursor
        cursor.fetchone.side_effect = [
            (links._pin_digest(self.payload['token'], self.payload['pin']), links._digest(self.key), 0, 0),
            (self.key_id,),
        ]
        connection.commit.side_effect = pyodbc.Error('commit failed')
        with patch.object(links, 'sql_connection', return_value=context):
            response = self.client.post('/api/v1/integration/key-links/redeem', json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.key, response.text)

    def test_invalid_public_urls_and_key_ids_are_rejected_before_sql(self):
        for url in ['http://example.com', 'https://user:password@example.com', 'https://example.com/path', 'https://example.com/#fragment']:
            with self.subTest(url=url), patch.object(links, 'sql_connection') as sql, self.assertRaises(ValueError):
                links.create_link(url, self.key_record_id)
            sql.assert_not_called()
        for partner in ['', 'UPPER', 'a b', 'a' * 101, "a'; DELETE"]:
            with patch.object(links, 'sql_connection') as sql, self.assertRaises(ValueError):
                links.create_link('http://127.0.0.1:8000', partner)
            sql.assert_not_called()

    def test_legacy_shared_key_and_master_secret_are_never_accepted(self):
        with patch.dict(os.environ, {'INTEGRATION_API_KEY': 'old-shared-key'}), patch.object(links, 'sql_connection') as sql:
            for key in ['old-shared-key', os.environ['INTEGRATION_KEY_SECRET']]:
                with self.assertRaises(HTTPException) as raised:
                    links.require_partner_key(key)
                self.assertEqual(raised.exception.status_code, 401)
            sql.assert_not_called()


@unittest.skipUnless(os.getenv('SMARTHERZ_TEST_MSSQL') == '1', 'Set SMARTHERZ_TEST_MSSQL=1 to test the existing MSSQL database')
class KeyLinkSqlTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'INTEGRATION_KEY_SECRET': secrets.token_urlsafe(32)})
        environment.start()
        self.addCleanup(environment.stop)
        self.tokens, self.partners = [], []
        self.addCleanup(self.cleanup_rows)
        self.partner, self.key = self.new_partner()
        self.client = TestClient(main.app)

    def new_partner(self):
        partner = str(uuid4())
        self.partners.append(partner)
        key_id = secrets.token_urlsafe(32)
        key = links._partner_key(key_id)
        with links.sql_connection() as connection:
            cursor = connection.cursor()
            cursor.execute("""INSERT INTO admin.AdminApiKeys (api_key_id,app_id,name,key_prefix,key_hash,created_by,updated_by)
                VALUES (?,66,'SMART-HERZ test',?,?,'smartherz-test','smartherz-test')""", partner, key[:16], links._digest(key))
            cursor.execute('INSERT INTO devplay.SmartHerzPartners (partner_id,key_id,key_digest) VALUES (?,?,?)',
                           partner, key_id, links._digest(key))
            cursor.execute("""INSERT INTO admin.AdminApiKeyScopeAssignments (api_key_id,scope_id)
                SELECT ?,scope_id FROM admin.AdminApiKeyScopes WHERE app_id=66 AND scope='integration:read'""", partner)
            connection.commit()
        return partner, key

    def revoke(self, partner):
        with links.sql_connection() as connection:
            connection.cursor().execute('UPDATE admin.AdminApiKeys SET is_active=0,revoked_at=SYSUTCDATETIME() WHERE api_key_id=?', partner)
            connection.commit()

    def reissue(self, partner):
        key_id = secrets.token_urlsafe(32)
        key = links._partner_key(key_id)
        with links.sql_connection() as connection:
            cursor = connection.cursor()
            cursor.execute('UPDATE admin.AdminApiKeys SET key_hash=?,is_active=1,revoked_at=NULL WHERE api_key_id=?', links._digest(key), partner)
            cursor.execute('UPDATE devplay.SmartHerzPartners SET key_id=?,key_digest=? WHERE partner_id=?', key_id, links._digest(key), partner)
            connection.commit()
        return key

    def cleanup_rows(self):
        with links.sql_connection() as connection:
            cursor = connection.cursor()
            for token in self.tokens:
                cursor.execute('DELETE FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?', links._digest(token))
            for partner in self.partners:
                cursor.execute('DELETE FROM admin.AdminApiKeyScopeAssignments WHERE api_key_id=?', partner)
                cursor.execute('DELETE FROM admin.AdminApiKeys WHERE api_key_id=?', partner)
                cursor.execute('DELETE FROM devplay.SmartHerzPartners WHERE partner_id = ?', partner)
            connection.commit()

    def create(self, partner=None):
        result = links.create_link('http://127.0.0.1:8000', partner or self.partner, 1)
        token = urlsplit(result['url']).fragment
        self.tokens.append(token)
        return {'token': token, 'pin': result['pin']}

    def redeem(self, payload):
        return self.client.post('/api/v1/integration/key-links/redeem', json=payload)

    def auth_status(self, key):
        try:
            links.require_partner_key(key)
            return 200
        except HTTPException as error:
            return error.status_code

    def test_wrong_pin_then_one_success_and_no_reuse(self):
        payload = self.create()
        wrong = '0000' if payload['pin'] != '0000' else '1111'
        self.assertEqual(self.redeem({**payload, 'pin': wrong}).status_code, 403)
        response = self.redeem(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'api_key': self.key})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.redeem(payload).status_code, 410)

    def test_page_preview_does_not_consume_link_or_reveal_key(self):
        payload = self.create()
        for _ in range(2):
            page = self.client.get('/integration/key')
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(self.key, page.text)
            self.assertEqual(page.headers['cache-control'], 'no-store')
            nonce = page.headers['content-security-policy'].split("'nonce-")[1].split("'")[0]
            self.assertIn(f'<script nonce="{nonce}">', page.text)
        self.assertEqual(self.redeem(payload).status_code, 200)

    def test_five_wrong_pins_disable_link(self):
        payload = self.create()
        wrong = '0000' if payload['pin'] != '0000' else '1111'
        for expected in [403, 403, 403, 403, 410]:
            self.assertEqual(self.redeem({**payload, 'pin': wrong}).status_code, expected)
        self.assertEqual(self.redeem(payload).status_code, 410)

    def test_expiry_uses_sql_clock(self):
        payload = self.create()
        with links.sql_connection() as connection:
            connection.cursor().execute('UPDATE devplay.SmartHerzKeyLinks SET expires_at = DATEADD(second, -1, SYSUTCDATETIME()) WHERE token_digest = ?', links._digest(payload['token']))
            connection.commit()
        self.assertEqual(self.redeem(payload).status_code, 410)

    def test_misconfigured_secret_does_not_burn_link_or_return_another_key(self):
        payload = self.create()
        with patch.dict(os.environ, {'INTEGRATION_KEY_SECRET': 'replacement-secret-' + 'z' * 32}):
            response = self.redeem(payload)
            self.assertEqual(response.status_code, 503)
            with self.assertRaises(HTTPException):
                links.create_link('http://127.0.0.1:8000', self.partner)
        self.assertEqual(self.redeem(payload).json(), {'api_key': self.key})

    def test_only_one_concurrent_request_succeeds(self):
        payload = self.create()
        barrier = threading.Barrier(6)
        def redeem(_):
            with TestClient(main.app) as client:
                barrier.wait(timeout=15)
                return client.post('/api/v1/integration/key-links/redeem', json=payload).status_code
        with ThreadPoolExecutor(max_workers=6) as executor:
            self.assertEqual(sorted(executor.map(redeem, range(6))), [200, 410, 410, 410, 410, 410])

    def test_consumption_survives_a_new_process(self):
        payload = self.create()
        self.assertEqual(self.redeem(payload).status_code, 200)
        script = """import json, sys
from fastapi import HTTPException
from integration_key_links import RedeemRequest, redeem_link
try:
    redeem_link(RedeemRequest(**json.load(sys.stdin)))
except HTTPException as error:
    print(error.status_code)
"""
        process = subprocess.run([sys.executable, '-c', script], input=json.dumps(payload),
                                 text=True, capture_output=True, cwd=Path(main.__file__).parent, check=True, timeout=30)
        self.assertEqual(process.stdout.strip(), '410')

    def test_database_contains_no_plaintext_credentials(self):
        payload = self.create()
        with links.sql_connection() as connection:
            row = connection.cursor().execute('SELECT token_digest, pin_digest, key_digest FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?', links._digest(payload['token'])).fetchone()
            partner = connection.cursor().execute('SELECT key_id, key_digest FROM devplay.SmartHerzPartners WHERE partner_id = ?', self.partner).fetchone()
        self.assertTrue(all(len(value) == 64 for value in row))
        for secret in [self.key, payload['token'], payload['pin'], os.environ['INTEGRATION_KEY_SECRET']]:
            self.assertNotIn(secret, row)
            self.assertNotIn(secret, partner)

    def test_partners_have_different_keys_and_revocation_is_individual(self):
        other, other_key = self.new_partner()
        payload, other_payload = self.create(), self.create(other)
        self.assertNotEqual(self.key, other_key)
        self.assertEqual(self.auth_status(self.key), 200)
        self.assertEqual(self.auth_status(other_key), 200)
        self.revoke(self.partner)
        self.revoke(self.partner)
        self.assertEqual(self.auth_status(self.key), 401)
        self.assertEqual(self.redeem(payload).status_code, 410)
        self.assertEqual(self.auth_status(other_key), 200)
        self.assertEqual(self.redeem(other_payload).json(), {'api_key': other_key})
        with self.assertRaises(ValueError):
            self.create()

    def test_new_link_keeps_active_key_and_reissue_never_reactivates_old_key(self):
        old_payload = self.create()
        self.assertEqual(self.redeem(self.create()).json(), {'api_key': self.key})
        self.revoke(self.partner)
        self.reissue(self.partner)
        new_key = self.redeem(self.create()).json()['api_key']
        self.assertNotEqual(new_key, self.key)
        self.assertEqual(self.auth_status(self.key), 401)
        self.assertEqual(self.auth_status(new_key), 200)
        self.assertEqual(self.redeem(old_payload).status_code, 410)

    def test_revocation_racing_redemption_leaves_no_access(self):
        payload = self.create()
        barrier = threading.Barrier(2)
        def redeem():
            barrier.wait(timeout=15)
            return self.redeem(payload).status_code
        def revoke():
            barrier.wait(timeout=15)
            self.revoke(self.partner)
        with ThreadPoolExecutor(max_workers=2) as executor:
            response = executor.submit(redeem)
            executor.submit(revoke).result(timeout=30)
            self.assertIn(response.result(timeout=30), [200, 410])
        self.assertEqual(self.auth_status(self.key), 401)
        self.assertEqual(self.redeem(payload).status_code, 410)

    def test_adminapp_expiry_app_and_scope_are_enforced(self):
        for mutation in [
            'UPDATE admin.AdminApiKeys SET expires_at=DATEADD(second,-1,SYSUTCDATETIME()) WHERE api_key_id=?',
            'UPDATE admin.AdminApiKeys SET app_id=64 WHERE api_key_id=?',
            'DELETE FROM admin.AdminApiKeyScopeAssignments WHERE api_key_id=?',
        ]:
            partner, key = self.new_partner()
            payload = self.create(partner)
            with links.sql_connection() as connection:
                connection.cursor().execute(mutation, partner)
                connection.commit()
            self.assertEqual(self.auth_status(key), 401)
            self.assertEqual(self.redeem(payload).status_code, 410)

    def test_legacy_delivery_metadata_cannot_override_adminapp_revocation(self):
        self.revoke(self.partner)
        with links.sql_connection() as connection:
            self.assertIsNone(connection.cursor().execute('SELECT revoked_at FROM devplay.SmartHerzPartners WHERE partner_id=?', self.partner).fetchone()[0])
        self.assertEqual(self.auth_status(self.key), 401)


if __name__ == '__main__':
    unittest.main()
