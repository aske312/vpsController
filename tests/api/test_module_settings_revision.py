"""Optimistic component settings edits, including delayed durable workers."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from fastapi.testclient import TestClient
from tests.api.support import manager, ROOT
from tests.api import test_mihomo_operations as operation_tests

operations = operation_tests.operations


class ModuleSettingsReceiptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, "SETTINGS_ROOT", self.root))
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=False))
        self.enterContext(patch.object(manager, "systemctl_active", return_value=False))
        self.enterContext(patch.object(manager, "module_version_info", return_value={}))
        self.path = self.root / "transport-shadowsocks.json"

    def test_worker_receipt_matches_current_values_without_changing_revision_or_exports(self):
        values = manager.module_settings("transport-shadowsocks")
        revision = manager.module_settings_revision("transport-shadowsocks")
        for identity in ("a" * 32, "b" * 32):
            with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": identity}):
                result = manager.apply_module_settings("transport-shadowsocks", values)
            self.assertEqual(result["last_operation_id"], identity)
            self.assertEqual(result["revision"], revision)
            self.assertNotIn("_mutation", result["values"])
            self.assertEqual(manager.module_settings("transport-shadowsocks"), values)
            payload = manager.module_payload("transport-shadowsocks")
            self.assertEqual(payload["last_settings_operation_id"], identity)
            self.assertEqual(payload["settings_revision"], revision)
            self.assertNotIn("_mutation", payload["settings_values"])

    def test_mismatched_values_and_wrong_module_digest_cannot_prove_a_saved_baseline(self):
        values = manager.module_settings("transport-shadowsocks")
        with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": "c" * 32}):
            manager.apply_module_settings("transport-shadowsocks", values)
        self.assertIsNone(manager.module_settings_operation_id("transport-shadowsocks", {**values, "port_start": 32000}))
        stored = json.loads(self.path.read_text())
        stored["port_start"] = 32000
        manager.atomic_json(self.path, stored)
        self.assertIsNone(manager.get_module_settings("transport-shadowsocks")["last_operation_id"])
        stored["_mutation"]["hash"] = manager.collection_revision("module-settings:transport-wg", {"port_start": 32000})
        manager.atomic_json(self.path, stored)
        self.assertIsNone(manager.get_module_settings("transport-shadowsocks")["last_operation_id"])

    def test_legacy_write_clears_previous_receipt(self):
        values = manager.module_settings("transport-shadowsocks")
        with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": "d" * 32}):
            manager.apply_module_settings("transport-shadowsocks", values)
        with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": ""}):
            result = manager.apply_module_settings("transport-shadowsocks", values)
        self.assertIsNone(result["last_operation_id"])
        self.assertNotIn("_mutation", json.loads(self.path.read_text()))


class ModuleSettingsRevisionTests(unittest.TestCase):
    def setUp(self):
        self.values = {"mtu": 1280, "private_key": "secret"}
        self.enterContext(patch.object(manager, "manifest", return_value={"settings": [{"key": "mtu", "default": 1280}]}))
        self.enterContext(patch.object(manager, "module_settings", side_effect=lambda _: dict(self.values)))

    def test_revision_covers_editable_fields_and_module_identity_without_secret_fields(self):
        revision = manager.module_settings_revision("transport-wg")
        self.assertEqual(len(revision), 64)
        self.values["private_key"] = "rotated"
        self.values["last_seen_at"] = "now"
        self.assertEqual(manager.module_settings_revision("transport-wg"), revision)
        self.assertNotEqual(manager.module_settings_revision("transport-awg"), revision)
        self.values["mtu"] = 1400
        self.assertNotEqual(manager.module_settings_revision("transport-wg"), revision)

    def test_invalid_revision_is_rejected_by_schema(self):
        for revision in ("", "*", "A" * 64, "a" * 63):
            with self.subTest(revision=revision), self.assertRaises(ValidationError):
                manager.ModuleSettingsPatch(expected_revision=revision)

    def test_stale_worker_rejects_before_validation_snapshot_or_runtime(self):
        revision = manager.module_settings_revision("transport-wg")
        self.values["mtu"] = 1400
        with (patch.object(manager, "validate_settings") as validate,
              patch.object(manager, "profile_runtime_transaction") as snapshot,
              patch.object(manager, "apply_module_settings") as apply,
              self.assertRaises(manager.HTTPException) as conflict):
            manager.patch_module_settings("transport-wg", manager.ModuleSettingsPatch(values={"mtu": 1500}, expected_revision=revision))
        self.assertEqual(conflict.exception.status_code, 409)
        validate.assert_not_called(); snapshot.assert_not_called(); apply.assert_not_called()

    def test_matching_and_legacy_revisions_can_apply(self):
        for expected in (manager.module_settings_revision("transport-wg"), None):
            with (patch.object(manager, "validate_settings", return_value={"mtu": 1400}),
                  patch.object(manager, "module_is_ready", return_value=False),
                  patch.object(manager, "MODULE_LISTENERS", {}),
                  patch.object(manager, "profile_runtime_transaction") as snapshot,
                  patch.object(manager, "apply_module_settings", return_value={"saved": True}) as apply):
                self.assertEqual(manager.patch_module_settings("transport-wg", manager.ModuleSettingsPatch(values={"mtu": 1400}, expected_revision=expected)), {"saved": True})
                snapshot.assert_called_once(); apply.assert_called_once()

    def test_public_payload_and_get_return_the_same_revision(self):
        with (patch.object(manager, "module_is_installed", return_value=False),
              patch.object(manager, "module_version_info", return_value={}),
              patch.object(manager, "systemctl_active", return_value=False)):
            payload = manager.module_payload("transport-wg")
            settings = manager.get_module_settings("transport-wg")
        self.assertEqual(payload["settings_revision"], settings["revision"])
        self.assertNotIn("secret", settings["revision"])

    def test_policy_endpoints_reject_stale_revision_before_write(self):
        for module_id, getter, endpoint in ((manager.DNS_MODULE_ID, "dns_settings", manager.patch_dns_settings),
                                             (manager.ROUTING_MODULE_ID, "routing_settings", manager.patch_routing_settings)):
            with self.subTest(module_id=module_id), patch.object(manager, getter, return_value={"mtu": 1280}), patch.object(manager, "atomic_json") as write:
                with self.assertRaises(manager.HTTPException) as conflict:
                    endpoint(manager.ModuleSettingsPatch(values={}, expected_revision="a" * 64))
                self.assertEqual(conflict.exception.status_code, 409)
                write.assert_not_called()


class ModuleSettingsRevisionOperationTests(unittest.TestCase):
    setUp = operation_tests.MihomoOperationTests.setUp

    def prepare(self):
        self.values = {"mtu": 1280}
        self.enterContext(patch.object(manager, "manifest", return_value={"settings": [{"key": "mtu", "default": 1280}]}))
        self.enterContext(patch.object(manager, "module_settings", side_effect=lambda _: dict(self.values)))
        revision = manager.module_settings_revision("transport-wg")
        return {"module_id": "transport-wg", "settings": {"values": {"mtu": 1500}, "expected_revision": revision}}

    def test_stale_admission_does_not_persist_or_launch(self):
        payload = self.prepare()
        self.values["mtu"] = 1400
        with patch.object(operations, "persist_payload") as persist, self.assertRaises(manager.HTTPException) as conflict:
            manager.start_profile_operation("module-settings", payload, "a" * 32)
        self.assertEqual(conflict.exception.status_code, 409)
        persist.assert_not_called(); self.launch.assert_not_called()

    def test_http_admission_rejects_stale_revision_without_launching_worker(self):
        payload = self.prepare()
        self.values["mtu"] = 1400
        manager.app.dependency_overrides[manager.auth_required] = lambda: None
        self.addCleanup(manager.app.dependency_overrides.clear)
        response = TestClient(manager.app).patch("/api/mihomo/modules/transport-wg/settings",
            json=payload["settings"], headers={"X-Operation-ID": "c" * 32})
        self.assertEqual(response.status_code, 409, response.text)
        self.launch.assert_not_called()

    def test_successful_operation_replay_keeps_identity_without_applying_again(self):
        payload = self.prepare()
        identity = "d" * 32
        started = manager.start_profile_operation("module-settings", payload, identity)
        command = self.launch.call_args.args[0]
        def save(*args):
            self.values["mtu"] = 1500
            return {"values": dict(self.values)}
        with (patch.object(manager, "validate_settings", return_value={"mtu": 1500}),
              patch.object(manager, "MODULE_LISTENERS", {}), patch.object(manager, "module_is_ready", return_value=False),
              patch.object(manager, "profile_runtime_transaction") as snapshot,
              patch.object(manager, "apply_module_settings", side_effect=save) as apply):
            self.assertTrue(manager.execute_profile_operation("module-settings", Path(command[-2]), command[-1], identity, started["unit"]))
            self.assertEqual(manager.start_profile_operation("module-settings", payload, identity)["id"], identity)
            self.assertEqual(operations.read(self.root / "operations" / f"{identity}.json")["state"], "succeeded")
            self.assertTrue(manager.execute_profile_operation("module-settings", Path(command[-2]), command[-1], identity, started["unit"]))
            apply.assert_called_once(); snapshot.assert_called_once()
        self.launch.assert_called_once()

    def test_worker_rechecks_after_admission_and_terminal_repeat_is_not_reapplied(self):
        payload = self.prepare()
        identity = "b" * 32
        started = manager.start_profile_operation("module-settings", payload, identity)
        command = self.launch.call_args.args[0]
        self.values["mtu"] = 1400
        # A repeat of the admitted request uses the durable operation identity.
        self.assertEqual(manager.start_profile_operation("module-settings", payload, identity)["id"], identity)
        self.launch.assert_called_once()
        with patch.object(manager, "apply_module_settings") as apply, patch.object(manager, "profile_runtime_transaction") as snapshot, patch.object(manager.logger, "exception"):
            self.assertFalse(manager.execute_profile_operation("module-settings", Path(command[-2]), command[-1], identity, started["unit"]))
            self.assertFalse(manager.execute_profile_operation("module-settings", Path(command[-2]), command[-1], identity, started["unit"]))
            apply.assert_not_called(); snapshot.assert_not_called()
        self.assertEqual(operations.read(self.root / "operations" / f"{identity}.json")["state"], "failed")
