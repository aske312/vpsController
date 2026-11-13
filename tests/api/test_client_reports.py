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
        with patch.dict(manager.app.dependency_overrides, {manager.subscription_mutation_access: lambda: False}):
            self.assertEqual(self.send().status_code, 409)

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
