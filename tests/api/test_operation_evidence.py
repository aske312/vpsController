import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.api.support import api
import application_operation as operations
import assistant_control
import operation_evidence as evidence
import operation_log as journal


class OperationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.record = {'id': 'a' * 32, 'state': 'failed', 'result': 'exit-code', 'action': 'cdn-security',
                       'unit': 'vps-control-action-123.service', 'started_at': '2026-11-19T10:00:00+00:00',
                       'finished_at': '2026-11-19T10:01:00+00:00', 'systemd_invocation_id': 'b' * 32}
        self.enterContext(patch.object(assistant_control, '_read', return_value={'providers': [{'api_key': 'fixture-private-key'}]}))

    def output(self, message):
        return SimpleNamespace(stdout=json.dumps({'__REALTIME_TIMESTAMP': '1795082400000000', 'MESSAGE': message, 'PRIORITY': '3'}) + '\n')

    def test_capture_is_scoped_bounded_and_masks_secrets_before_persistence(self):
        message = 'TLS failed fixture-private-key admin-fixture password=guess Bearer abc https://private.example/path'
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'admin-fixture'}), patch.object(evidence.subprocess, 'run', return_value=self.output(message)) as run:
            result = evidence.capture(self.root, self.record)
        self.assertTrue(result['available'])
        encoded = json.dumps(result)
        for secret in ('fixture-private-key', 'admin-fixture', 'guess', 'abc', 'private.example'):
            self.assertNotIn(secret, encoded)
        args = run.call_args.args[0]
        self.assertIn(self.record['unit'], args)
        self.assertIn('_SYSTEMD_INVOCATION_ID=' + 'b' * 32, args)
        self.assertIn('--since', args)
        self.assertIn('--until', args)
        self.assertEqual(run.call_args.kwargs['timeout'], 4)
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertIn('TLS failed', result['lines'][0]['message'])

    def test_snapshot_survives_rotation_and_shares_existing_journal_retention(self):
        operations.atomic_json(self.root / 'operations' / ('a' * 32 + '.json'), self.record)
        with patch.object(evidence.subprocess, 'run', return_value=self.output('HTTP 520 at origin')) as run:
            journal.append(self.root, self.record)
            first = journal.get(self.root, 'system', 'a' * 32)
            second = journal.get(self.root, 'system', 'a' * 32)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first['evidence'], second['evidence'])
        self.assertIn('HTTP 520', first['evidence']['lines'][0]['message'])
        path = journal.location(self.root, 'system', 'a' * 32)
        os.utime(path, (1, 1))
        journal.prune(self.root)
        self.assertFalse(path.exists())
        self.assertEqual(operations.read(self.root / 'operations' / ('a' * 32 + '.json'))['state'], 'failed')

    def test_failed_capture_and_missing_unit_never_break_recording(self):
        with patch.object(evidence.subprocess, 'run', side_effect=subprocess.TimeoutExpired('journalctl', 4)):
            journal.append(self.root, self.record)
        saved = journal.get(self.root, 'system', 'a' * 32)
        self.assertEqual(saved['events'][0]['state'], 'failed')
        self.assertFalse(saved['evidence']['available'])
        self.assertEqual(evidence.capture(self.root, {'state': 'failed'})['reason'], 'operation_unit_not_recorded')

    def test_legacy_failure_gets_one_snapshot_when_its_journal_is_first_opened(self):
        operations.atomic_json(self.root / 'operations' / ('a' * 32 + '.json'), self.record)
        with patch.object(evidence.subprocess, 'run', return_value=self.output('old failure retained')) as run:
            result = journal.get(self.root, 'system', 'a' * 32)
            journal.get(self.root, 'system', 'a' * 32)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(result['evidence']['available'])
        self.assertEqual(result['events'], [])

    def test_corrupt_journal_is_not_rewritten_while_attempting_snapshot_capture(self):
        operations.atomic_json(self.root / 'operations' / ('a' * 32 + '.json'), self.record)
        path = journal.location(self.root, 'system', 'a' * 32)
        corrupt = {'id': 'a' * 32, 'source': 'system', 'events': 'corrupt', 'dropped': 0}
        operations.atomic_json(path, corrupt)
        with patch.object(evidence.subprocess, 'run') as run:
            with self.assertRaises(ValueError): journal.get(self.root, 'system', 'a' * 32)
        self.assertFalse(run.called)
        self.assertEqual(operations.read(path), corrupt)

    def test_delayed_journal_ingestion_can_recover_an_empty_snapshot_but_never_replaces_success(self):
        operations.atomic_json(self.root / 'operations' / ('a' * 32 + '.json'), self.record)
        with patch.object(evidence.subprocess, 'run', side_effect=subprocess.TimeoutExpired('journalctl', 4)):
            journal.append(self.root, self.record)
        path = journal.location(self.root, 'system', 'a' * 32)
        saved = operations.read(path)
        saved['evidence']['captured_at'] = '1970-01-01T00:00:00+00:00'
        operations.atomic_json(path, saved)
        with patch.object(evidence.subprocess, 'run', return_value=self.output('late journal message')) as run:
            first = journal.get(self.root, 'system', 'a' * 32)
            second = journal.get(self.root, 'system', 'a' * 32)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(first['evidence']['available'])
        self.assertEqual(first['evidence'], second['evidence'])
