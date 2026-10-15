import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
import application_operation as operations


class ApplicationCancelTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.current = self.root / "application-action.json"
        self.identity = "a" * 32
        self.action = {"id": self.identity, "unit": "vps-control-action-123.service", "action": "update", "state": "running", "progress": 20}
        self.enterContext(patch.object(api, "DATA_DIR", self.root))
        self.enterContext(patch.object(api, "ACTION_FILE", self.current))
        self.stop = self.enterContext(patch.object(api.subprocess, "run", return_value=SimpleNamespace(returncode=0)))
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        self.client = TestClient(api.app)
        operations.save(self.root, self.current, self.action)

    def cancel(self, identity=None):
        return self.client.delete("/api/application/action", headers={"X-Expected-Operation-ID": identity or self.identity})

    def test_requires_auth_and_confirmed_target_identity(self):
        api.app.dependency_overrides.clear()
        self.assertEqual(self.cancel().status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.assertEqual(self.client.delete("/api/application/action").status_code, 409)
        self.assertEqual(self.cancel("invalid").status_code, 409)
        self.stop.assert_not_called()

    def test_a_stale_card_never_stops_a_different_latest_operation(self):
        replacement = {**self.action, "id": "b" * 32, "unit": "vps-control-action-456.service"}
        operations.save(self.root, self.current, replacement)
        before = self.current.read_bytes()
        self.assertEqual(self.cancel().status_code, 409)
        self.assertEqual(self.current.read_bytes(), before)
        self.stop.assert_not_called()

    def test_supported_cancel_persists_intent_before_stop_and_does_not_repeat_it(self):
        for action in ("update", "test-rollback", "safe-update", "kernel-update"):
            with self.subTest(action=action):
                operations.save(self.root, self.current, {**self.action, "action": action})
                self.stop.reset_mock()

                def stop(*args, **kwargs):
                    self.assertTrue(operations.read(self.current)["cancel_requested"])
                    self.assertTrue(operations.read(self.root / "operations" / f"{self.identity}.json")["cancel_requested"])
                    self.assertEqual(args[0], ["systemctl", "stop", "--no-block", self.action["unit"]])
                    return SimpleNamespace(returncode=0)

                self.stop.side_effect = stop
                first = self.cancel()
                second = self.cancel()
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertEqual(first.json()["id"], self.identity)
                self.assertEqual(first.json()["state"], "running")
                self.assertTrue(second.json()["cancel_requested"])
                self.stop.assert_called_once()

    def test_unsupported_actions_units_and_terminal_states_have_no_stop_effect(self):
        for change in ({"action": "test-update"}, {"action": "protocol-install:wg"}, {"unit": "ssh.service"}, {"state": "succeeded"}, {"state": "unknown"}, {"action": []}, {"state": []}, {"cancel_requested": "true"}):
            with self.subTest(change=change):
                operations.atomic_json(self.current, {**self.action, **change})
                operations.atomic_json(self.root / "operations" / f"{self.identity}.json", {**self.action, **change})
                response = self.cancel()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(operations.read(self.current), {**self.action, **change})
        self.stop.assert_not_called()

    def test_unknown_stop_acknowledgement_keeps_receipt_and_cannot_repeat_stop(self):
        for error in (subprocess.TimeoutExpired("systemctl", 10), OSError("lost acknowledgement")):
            with self.subTest(error=type(error).__name__):
                operations.save(self.root, self.current, self.action)
                self.stop.reset_mock()
                self.stop.side_effect = error
                self.assertEqual(self.cancel().status_code, 503)
                second = self.cancel()
                self.assertEqual(second.status_code, 200, second.text)
                self.assertTrue(second.json()["cancel_requested"])
                self.stop.assert_called_once()

    def test_definitive_stop_rejection_removes_intent_for_a_later_explicit_attempt(self):
        self.stop.return_value = SimpleNamespace(returncode=1)
        response = self.cancel()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("cancel_requested", operations.read(self.current))
        self.assertNotIn("cancel_requested", operations.read(self.root / "operations" / f"{self.identity}.json"))
        self.stop.return_value = SimpleNamespace(returncode=0)
        self.assertEqual(self.cancel().status_code, 200)
        self.assertEqual(self.stop.call_count, 2)

    def test_history_intent_survives_a_partial_pointer_write_without_launch_or_replay(self):
        original = operations.atomic_json

        def interrupted_pointer(path, value):
            if path == self.current and value.get("cancel_requested"):
                raise OSError("pointer unavailable")
            return original(path, value)

        with patch.object(operations, "atomic_json", side_effect=interrupted_pointer):
            self.assertEqual(self.cancel().status_code, 503)
        self.assertNotIn("cancel_requested", operations.read(self.current))
        second = self.cancel()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["cancel_requested"])
        self.stop.assert_not_called()

    def test_concurrent_cancel_requests_issue_one_stop(self):
        def stop(*args, **kwargs):
            time.sleep(0.02)
            return SimpleNamespace(returncode=0)

        self.stop.side_effect = stop
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: api.cancel_application_action(expected_operation_id=self.identity), range(2)))
        self.assertTrue(all(result["cancel_requested"] for result in results))
        self.stop.assert_called_once()

    def test_terminal_and_damaged_history_cannot_redirect_a_stop(self):
        operations.save(self.root, self.current, {**self.action, "state": "cancelled", "cancel_requested": True})
        response = self.cancel()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "cancelled")
        operations.atomic_json(self.root / "operations" / f"{self.identity}.json", {**self.action, "unit": "foreign.service"})
        self.assertEqual(self.cancel().status_code, 409)
        self.stop.assert_not_called()
