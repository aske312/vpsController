import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.api.support import api
import application_operation as operations
import operation_policy as policy


class OperationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def record(self, directory, identity, state):
        path = directory / f"{identity:032x}.json"
        operations.atomic_json(path, {"id": path.stem, "state": state})
        old = time.time() - 10 * 86400
        os.utime(path, (old, old))
        return path

    def test_revision_conflict_preserves_first_write_and_shared_policy(self):
        original = policy.read(self.root)
        saved = policy.configure(self.root, {"retention_days": 2, "disk_limit_mb": 3}, original["revision"])
        with self.assertRaises(operations.OperationConflict):
            policy.configure(self.root, {"retention_days": 1, "disk_limit_mb": 1}, original["revision"])
        self.assertEqual(policy.read(self.root / "mihomo"), saved)

    def test_retention_protects_active_unknown_and_artifact_owners_in_all_sources(self):
        policy.configure(self.root, {"retention_days": 1, "disk_limit_mb": 1}, policy.DEFAULTS["revision"])
        for base, directory in ((self.root, self.root / "operations"),
                                (self.root, self.root / "cdn-operations"),
                                (self.root / "mihomo", self.root / "mihomo" / "operations")):
            records = [self.record(directory, n, state) for n, state in enumerate(
                ("running", "unknown", "queued", "succeeded", "failed", "succeeded"), 1)]
            operations.atomic_json(base / "tmp" / "artifact" / ".vps-control-temp.json", {"operation_id": records[5].stem})
            operations.prune(base, None, directory=directory)
            self.assertEqual([path.exists() for path in records], [True, True, True, False, False, True])

    def test_corrupt_policy_never_deletes_using_defaults(self):
        record = self.record(self.root / "operations", 1, "succeeded")
        (self.root / "operation-history-settings.json").write_text("{broken", encoding="utf-8")
        operations.prune(self.root, None, byte_budget=0)
        self.assertTrue(record.exists())
        with self.assertRaises(ValueError):
            policy.status(self.root)

    def test_usage_includes_protected_records_and_reports_overflow(self):
        policy.configure(self.root, {"retention_days": 1, "disk_limit_mb": 1}, policy.DEFAULTS["revision"])
        path = self.record(self.root / "operations", 1, "unknown")
        path.write_text(json.dumps({"state": "unknown", "details": "x" * 1100000}), encoding="utf-8")
        operations.prune(self.root, None)
        status = policy.status(self.root)
        self.assertTrue(path.exists())
        self.assertTrue(status["sources"]["system"]["over_limit"])
        self.assertEqual(status["sources"]["system"]["used_bytes"], path.stat().st_size)
        self.assertFalse(status["sources"]["mihomo"]["over_limit"])

    def test_count_limit_removes_oldest_terminal_records_but_keeps_protected_ones(self):
        policy.configure(self.root, {'retention_days': 730, 'disk_limit_mb': 20, 'max_entries': 100}, policy.DEFAULTS['revision'])
        for base, directory in ((self.root, self.root / 'operations'), (self.root, self.root / 'cdn-operations'),
                                (self.root / 'mihomo', self.root / 'mihomo/operations')):
            records = [self.record(directory, n, 'failed' if n % 2 else 'succeeded') for n in range(1, 108)]
            for index, path in enumerate(records):
                at = time.time() - 1000 + index
                os.utime(path, (at, at))
            protected = [self.record(directory, 200 + n, state) for n, state in enumerate(('running', 'unknown', 'queued'))]
            operations.prune(base, records[0].stem, directory=directory)
            self.assertTrue(records[0].exists())
            self.assertFalse(records[1].exists())
            self.assertTrue(all(path.exists() for path in protected))
            self.assertEqual(len(list(directory.glob('*.json'))), 100)

    def test_legacy_policy_gains_count_default_and_invalid_count_never_overwrites_it(self):
        saved = policy.configure(self.root, {'retention_days': 90, 'disk_limit_mb': 20}, policy.DEFAULTS['revision'])
        self.assertEqual(saved['max_entries'], 1000)
        for value in (True, 99, 10001, None):
            with self.assertRaises(ValueError):
                policy.configure(self.root, {**saved, 'max_entries': value}, saved['revision'])
            self.assertEqual(policy.read(self.root), saved)


if __name__ == "__main__":
    unittest.main()
