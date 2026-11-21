"""Client reports cannot register devices, change profiles or enforce quotas."""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
from uuid import uuid4
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from tests.api.support import manager
from tests.api import test_subscription_formats as formats


def report():
    return dict(version=1, report_id=str(uuid4()), session_id=str(uuid4()),
                observed_at='2026-11-14T10:00:00Z', client_version='0.0.4',
                counters={'upload': 123, 'download': None},
                events=[{'time': '2026-11-14T09:59:00+03:00', 'event': 'traffic', 'delta_upload': 12}])


class ClientReportTests(unittest.TestCase):
    def setUp(self):
        formats.SubscriptionFormatTests.setUp(self)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.enterContext(patch.object(manager.subscription_signing, 'server_signer', return_value=Ed25519PrivateKey.generate()))
        self.fetch('Nodera/0.0.4 (Windows; Mihomo)', 'pc',
                   extra_headers=((b'x-client-name', b'Nodera'), (b'x-device-os', b'windows')))

    fetch = formats.SubscriptionFormatTests.fetch

    def send(self, payload=None, token='test-token', hwid='pc', prefix='/s'):
        return TestClient(manager.app).post(f'{prefix}/{token}/reports', json=payload or report(),
            headers={'X-Device-ID': hwid, 'X-Client-Name': 'Nodera', 'X-Device-OS': 'windows',
                     'User-Agent': 'Nodera/0.0.4 (Windows; Mihomo)'})

    def test_receive_idempotency_throttle_and_profile_isolation(self):
        before = deepcopy(self.store)
        payload = report()
        first = self.send(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertFalse(first.json()['duplicate'])
        self.assertTrue(self.send(payload, prefix='/api/mihomo/subscriptions').json()['duplicate'])
        limited = self.send()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers['retry-after'], '15')
        self.assertEqual(before, self.store)
        with closing(sqlite3.connect(manager.DATA_ROOT / 'client-reports/reports.sqlite3')) as db:
            rows = db.execute('SELECT profile, device, payload FROM reports').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], ('profile', self.store[0]['devices'][-1]['id']))
        self.assertIsNone(json.loads(rows[0][2])['counters']['download'])
        self.assertNotIn('test-token', rows[0][2])

    def test_unknown_or_wrong_bound_device_cannot_submit(self):
        token = manager.device_subscription_token('test-token', self.store[0]['devices'][-1]['id'])
        for kwargs, expected in [({'hwid': ''}, 403), ({'hwid': 'new'}, 403),
                                 ({'token': 'wrong'}, 404), ({'token': token, 'hwid': 'other'}, 403)]:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.send(**kwargs).status_code, expected)
        self.assertEqual(self.send(token=token).status_code, 200)

    def test_reissued_links_keep_reporting_for_the_registered_device(self):
        device = self.store[0]['devices'][-1]['id']
        self.fetch('Nodera/0.0.4 (Windows; Mihomo)', 'another-pc',
                   extra_headers=((b'x-client-name', b'Nodera'), (b'x-device-os', b'windows')))
        payload = report()
        self.assertEqual(self.send(payload).status_code, 200)
        old_bound = manager.device_subscription_token('test-token', device)
        manager.rotate_profile_subscription('profile', manager.profile_revision(self.store[0]), 'a' * 32)
        new_token = self.store[0]['subscription_token']
        new_bound = manager.device_subscription_token(new_token, device)
        before = deepcopy(self.store)

        for revoked in ('test-token', old_bound):
            self.assertEqual(self.send(payload, token=revoked).status_code, 404)
        self.assertEqual(self.send(payload, token=new_token, hwid='unregistered').status_code, 403)
        self.assertEqual(self.send(payload, token=new_bound, hwid='another-pc').status_code, 403)
        for token, prefix in ((new_token, '/s'), (new_bound, '/api/mihomo/subscriptions')):
            with self.subTest(prefix=prefix):
                result = self.send(payload, token=token, prefix=prefix)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertTrue(result.json()['duplicate'])
        self.assertEqual(self.store, before)

    def test_invalid_payload_and_raw_logs_are_rejected(self):
        cases = [dict(version=2), dict(counters={'upload': -1}), dict(counters={'download': True}),
                 dict(counters={'upload': 2**63}), dict(events=[{}] * 101),
                 dict(observed_at='2026-11-14T10:00:00'), dict(message='secret'),
                 dict(events=[{'time': '2026-11-14T10:00:00Z', 'event': 'opened', 'host': 'private.example'}])]
        for fields in cases:
            with self.subTest(fields=fields):
                self.assertEqual(self.send({**report(), **fields}).status_code, 422)

    def test_expired_and_readonly_subscriptions_cannot_write(self):
        with patch.object(manager.subscription_limits, 'status', return_value={'state': 'expired'}):
            self.assertEqual(self.send().status_code, 403)
        with patch.dict(manager.app.dependency_overrides, {manager.client_report_access: lambda: False}):
            self.assertEqual(self.send().status_code, 409)

    def test_reports_remain_available_during_runtime_operations(self):
        before = deepcopy(self.store)
        manager.application_operation.atomic_json(manager.DATA_ROOT.parent / 'application-action.json',
                                                  {'action': 'network-check', 'state': 'running'})
        with manager.application_operation.mutation_lock(manager.DATA_ROOT.parent):
            response = self.send()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store, before)

    def test_reports_are_blocked_for_unmanaged_component_and_update_recovery(self):
        with patch.object(manager, 'component_is_managed', return_value=False):
            self.assertEqual(self.send().status_code, 409)
        for action in ('update', 'test-update', 'test-rollback', 'safe-update', 'auto-safe-update', 'kernel-update'):
            with self.subTest(action=action):
                manager.application_operation.atomic_json(manager.DATA_ROOT.parent / 'application-action.json',
                                                          {'action': action, 'state': 'running'})
                self.assertEqual(self.send().status_code, 409)
        manager.application_operation.atomic_json(manager.DATA_ROOT.parent / 'application-action.json',
                                                  {'action': 'test-update', 'state': 'succeeded'})
        recovery = manager.DATA_ROOT.parent / 'recovery/application-update'
        recovery.mkdir(parents=True)
        self.assertEqual(self.send().status_code, 409)
        self.assertFalse((manager.DATA_ROOT / 'client-reports/reports.sqlite3').exists())

    def test_storage_retention_bounds_and_reuse_after_throttle(self):
        root = manager.DATA_ROOT
        schema = manager.client_reports.Report
        for index in range(105):
            manager.client_reports.accept(root, 'profile', 'device', schema.model_validate(report()), now=1000 + index * 16)
        with closing(sqlite3.connect(root / 'client-reports/reports.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reports').fetchone()[0], 100)
        manager.client_reports.accept(root, 'other', 'device', schema.model_validate(report()), now=1000 + 8 * 86400)
        with closing(sqlite3.connect(root / 'client-reports/reports.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reports').fetchone()[0], 1)

    def test_pro_gate_precedes_storage_and_capability_is_not_advertised_without_signing(self):
        with patch.object(manager.subscription_signing, 'server_signer', return_value=None):
            response = self.send()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.fetch('Nodera/0.0.11 (Windows; Mihomo)', 'pc').headers['X-Nodera-Reports-Version'], '0')
        self.assertFalse((manager.DATA_ROOT / 'client-reports/reports.sqlite3').exists())

    def test_diagnostics_reject_free_text_addresses_and_client_agent_configuration(self):
        diagnostics = {'state': 'connected', 'mode': 'proxy', 'core_version': 'v1.19.33',
                       'network_check': 'error', 'issues': ['https_probe_failed']}
        for extra in ({'host': 'private.example'}, {'api_key': 'secret'}, {'issues': ['arbitrary message']}, {'uptime_seconds': True}):
            with self.subTest(extra=extra):
                self.assertEqual(self.send({**report(), 'diagnostics': {**diagnostics, **extra}}).status_code, 422)
        self.assertEqual(self.send({**report(), 'diagnostics': diagnostics}).status_code, 200)

    def test_authorized_read_keeps_device_scope_unknown_values_and_freshness(self):
        self.assertEqual(self.send().status_code, 200)
        manager.client_reports.accept(manager.DATA_ROOT, 'foreign', 'foreign-device', manager.client_reports.Report.model_validate(report()))
        url = '/api/mihomo/profiles/profile/client-reports'
        client = TestClient(manager.app)
        self.assertEqual(client.get(url).status_code, 401)
        with patch.dict(manager.app.dependency_overrides, {manager.auth_required: lambda: None}):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['state'], 'recent')
            self.assertEqual(len(result['items']), 1)
            self.assertIsNone(result['items'][0]['counters']['download'])
            self.assertNotIn('foreign-device', response.text)
            self.assertNotIn('test-token', response.text)
            self.assertEqual(client.get(url + '?device_id=foreign-device').status_code, 404)
            with patch.object(manager.client_reports.time, 'time', return_value=manager.client_reports.time.time() + 100):
                self.assertEqual(client.get(url).json()['state'], 'stale')

    def test_report_read_is_readonly_when_no_store_exists_and_bounds_large_events(self):
        result = manager.client_reports.read(manager.DATA_ROOT, 'profile')
        self.assertEqual(result['state'], 'no_reports')
        self.assertFalse((manager.DATA_ROOT / 'client-reports').exists())
        payload = report()
        payload['events'] = [{'time': '2026-11-16T10:00:00Z', 'event': 'traffic', **{name: 2**63 - 1 for name in ('upload', 'download', 'delta_upload', 'delta_download')}} for _ in range(100)]
        self.assertEqual(self.send(payload).status_code, 200)
        result = manager.client_reports.read(manager.DATA_ROOT, 'profile')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 22000)
