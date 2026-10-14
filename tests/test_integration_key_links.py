"""Offline failure checks and opt-in tests against devplay.SmartHerzKeyLinks."""
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

import pyodbc
from fastapi import HTTPException
from fastapi.testclient import TestClient

import integration_key_links as links
import main


class KeyLinkFailureTests(unittest.TestCase):
    def setUp(self):
        self.key = 'test-only-key'
        environment = patch.dict(os.environ, {'INTEGRATION_API_KEY': self.key})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(main.app)
        self.payload = {'token': 'a' * 43, 'pin': '1234'}

    def test_missing_key_fails_before_sql_access(self):
        with patch.dict(os.environ, {'INTEGRATION_API_KEY': ''}), patch.object(links, 'sql_connection') as sql:
            self.assertEqual(self.client.get('/integration/key').status_code, 503)
            self.assertEqual(self.client.post('/api/v1/integration/key-links/redeem', json=self.payload).status_code, 503)
            sql.assert_not_called()

    def test_sql_failure_is_generic_and_does_not_reveal_key(self):
        for error in [RuntimeError('private configuration'), pyodbc.Error('private connection details')]:
            with patch.object(links, 'sql_connection', side_effect=error):
                response = self.client.post('/api/v1/integration/key-links/redeem', json=self.payload)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(self.key, response.text)
                self.assertNotIn('private', response.text)
                with self.assertRaises(HTTPException) as raised:
                    links.create_link('http://127.0.0.1:8000')
                self.assertEqual(raised.exception.status_code, 503)

    def test_failed_commit_never_returns_key(self):
        context = MagicMock()
        connection = context.__enter__.return_value
        connection.cursor.return_value.fetchone.return_value = (
            links._pin_digest(self.payload['token'], self.payload['pin']), links._digest(self.key), 0, 0)
        connection.commit.side_effect = pyodbc.Error('commit failed')
        with patch.object(links, 'sql_connection', return_value=context):
            response = self.client.post('/api/v1/integration/key-links/redeem', json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(self.key, response.text)

    def test_invalid_public_urls_are_rejected_before_sql(self):
        for url in ['http://example.com', 'https://user:password@example.com', 'https://example.com/path', 'https://example.com/#fragment']:
            with self.subTest(url=url), patch.object(links, 'sql_connection') as sql, self.assertRaises(ValueError):
                links.create_link(url)
            sql.assert_not_called()


@unittest.skipUnless(os.getenv('SMARTHERZ_TEST_MSSQL') == '1', 'Set SMARTHERZ_TEST_MSSQL=1 to test the existing MSSQL database')
class KeyLinkSqlTests(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_urlsafe(32)
        self.tokens = []
        environment = patch.dict(os.environ, {'INTEGRATION_API_KEY': self.key})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.cleanup_rows)
        self.client = TestClient(main.app)

    def cleanup_rows(self):
        if self.tokens:
            with links.sql_connection() as connection:
                for token in self.tokens:
                    connection.cursor().execute('DELETE FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?', links._digest(token))
                connection.commit()

    def create(self):
        result = links.create_link('http://127.0.0.1:8000', 1)
        token = urlsplit(result['url']).fragment
        self.tokens.append(token)
        return {'token': token, 'pin': result['pin']}

    def redeem(self, payload):
        return self.client.post('/api/v1/integration/key-links/redeem', json=payload)

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

    def test_rotation_does_not_expose_replacement_key(self):
        payload = self.create()
        with patch.dict(os.environ, {'INTEGRATION_API_KEY': 'replacement-key'}):
            response = self.redeem(payload)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('replacement-key', response.text)

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

    def test_database_contains_hashes_instead_of_plain_credentials(self):
        payload = self.create()
        with links.sql_connection() as connection:
            row = connection.cursor().execute('SELECT token_digest, pin_digest, key_digest FROM devplay.SmartHerzKeyLinks WHERE token_digest = ?', links._digest(payload['token'])).fetchone()
        self.assertTrue(all(len(value) == 64 for value in row))
        for secret in [self.key, payload['token'], payload['pin']]:
            self.assertNotIn(secret, row)


if __name__ == '__main__':
    unittest.main()
