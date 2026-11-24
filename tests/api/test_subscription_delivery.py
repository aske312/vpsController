"""Successful YAML delivery is durable telemetry, separate from profile edits."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import manager
from tests.api import test_subscription_formats as fixtures


class SubscriptionDeliveryTests(unittest.TestCase):
    fetch = fixtures.SubscriptionFormatTests.fetch

    def setUp(self):
        fixtures.SubscriptionFormatTests.setUp(self)
        self.enterContext(patch.object(manager.subscription_signing, 'server_signer', return_value=None))
        self.enterContext(patch.dict(manager.app.dependency_overrides, {manager.subscription_mutation_access: lambda: True}))
        self.client = TestClient(manager.app)

    def test_only_sent_yaml_records_profile_and_common_device_without_changing_revision(self):
        response = self.fetch('Mihomo/1.19', hwid=None)
        self.assertEqual(manager.subscription_delivery('profile'), {})
        revision = manager.profile_revision(self.store[0])
        saved = json.dumps(self.store)
        asyncio.run(response.background())
        record = manager.subscription_delivery('profile')
        at = record['last_subscription_fetched_at']
        self.assertEqual(record['devices'], {'common': at})
        self.assertEqual(manager.profile_revision(self.store[0]), revision)
        self.assertEqual(json.dumps(self.store), saved)
        self.assertEqual(manager.profile_response(self.store[0])['last_subscription_fetched_at'], at)
        self.assertEqual(manager.profile_response(self.store[0])['devices'][0]['last_subscription_fetched_at'], at)
        # Re-read from disk: there is no process-local timestamp cache.
        self.assertEqual(json.loads(manager.subscription_delivery_path('profile').read_text())['devices']['common'], at)

    def test_hwid_and_common_delivery_are_independent_and_failed_requests_do_not_advance_time(self):
        response = self.fetch('Mihomo/1.19', hwid='phone')
        device = next(row['id'] for row in self.store[0]['devices'] if row.get('hwid_hash'))
        asyncio.run(response.background())
        first = manager.subscription_delivery('profile')['devices'][device]
        response = self.fetch('Mihomo/1.19', hwid=None)
        with patch.object(manager.time, 'strftime', return_value='2030-01-02T03:04:05Z'):
            asyncio.run(response.background())
        record = manager.subscription_delivery('profile')
        self.assertEqual(record['devices'][device], first)
        self.assertEqual(record['devices']['common'], '2030-01-02T03:04:05Z')
        self.assertEqual(record['last_subscription_fetched_at'], '2030-01-02T03:04:05Z')
        self.assertEqual(manager.subscription_delivery('another-profile'), {})
        for headers, status in (({'Accept': 'text/html'}, 200), ({'User-Agent': 'Happ/3.0'}, 410)):
            self.assertEqual(self.client.get('/s/test-token', headers=headers).status_code, status)
            self.assertEqual(manager.subscription_delivery('profile'), record)
        self.assertEqual(self.client.get('/s/invalid-token').status_code, 404)
        with patch.object(manager, 'render_client_profile', side_effect=HTTPException(503, 'fixture failure')):
            self.assertEqual(self.client.get('/s/test-token', headers={'User-Agent': 'Mihomo/1.19'}).status_code, 503)
        self.assertEqual(manager.subscription_delivery('profile'), record)

    def test_http_aliases_record_successful_yaml_and_restarts_keep_metadata(self):
        for url in ('/s/test-token', '/api/mihomo/subscriptions/test-token'):
            response = self.client.get(url, headers={'User-Agent': 'Mihomo/1.19'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('last_subscription_fetched_at', manager.subscription_delivery('profile'))
        self.assertNotIn('test-token', manager.subscription_delivery_path('profile').read_text())

    def test_device_bound_link_records_its_device_without_marking_the_common_pool(self):
        response = self.fetch('Mihomo/1.19', hwid='phone')
        device = next(row['id'] for row in self.store[0]['devices'] if row.get('hwid_hash'))
        token = manager.device_subscription_token('test-token', device)
        # Registration alone has not completed the first response's background task.
        self.assertEqual(manager.subscription_delivery('profile'), {})
        response = self.client.get('/s/' + token, headers={'User-Agent': 'Mihomo/1.19', 'X-Hwid': 'phone'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(manager.subscription_delivery('profile')['devices']), {device})

    def test_interrupted_body_and_signature_failure_are_not_recorded(self):
        response = self.fetch('Mihomo/1.19', hwid=None)
        async def receive(): return {'type': 'http.request', 'body': b''}
        async def send(message):
            if message['type'] == 'http.response.body': raise OSError('disconnected')
        with self.assertRaises(OSError):
            asyncio.run(response({'type': 'http', 'method': 'GET', 'path': '/s/test-token'}, receive, send))
        self.assertEqual(manager.subscription_delivery('profile'), {})
        with patch.object(manager.subscription_signing, 'proof_headers', side_effect=ValueError('signing failed')):
            with self.assertRaises(ValueError): self.fetch('Mihomo/1.19', hwid=None)
        self.assertEqual(manager.subscription_delivery('profile'), {})

    def test_concurrent_devices_keep_both_timestamps_and_corrupt_metadata_is_preserved(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda device: manager.record_subscription_delivery('profile', device), ['phone', 'desktop']))
        self.assertEqual(set(manager.subscription_delivery('profile')['devices']), {'phone', 'desktop'})
        file = manager.subscription_delivery_path('profile')
        file.write_text('{"schema": 999, "devices": {}}')
        damaged = file.read_bytes()
        with self.assertLogs(manager.logger, level='ERROR'):
            self.assertEqual(manager.subscription_delivery('profile'), {})
            manager.record_subscription_delivery('profile', 'phone')
        self.assertEqual(file.read_bytes(), damaged)
