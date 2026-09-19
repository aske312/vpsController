import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.api.support import api
import application_operation as operations


class ApplicationOperationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.current = self.root / "application-action.json"
        self.command = ["vps-control", "protocol-install", "wg"]
        self.launch = Mock(return_value=SimpleNamespace(returncode=0))

    def start(self, identity="1" * 32, command=None, launch=None):
        return operations.start(self.root, self.current, "protocol-install:wg", command or self.command,
                                launch or self.launch, request_id=identity)

    def test_persist_before_launch_and_same_request_never_launches_twice(self):
        def launch(*args, **kwargs):
            self.assertEqual(json.loads(self.current.read_text())["state"], "queued")
            return SimpleNamespace(returncode=0)
        self.launch.side_effect = launch
        started = self.start()
        self.assertEqual(self.start(), started)
        self.launch.assert_called_once()
        with self.assertRaises(operations.OperationConflict):
            self.start(command=["vps-control", "protocol-remove", "wg"])
        operations.write_status(self.root, self.current, started["action"], "succeeded", 100, "Done", started["started_at"], started["id"])
        self.assertEqual(self.start()["state"], "succeeded")
        self.launch.assert_called_once()

    def test_concurrent_requests_only_admit_one_worker(self):
        def attempt(number):
            try:
                return self.start(f"{number:032x}")["state"]
            except operations.OperationConflict:
                return "blocked"
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(attempt, range(1, 9)))
        self.assertEqual(states.count("queued"), 1)
        self.assertEqual(states.count("blocked"), 7)
        self.launch.assert_called_once()

    def test_timeout_is_unknown_and_worker_can_complete_same_identity(self):
        self.launch.side_effect = subprocess.TimeoutExpired("systemd-run", 10)
        started = self.start()
        self.assertEqual(started["state"], "unknown")
        self.assertEqual(self.start()["state"], "unknown")
        operations.write_status(self.root, self.current, started["action"], "succeeded", 100, "Done", started["started_at"], started["id"], started["unit"])
        self.assertEqual(self.start()["state"], "succeeded")
        self.launch.assert_called_once()

    def test_fast_worker_result_survives_a_late_launch_acknowledgement(self):
        def launch(*args, **kwargs):
            started = operations.read(self.current)
            operations.write_status(self.root, self.current, started["action"], "succeeded", 100, "Done", started["started_at"], started["id"], started["unit"])
            raise subprocess.TimeoutExpired("systemd-run", 10)
        result = self.start(launch=launch)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(operations.read(self.current)["state"], "succeeded")

    def test_success_marker_waits_for_process_exit_and_mutations_share_lock(self):
        started = self.start()
        finished = operations.write_status(self.root, self.current, started["action"], "succeeded", 100, "Done", started["started_at"], started["id"], started["unit"])
        running = lambda _: {"unit_present": True, "active": True, "state": "active"}
        self.assertEqual(operations.reconcile(self.root, self.current, finished, running)["state"], "running")
        self.assertEqual(operations.read(self.current)["state"], "succeeded")
        with operations.short_mutation(self.root):
            with self.assertRaises(operations.OperationConflict):
                self.start("2" * 32)
        self.launch.assert_called_once()

    def test_failed_storage_or_corrupt_previous_record_never_launches(self):
        with patch.object(operations, "atomic_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.start()
        self.current.write_text("corrupt")
        with self.assertRaises(operations.OperationConflict):
            self.start()
        self.launch.assert_not_called()

    def test_missing_observation_is_unknown_and_collected_worker_is_interrupted(self):
        started = self.start()
        started["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        operations.save(self.root, self.current, started)
        unknown = lambda _: {"unit_present": None}
        missing = lambda _: {"unit_present": False, "active": False, "state": "inactive"}
        self.assertEqual(operations.reconcile(self.root, self.current, started, unknown)["state"], "unknown")
        self.assertEqual(operations.read(self.current)["state"], "queued")
        result = operations.reconcile(self.root, self.current, started, missing)
        self.assertEqual((result["state"], result["result"]), ("failed", "interrupted"))
        self.assertEqual(self.start("2" * 32)["state"], "queued")

    def test_history_budget_protects_active_and_current_records(self):
        first = self.start()
        operations.write_status(self.root, self.current, first["action"], "succeeded", 100, "Done", first["started_at"], first["id"])
        second = self.start("2" * 32)
        operations.prune(self.root, second["id"], byte_budget=1)
        self.assertEqual([value["id"] for value in operations.history(self.root)], [second["id"]])

    def test_success_from_previous_boot_does_not_observe_reused_process_identity(self):
        value = {"id": "a" * 32, "state": "succeeded", "boot_id": "previous", "worker_pid": 25, "worker_start": "100"}
        observe = Mock(side_effect=AssertionError("Old process must not be queried"))
        with patch.object(operations, "boot_id", return_value="current"), patch.object(operations, "process_start", observe):
            self.assertEqual(operations.reconcile(self.root, self.current, value, observe), value)

    def test_cancel_does_not_interrupt_commands_without_supported_rollback(self):
        for action in ("protocol-install:wg", "cdn-security", "ech:example.com", "access-mode"):
            with self.subTest(action=action):
                operations.atomic_json(self.current, {"unit": "vps-control-action-123.service", "state": "running", "action": action})
                with patch.object(api, "ACTION_FILE", self.current), patch.object(api.subprocess, "run") as stop:
                    with self.assertRaises(api.HTTPException) as raised:
                        api.cancel_application_action()
                    self.assertEqual(raised.exception.status_code, 409)
                    stop.assert_not_called()

    def test_api_uses_identity_for_reconciliation_and_requires_auth(self):
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        self.assertEqual(client.get("/api/application/operations").status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        with patch.object(api, "DATA_DIR", self.root), patch.object(api, "ACTION_FILE", self.current), \
             patch.object(api.subprocess, "run", self.launch), patch.object(api, "observe_service", return_value={"unit_present": None}):
            headers = {"X-Operation-ID": "1" * 32}
            first = client.post("/api/application/action", headers=headers, json={"action": "optimize"})
            second = client.post("/api/application/action", headers=headers, json={"action": "optimize"})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["id"], second.json()["id"])
            self.assertEqual(client.get("/api/application/operations/" + "1" * 32).json()["state"], "unknown")
            self.launch.assert_called_once()

    def test_panel_modes_are_durable_nonblocking_and_reject_identity_reuse(self):
        from fastapi.testclient import TestClient
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        client = TestClient(api.app)
        with patch.object(api, "DATA_DIR", self.root), patch.object(api, "ACTION_FILE", self.current), \
             patch.object(api.subprocess, "run", self.launch), patch.object(api, "observe_service", return_value={"unit_present": None}), \
             patch.object(api, "VLESS_ENV", self.root / "missing.env"), patch.object(api, "MIHOMO_VLESS_CDN_ROUTES", self.root / "missing-routes"), \
             patch.object(api, "configured_panel_channels", return_value=["WireGuard"]), \
             patch.object(api, "internal_panel_url", return_value="http://panel.invalid"), patch.object(api, "external_panel_url", return_value="https://panel.invalid"):
            for endpoint, first, conflicting in [("panel-access", {"mode": "external"}, {"mode": "vpn"}), ("service-mode", {"active": True}, {"active": False})]:
                with self.subTest(endpoint=endpoint):
                    identity = "a" * 32 if endpoint == "panel-access" else "b" * 32
                    response = client.put(f"/api/services/{endpoint}", json=first, headers={"X-Operation-ID": identity})
                    self.assertIn(response.status_code, (200, 202), response.text)
                    self.assertEqual(response.json()["state"], "queued")
                    self.assertEqual(operations.read(self.current)["id"], identity)
                    self.assertNotIn("--wait", self.launch.call_args.args[0])
                    self.assertEqual(client.put(f"/api/services/{endpoint}", json=conflicting, headers={"X-Operation-ID": identity}).status_code, 409)
                    marker = operations.read(self.current)
                    operations.write_status(self.root, self.current, marker["action"], "failed", 100, "Fixture end", marker["started_at"], identity)
            self.assertEqual(self.launch.call_count, 2)
