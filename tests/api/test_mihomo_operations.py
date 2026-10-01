import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import manager
import application_operation as operations


class MihomoOperationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, "DATA_ROOT", self.root / "mihomo"))
        self.enterContext(patch.object(manager, "ACTION_FILE", self.root / "mihomo" / "action.json"))
        self.enterContext(patch.object(manager, "manifest", return_value={"name": "Fixture"}))
        self.permission = self.enterContext(patch.object(manager, "require_mihomo_management"))
        self.enterContext(patch("service_state.observe_service", return_value={"unit_present": None}))
        self.enterContext(patch.object(manager, "observe_service", return_value={"unit_present": None}))
        self.launch = self.enterContext(patch.object(manager.subprocess, "run", return_value=SimpleNamespace(returncode=0)))

    def test_admission_replay_worker_progress_and_terminal_replay(self):
        identity = "c" * 32
        started = manager.start_module_operation("transport-wg", "install", identity)
        self.assertEqual(started["state"], "queued")
        self.assertEqual(manager.start_module_operation("transport-wg", "install", identity)["id"], identity)
        self.launch.assert_called_once()
        def perform(_):
            manager.write_action("module-install:transport-wg", "Installing", progress=10)
            manager.write_action("module-install:transport-wg", "Verified", state="done", progress=100)
            self.assertEqual(operations.read(self.root / "application-action.json")["state"], "running")
            self.assertEqual(manager.get_action_payload()["id"], identity)
        with patch.object(manager, "install_module", side_effect=perform) as install, patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": identity}):
            self.assertTrue(manager.execute_module_operation("install", "transport-wg", identity, started["unit"]))
            manager.ACTION_FILE.write_text('{"id":"newer","state":"running"}')
            self.assertTrue(manager.execute_module_operation("install", "transport-wg", identity, started["unit"]))
            install.assert_called_once()
        self.assertEqual(manager.get_action_payload()["id"], "newer")
        self.assertEqual(operations.read(self.root / "application-action.json")["state"], "succeeded")

    def test_worker_rechecks_ownership_and_never_runs_wrong_command(self):
        started = manager.start_module_operation("transport-wg", "remove", "d" * 32)
        self.permission.side_effect = manager.HTTPException(409, "not managed")
        with patch.object(manager, "remove_module") as remove, patch.object(manager.logger, "exception"):
            self.assertFalse(manager.execute_module_operation("remove", "transport-wg", started["id"], started["unit"]))
            remove.assert_not_called()
            with self.assertRaises(operations.OperationConflict):
                manager.execute_module_operation("install", "transport-wg", started["id"], started["unit"])

    def test_all_module_commands_require_auth_and_accept_without_running_installer(self):
        client = TestClient(manager.app)
        for suffix, method in (("/install", "POST"), ("/update", "POST"), ("", "DELETE")):
            self.assertEqual(client.request(method, "/api/mihomo/modules/transport-wg" + suffix).status_code, 401)
        manager.app.dependency_overrides[manager.auth_required] = lambda: None
        self.addCleanup(manager.app.dependency_overrides.clear)
        with patch.object(manager, "install_module") as install:
            response = client.post("/api/mihomo/modules/transport-wg/install", headers={"X-Operation-ID": "e" * 32})
        self.assertEqual(response.status_code, 202, response.text)
        install.assert_not_called()

    def test_unicode_authentication_has_no_type_error(self):
        credentials = base64.b64encode("владелец:пароль".encode()).decode()
        with patch.dict(os.environ, {"ADMIN_USER": "владелец", "ADMIN_PASSWORD": "пароль"}):
            manager.auth_required("Basic " + credentials)
            with self.assertRaises(manager.HTTPException) as denied:
                manager.auth_required("Basic " + base64.b64encode("владелец:другой".encode()).decode())
            self.assertEqual(denied.exception.status_code, 401)
        with self.assertRaises(manager.HTTPException) as denied:
            manager.public_profile_subscription("неверный-токен", None, False)
        self.assertEqual(denied.exception.status_code, 404)

    def test_manager_runtime_is_nullable_and_never_implies_healthy(self):
        with patch.object(manager, "state", return_value={}), patch.object(manager, "profiles", return_value=[]), \
             patch.object(manager, "module_is_installed", return_value=False), patch.object(manager, "github_latest_tag", return_value=""), \
             patch.object(manager, "public_endpoint", return_value="fixture"), patch.object(manager, "CORE_BIN", self.root / "missing"):
            for state, active in (("running", True), ("stopped", False), ("unknown", None), ("error", None)):
                observation = {"runtime": {"state": state, "reason": "Observed", "checked_at": "now"}}
                with self.subTest(state=state), patch.object(manager, "observe_service", return_value=observation):
                    result = manager.stable_protocol_status()
                    self.assertIs(result["active"], active)
                    self.assertEqual(result["runtime"]["state"], state)
                    self.assertEqual(result["health"]["state"], "unchecked")
                    self.assertNotEqual(result["diagnostics"]["state"], "healthy")

    def test_state_read_does_not_initialize_settings_during_another_operation(self):
        with patch.object(manager, "load_json", return_value={"modules": {"transport-wg": True}}), \
             patch.object(manager, "ensure_policy_settings", side_effect=AssertionError("GET must not mutate")):
            self.assertTrue(manager.state()["modules"]["transport-wg"])

    def test_profile_admission_keeps_payload_private_and_worker_does_not_replay(self):
        identity = "a" * 32
        payload = {"profile_id": "profile", "settings": {"name": "Changed", "operation_id": identity}}
        started = manager.start_profile_operation("update", payload, identity)
        replay = manager.start_profile_operation("update", payload, identity)
        self.assertEqual(replay["id"], identity)
        self.launch.assert_called_once()
        command = self.launch.call_args.args[0]
        self.assertNotIn("Changed", " ".join(command))
        path, digest = Path(command[-2]), command[-1]
        with patch.object(manager, "update_profile") as update:
            self.assertTrue(manager.execute_profile_operation("update", path, digest, identity, started["unit"]))
            self.assertTrue(manager.execute_profile_operation("update", path, digest, identity, started["unit"]))
            update.assert_called_once()
            self.assertEqual(update.call_args.args[1].model_fields_set, {"name", "operation_id"})
        with self.assertRaises(manager.HTTPException) as conflict:
            manager.start_profile_operation("update", {**payload, "profile_id": "other"}, identity)
        self.assertEqual(conflict.exception.status_code, 409)

    def test_profile_recovery_admission_bypasses_only_recovery_gate(self):
        operations.atomic_json(self.root / "mihomo/profile-recovery/manifest.json", {"schema": 1})
        with self.assertRaises(manager.HTTPException) as conflict:
            manager.start_profile_operation("delete", {"profile_id": "profile"}, "b" * 32)
        self.assertEqual(conflict.exception.status_code, 409)
        started = manager.start_module_operation("profiles", "recover", "f" * 32)
        self.assertEqual(started["state"], "queued")

    def test_module_settings_are_admitted_once_and_applied_only_by_worker(self):
        client = TestClient(manager.app)
        manager.app.dependency_overrides[manager.auth_required] = lambda: None
        self.addCleanup(manager.app.dependency_overrides.clear)
        identity = "f" * 32
        with patch.object(manager, "patch_module_settings") as apply:
            response = client.patch("/api/mihomo/modules/transport-wg/settings",
                                    json={"values": {"port": 51234}}, headers={"X-Operation-ID": identity})
            self.assertEqual(response.status_code, 202, response.text)
            apply.assert_not_called()
            command = self.launch.call_args.args[0]
            self.assertTrue(manager.execute_profile_operation("module-settings", Path(command[-2]), command[-1], identity, response.json()["unit"]))
            self.assertEqual(apply.call_args.args[0], "transport-wg")
            self.assertEqual(apply.call_args.args[1].values, {"port": 51234})
