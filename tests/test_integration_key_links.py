import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

import integration_key_links as links
import main


class KeyLinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / 'links.sqlite3'
        self.key = 'test-only-integration-key-with-sufficient-entropy'
        self.environment = patch.dict(os.environ, {
            'INTEGRATION_KEY_LINKS_DB': str(self.database), 'INTEGRATION_API_KEY': self.key,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(main.app)

    def create(self):
        result = links.create_link('http://127.0.0.1:8000', 1)
        return {'token': urlsplit(result['url']).fragment, 'pin': result['pin']}

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
            self.assertNotIn('__NONCE__', page.text)
            nonce = page.headers['content-security-policy'].split("'nonce-")[1].split("'")[0]
            self.assertIn(f'<script nonce="{nonce}">', page.text)
        self.assertEqual(self.redeem(payload).status_code, 200)

    def test_five_wrong_pins_disable_link(self):
        payload = self.create()
        wrong = '0000' if payload['pin'] != '0000' else '1111'
        for expected in [403, 403, 403, 403, 410]:
            self.assertEqual(self.redeem({**payload, 'pin': wrong}).status_code, expected)
        self.assertEqual(self.redeem(payload).status_code, 410)

    def test_expired_link_is_unavailable(self):
        with patch.object(links.time, 'time', return_value=1_700_000_000):
            payload = self.create()
        with patch.object(links.time, 'time', return_value=1_700_003_600):
            self.assertEqual(self.redeem(payload).status_code, 410)

    def test_rotation_does_not_expose_replacement_key(self):
        payload = self.create()
        with patch.dict(os.environ, {'INTEGRATION_API_KEY': 'replacement-key'}):
            response = self.redeem(payload)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('replacement-key', response.text)

    def test_only_one_concurrent_request_succeeds(self):
        payload = self.create()
        def redeem(_):
            with TestClient(main.app) as client:
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
                                 text=True, capture_output=True, cwd=Path(main.__file__).parent, check=True)
        self.assertEqual(process.stdout.strip(), '410')

    def test_database_does_not_store_plain_key_or_token(self):
        payload = self.create()
        content = self.database.read_bytes()
        self.assertNotIn(self.key.encode(), content)
        self.assertNotIn(payload['token'].encode(), content)

    def test_missing_configuration_and_store_fail_closed(self):
        payload = {'token': 'a' * 43, 'pin': '1234'}
        self.assertEqual(self.redeem(payload).status_code, 503)
        with patch.dict(os.environ, {'INTEGRATION_KEY_LINKS_DB': ''}):
            self.assertEqual(self.client.get('/integration/key').status_code, 503)
            self.assertEqual(self.redeem(payload).status_code, 503)

    def test_cli_requires_https_except_on_loopback(self):
        for url in ['http://example.com', 'https://user:password@example.com', 'https://example.com/path', 'https://example.com/#fragment']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                links.create_link(url)
        result = links.create_link('https://example.com')
        self.assertTrue(result['url'].startswith('https://example.com/integration/key#'))
        self.assertNotIn(self.key, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
