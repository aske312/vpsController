import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
import application_operation as operations
import operation_log as journal
import operation_policy as policy


class OperationLogTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.identity = "a" * 32

    def test_public_stages_are_deduplicated_bounded_and_secret_free(self):
        value = {"id": self.identity, "state": "running", "progress": 10,
                 "message": "password=secret https://user:password@example.test/token Bearer abc", "environment": {"SECRET": "never-copy"}}
        with patch.object(journal, "MAX_EVENTS", 2):
            journal.append(self.root, value)
            journal.append(self.root, value)
            self.assertEqual(len(journal.get(self.root, "system", self.identity)["events"]), 1)
            journal.append(self.root, {**value, "progress": 50, "message": "Applying"})
            text = journal.location(self.root, "system", self.identity).read_text(encoding="utf-8")
            for secret in ("secret", "example.test", "abc", "never-copy"):
                self.assertNotIn(secret, text)
            journal.append(self.root, {**value, "state": "succeeded", "message": "Verified"})
        saved = journal.get(self.root, "system", self.identity)
        self.assertEqual(saved["dropped"], 1)
        self.assertEqual([event["message"] for event in saved["events"]], ["Applying", "Verified"])

    def test_retention_preserves_unfinished_and_recovery_owner_but_not_terminal_logs(self):
        paths = []
        for number, state in enumerate(("running", "unknown", "failed", "succeeded"), 1):
            identity = f"{number:032x}"
            journal.append(self.root, {"id": identity, "state": state, "message": state})
            path = journal.location(self.root, "system", identity)
            paths.append(path)
        operations.atomic_json(self.root / "dns-recovery.json", {"operation_id": paths[2].stem})
        for path in paths:
            old = time.time() - 8 * 86400
            os.utime(path, (old, old))
        journal.prune(self.root)
        self.assertEqual([path.exists() for path in paths], [True, True, True, False])
        (self.root / "operation-history-settings.json").write_text("{")
        with self.assertRaises(ValueError):
            journal.prune(self.root)
        self.assertTrue(paths[2].exists())

    def test_logging_failure_does_not_lose_committed_operation(self):
        current = self.root / "application-action.json"
        with patch.object(journal, "location", side_effect=OSError("disk failure")):
            operations.save(self.root, current, {"id": self.identity, "state": "succeeded"})
        self.assertEqual(operations.read(current)["state"], "succeeded")

    def test_disk_budget_prunes_only_finished_journals(self):
        journal.append(self.root, {"id": self.identity, "state": "unknown", "message": "Pending"})
        pending = journal.location(self.root, "system", self.identity)
        # A large protected archive remains visible as over limit.
        document = json.loads(pending.read_text(encoding="utf-8"))
        document["padding"] = "x" * (1024 * 1024)
        pending.write_text(json.dumps(document), encoding="utf-8")
        settings = policy.read(self.root)
        policy.configure(self.root, {**settings, "log_disk_limit_mb": 1}, settings["revision"])
        finished_id = "b" * 32
        journal.append(self.root, {"id": finished_id, "state": "succeeded", "message": "Done"})
        self.assertTrue(pending.exists())
        self.assertFalse(journal.location(self.root, "system", finished_id).exists())
        self.assertTrue(policy.status(self.root)["logs"]["over_limit"])

    def test_api_auth_source_isolation_and_missing_journal(self):
        client = TestClient(api.app)
        endpoint = f"/api/application/operations/{self.identity}/log"
        self.assertEqual(client.get(endpoint).status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        with patch.object(api, "DATA_DIR", self.root):
            journal.append(self.root, {"id": self.identity, "state": "running", "message": "System"})
            journal.append(self.root / "mihomo", {"id": self.identity, "state": "running", "message": "Mihomo"})
            self.assertEqual(client.get(endpoint).json()["events"][0]["message"], "System")
            self.assertEqual(client.get(endpoint + "?source=mihomo").json()["events"][0]["message"], "Mihomo")
            self.assertFalse(client.get(endpoint + "?source=cdn").json()["available"])
            self.assertEqual(client.get(endpoint + "?source=../").status_code, 422)
            self.assertEqual(client.get("/api/application/operations/invalid/log").status_code, 422)

    def test_legacy_policy_gets_log_defaults_without_resetting_history(self):
        (self.root / "operation-history-settings.json").write_text(json.dumps({"retention_days": 45, "disk_limit_mb": 3, "revision": "b" * 32}))
        saved = policy.read(self.root)
        self.assertEqual((saved["retention_days"], saved["log_retention_days"]), (45, 7))
        saved = policy.configure(self.root, {**saved, "log_retention_days": 2}, saved["revision"])
        self.assertEqual(saved["log_retention_days"], 2)
