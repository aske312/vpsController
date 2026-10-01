import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import api
import service_worker
import application_operation as operations


class ServiceOperationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, "DATA_DIR", self.root))
        self.enterContext(patch.object(api, "ACTION_FILE", self.root / "application-action.json"))
        self.enterContext(patch.object(api, "INSTALL_DIR", self.root / "app"))
        self.enterContext(patch.object(api, "observe_service", return_value={"unit_present": None}))
        self.launch = self.enterContext(patch.object(api.subprocess, "run", return_value=SimpleNamespace(returncode=0)))

    def test_network_payload_is_private_and_replay_does_not_repeat_mutation(self):
        identity = "c" * 32
        payload = {"cdn_domain": "cdn.example", "cdn_domains": ["cdn.example"]}
        started = api.start_network_mutation("network-settings", payload, identity)
        command = self.launch.call_args.args[0]
        self.assertNotIn("cdn.example", " ".join(command))
        self.assertEqual(api.start_network_mutation("network-settings", payload, identity)["id"], identity)
        self.launch.assert_called_once()
        with self.assertRaises(HTTPException) as failure:
            api.start_network_mutation("network-settings", {"cdn_domain": "changed.example"}, identity)
        self.assertEqual(failure.exception.status_code, 409)
        apply = self.enterContext(patch.object(api, "update_network_endpoints"))
        path, digest = Path(command[-2]), command[-1]
        perform = lambda: service_worker.apply_network_request(api, "network-settings", path, digest, identity)
        self.assertTrue(service_worker.execute_operation(api, "network-settings", perform, identity, started["unit"]))
        self.assertTrue(service_worker.execute_operation(api, "network-settings", perform, identity, started["unit"]))
        apply.assert_called_once()

    def test_modified_operation_payload_is_rejected_before_any_change(self):
        identity = "d" * 32
        api.start_network_mutation("network-delete", {"kind": "cdn", "domain": "cdn.example"}, identity)
        command = self.launch.call_args.args[0]
        path, digest = Path(command[-2]), command[-1]
        path.write_text('{"kind":"cdn","domain":"another.example"}')
        with patch.object(api, "delete_network_endpoint") as apply, self.assertRaises(ValueError):
            service_worker.apply_network_request(api, "network-delete", path, digest, identity)
        apply.assert_not_called()

    def test_network_revision_conflict_before_admission_and_before_execution(self):
        with patch.object(api, "NETWORK_ENDPOINTS_FILE", self.root / "network.json"):
            revision = api.network_settings_revision("network-settings")
            payload = {"cdn_domain": "cdn.example", "expected_revision": revision}
            started = api.start_network_mutation("network-settings", payload, "1" * 32)
            command = self.launch.call_args.args[0]
            api.NETWORK_ENDPOINTS_FILE.write_text('{"cdn_domain":"new.example"}')
            replay = api.start_network_mutation("network-settings", payload, "1" * 32)
            self.assertEqual(replay["id"], started["id"])
            self.launch.assert_called_once()
            with patch.object(api, "update_network_endpoints") as apply, self.assertRaises(HTTPException) as failure:
                service_worker.apply_network_request(api, "network-settings", Path(command[-2]), command[-1], started["id"])
            self.assertEqual(failure.exception.status_code, 409)
            apply.assert_not_called()
            with self.assertRaises(HTTPException) as failure:
                api.start_network_mutation("network-settings", payload, "2" * 32)
            self.assertEqual(failure.exception.status_code, 409)
            self.assertFalse((self.root / "operation-inputs" / ("2" * 32 + ".json")).exists())

    def test_ssh_commands_require_auth_and_return_durable_operations(self):
        client = TestClient(api.app)
        cases = [("key", {"public_key": "ssh-ed25519 " + "A" * 80}, "ssh-key-add"),
                 ("key/reset", None, "ssh-key-reset"),
                 ("key/delete", {"fingerprint": "SHA256:" + "a" * 43}, "ssh-key-delete"),
                 *[(name, None, "ssh-access-" + name) for name in ("begin", "confirm", "rollback", "disable")]]
        for index, (suffix, payload, action) in enumerate(cases):
            with self.subTest(action=action):
                path = "/api/security/ssh-access/" + suffix
                self.assertEqual(client.post(path, json=payload).status_code, 401)
                api.app.dependency_overrides[api.require_token] = lambda: None
                try:
                    folder = self.root / str(index)
                    with patch.object(api, "DATA_DIR", folder), patch.object(api, "ACTION_FILE", folder / "action.json"):
                        response = client.post(path, json=payload, headers={"X-Operation-ID": f"{index + 1:032x}"})
                        self.assertEqual(response.status_code, 202, response.text)
                        self.assertEqual(response.json()["action"], action)
                        self.assertEqual(response.json()["state"], "queued")
                finally:
                    api.app.dependency_overrides.clear()

    def test_logging_replay_is_durable_and_does_not_wait_for_worker(self):
        payload = api.LoggingSettings(persistent=True, retention_days=30)
        started = api.update_logging(payload, None, "a" * 32)
        replay = api.update_logging(payload, None, "a" * 32)
        self.assertEqual(replay["id"], started["id"])
        self.launch.assert_called_once()
        command = self.launch.call_args.args[0]
        self.assertNotIn("--wait", command)
        self.assertEqual(command[-3:], ["logging-config", "enable", "30"])
        self.assertTrue((self.root / "operations" / (started["id"] + ".json")).exists())
        with self.assertRaises(HTTPException) as conflict:
            api.update_logging(api.LoggingSettings(persistent=False, retention_days=30), None, "a" * 32)
        self.assertEqual(conflict.exception.status_code, 409)
        self.launch.assert_called_once()

    def test_lost_launch_response_is_unknown_and_clear_cannot_overlap(self):
        self.launch.side_effect = subprocess.TimeoutExpired("systemd-run", 10)
        started = api.clear_logs(None, "b" * 32)
        self.assertEqual(started["state"], "unknown")
        replay = api.clear_logs(None, "b" * 32)
        self.assertEqual(replay["id"], started["id"])
        with self.assertRaises(HTTPException) as conflict:
            api.clear_logs(None, "c" * 32)
        self.assertEqual(conflict.exception.status_code, 409)
        self.launch.assert_called_once()

    def test_service_worker_rechecks_permissions_and_reports_observed_completion(self):
        definition = {"unit": "fixture.service", "controls": ["restart"], "component_id": "wg"}
        with patch.object(api, "managed_services", return_value={"fixture": definition}), \
             patch.object(api, "require_owned_components") as owned, \
             patch.object(api, "run", return_value="") as run, \
             patch.object(api, "service_details", return_value={"runtime": {"state": "running"}}):
            started = api.manage_service("fixture", api.ServiceAction(action="restart"), None, "d" * 32)
            run.assert_not_called()
            self.assertTrue(service_worker.execute(api, "fixture", "restart", started["id"], started["unit"]))
            self.assertEqual(owned.call_count, 2)
            run.assert_called_once_with("systemctl", "restart", "fixture.service", timeout=30, check=True)
            self.assertTrue(service_worker.execute(api, "fixture", "restart", started["id"], started["unit"]))
            run.assert_called_once()
            self.assertEqual(operations.read(api.ACTION_FILE)["state"], "succeeded")

    def test_automation_admission_does_not_publish_unapplied_settings(self):
        with patch.object(api, "AUTOMATION_FILE", self.root / "automation.json"):
            api.AUTOMATION_FILE.write_text("original", encoding="utf-8")
            settings = api.AutomationSettings.model_validate(api.default_automation())
            started = api.update_automation(settings, None, "f" * 32)
            self.assertEqual(started["action"], "automation-config")
            self.assertEqual(api.AUTOMATION_FILE.read_text(), "original")
            api.update_automation(settings, None, "f" * 32)
            self.launch.assert_called_once()

    def test_service_ownership_revoked_before_worker_prevents_mutation(self):
        definition = {"unit": "fixture.service", "controls": ["stop"], "component_id": "wg"}
        with patch.object(api, "managed_services", return_value={"fixture": definition}), \
             patch.object(api, "require_owned_components", side_effect=[None, HTTPException(409, "not managed")]), \
             patch.object(api, "run") as run, patch.object(api.logger, "exception"):
            started = api.manage_service("fixture", api.ServiceAction(action="stop"), None, "e" * 32)
            self.assertFalse(service_worker.execute(api, "fixture", "stop", started["id"], started["unit"]))
            run.assert_not_called()
            self.assertEqual(operations.read(api.ACTION_FILE)["state"], "failed")
            replay = api.manage_service("fixture", api.ServiceAction(action="stop"), None, "e" * 32)
            self.assertEqual(replay["id"], started["id"])
            self.launch.assert_called_once()
