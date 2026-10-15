"""Composite rule deletion is admitted once and executed by the durable worker."""
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import manager
from tests.api import test_mihomo_operations as operation_tests
import application_operation as operations


class PersonalRuleDeleteOperationTests(unittest.TestCase):
    setUp = operation_tests.MihomoOperationTests.setUp

    def client(self):
        manager.app.dependency_overrides[manager.auth_required] = lambda: None
        self.addCleanup(manager.app.dependency_overrides.clear)
        return TestClient(manager.app)

    def test_delete_is_admitted_once_and_terminal_replay_never_deletes_again(self):
        client = self.client()
        identity = "a" * 32
        rule = "personal-" + "b" * 12
        with patch.object(manager, "delete_personal_rule") as remove:
            started = client.delete(f"/api/mihomo/routing/personal-rules/{rule}", headers={"X-Operation-ID": identity})
            self.assertEqual(started.status_code, 202, started.text)
            self.assertEqual(started.json()["action"], "mihomo-profile-personal-delete")
            remove.assert_not_called()
            self.assertEqual(client.delete(f"/api/mihomo/routing/personal-rules/{rule}", headers={"X-Operation-ID": identity}).status_code, 202)
            self.launch.assert_called_once()
            command = self.launch.call_args.args[0]
            for _ in range(2):
                self.assertTrue(manager.execute_profile_operation("personal-delete", Path(command[-2]), command[-1], identity, started.json()["unit"]))
            remove.assert_called_once_with(rule)
            self.assertEqual(operations.read(self.root / "operations" / f"{identity}.json")["state"], "succeeded")
            self.assertEqual(client.delete(f"/api/mihomo/routing/personal-rules/{rule}", headers={"X-Operation-ID": identity}).status_code, 202)
            remove.assert_called_once_with(rule)
            self.launch.assert_called_once()
            collision = client.delete("/api/mihomo/routing/personal-rules/personal-" + "c" * 12, headers={"X-Operation-ID": identity})
            self.assertEqual(collision.status_code, 409)

    def test_failed_worker_remains_failed_and_is_not_replayed(self):
        identity = "d" * 32
        started = manager.submit_personal_rule_delete("personal-" + "e" * 12, identity)
        command = self.launch.call_args.args[0]
        with patch.object(manager, "delete_personal_rule", side_effect=RuntimeError("delete failed")) as remove, patch.object(manager.logger, "exception"):
            for _ in range(2):
                self.assertFalse(manager.execute_profile_operation("personal-delete", Path(command[-2]), command[-1], identity, started["unit"]))
            remove.assert_called_once()
        self.assertEqual(operations.read(self.root / "operations" / f"{identity}.json")["state"], "failed")

    def test_invalid_rule_identity_and_unowned_component_cannot_admit_delete(self):
        client = self.client()
        self.assertEqual(client.delete("/api/mihomo/routing/personal-rules/invalid").status_code, 404)
        self.launch.assert_not_called()
        with patch.object(manager, "require_mihomo_management", side_effect=manager.HTTPException(status_code=409, detail="Unowned")):
            self.assertEqual(client.delete("/api/mihomo/routing/personal-rules/personal-" + "f" * 12).status_code, 409)
        self.launch.assert_not_called()
