"""Independent optimistic versions for policies, presets and personal rules."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT


class PolicyEditRevisionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {"SETTINGS_ROOT": self.root, "DNS_SETTINGS_FILE": self.root / "dns.json",
                            "ROUTING_SETTINGS_FILE": self.root / "routing.json",
                            "PRESET_SETTINGS_FILE": self.root / "presets.json",
                            "PERSONAL_RULES_FILE": self.root / "personal.json",
                            "SUBMODULE_ROOT": ROOT / "protocol-images/mihomo/modules"}.items():
            self.enterContext(patch.object(manager, name, value))

    def rule_input(self, **changes):
        return manager.PersonalRuleInput(code="WORK", title="Work", entries=["example.com"], **changes)

    def test_created_rule_is_confirmable_and_identical_replay_does_not_duplicate(self):
        payload = self.rule_input(operation_id="a" * 32)
        saved = manager.create_personal_rule(payload)
        self.assertEqual(saved["last_operation_id"], payload.operation_id)
        self.assertNotIn("_mutation", saved)
        with patch.object(manager, "save_personal_rules") as write:
            self.assertEqual(manager.create_personal_rule(payload), saved)
            write.assert_not_called()
        self.assertEqual(len(manager.personal_rules()), 1)
        self.assertEqual(manager.get_routing_schema()["personal_rules"], [saved])
        with self.assertRaises(manager.HTTPException) as conflict:
            manager.create_personal_rule(self.rule_input(operation_id=payload.operation_id, target="DIRECT"))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(len(manager.personal_rules()), 1)

    def test_update_replay_precedes_stale_revision_check_but_rejects_changed_payload(self):
        saved = manager.create_personal_rule(self.rule_input())
        payload = self.rule_input(operation_id="b" * 32, target="DIRECT", expected_revision=saved["revision"])
        updated = manager.update_personal_rule(saved["id"], payload)
        with patch.object(manager, "save_personal_rules") as write:
            self.assertEqual(manager.update_personal_rule(saved["id"], payload), updated)
            write.assert_not_called()
            with self.assertRaises(manager.HTTPException) as conflict:
                manager.update_personal_rule(saved["id"], payload.model_copy(update={"target": "REJECT"}))
            self.assertEqual(conflict.exception.status_code, 409)
            write.assert_not_called()

    def test_policy_receipts_are_atomic_and_excluded_from_values_and_versions(self):
        for endpoint, getter, path in ((manager.patch_dns_settings, manager.get_dns_settings, manager.DNS_SETTINGS_FILE),
                                      (manager.patch_routing_settings, manager.get_routing_schema, manager.ROUTING_SETTINGS_FILE)):
            with self.subTest(path=path.name):
                original = getter()
                payload = manager.ModuleSettingsPatch(values=original["values"], expected_revision=original["revision"], operation_id="c" * 32)
                saved = endpoint(payload)
                self.assertEqual(saved["revision"], original["revision"])
                self.assertEqual(saved["last_operation_id"], payload.operation_id)
                self.assertNotIn("_mutation", saved["values"])
                self.assertEqual(getter()["last_operation_id"], payload.operation_id)
                self.assertIn("_mutation", json.loads(path.read_text()))
                with patch.object(manager, "atomic_json") as write:
                    self.assertEqual(endpoint(payload), saved)
                    with self.assertRaises(manager.HTTPException) as conflict:
                        endpoint(payload.model_copy(update={"expected_revision": "0" * 64}))
                    self.assertEqual(conflict.exception.status_code, 409)
                    write.assert_not_called()
                # A later writer invalidates the old receipt even when values match.
                endpoint(manager.ModuleSettingsPatch(values=original["values"], operation_id="d" * 32))
                self.assertEqual(getter()["last_operation_id"], "d" * 32)
                with patch.object(manager, "atomic_json", side_effect=OSError("write failed")), self.assertRaises(OSError):
                    endpoint(manager.ModuleSettingsPatch(values=original["values"], operation_id="e" * 32))
                self.assertEqual(getter()["last_operation_id"], "d" * 32)

    def test_presets_keep_list_format_and_confirm_exact_replay(self):
        original = manager.get_routing_schema()
        payload = manager.PresetSettingsPatch(presets=original["presets"], expected_revision=original["presets_revision"], operation_id="f" * 32)
        saved = manager.patch_profile_presets(payload)
        self.assertEqual(saved["revision"], manager.collection_revision("presets", manager.validate_profile_presets(original["presets"])))
        self.assertEqual(manager.get_routing_schema()["presets_last_operation_id"], payload.operation_id)
        self.assertTrue(isinstance(json.loads(manager.PRESET_SETTINGS_FILE.read_text()), list))
        self.assertTrue(all("_mutation" not in preset for preset in saved["presets"]))
        with patch.object(manager, "atomic_json") as write:
            self.assertEqual(manager.patch_profile_presets(payload), saved)
            with self.assertRaises(manager.HTTPException) as conflict:
                manager.patch_profile_presets(payload.model_copy(update={"presets": []}))
            self.assertEqual(conflict.exception.status_code, 409)
            write.assert_not_called()
        with self.assertRaises(ValueError):
            manager.patch_profile_presets(manager.PresetSettingsPatch(presets=[]))

    def test_corrupt_receipt_is_not_public_confirmation(self):
        for receipt in ({"id": "a" * 32}, {"id": "wrong", "hash": "b" * 64}, {"id": int("1" * 32), "hash": "b" * 64}, "wrong"):
            with self.subTest(receipt=receipt):
                manager.atomic_json(manager.DNS_SETTINGS_FILE, {**manager.dns_defaults(), "_mutation": receipt})
                result = manager.get_dns_settings()
                self.assertIsNone(result["last_operation_id"])
                self.assertNotIn("_mutation", result["values"])

    def test_personal_rule_revision_rejects_stale_edit_and_is_not_stored(self):
        rule = manager.create_personal_rule(self.rule_input())
        before = deepcopy(rule)
        updated = manager.update_personal_rule(rule["id"], self.rule_input(target="DIRECT", expected_revision=rule["revision"]))
        self.assertNotEqual(updated["revision"], rule["revision"])
        with patch.object(manager, "save_personal_rules") as save, self.assertRaises(manager.HTTPException) as conflict:
            manager.update_personal_rule(rule["id"], self.rule_input(expected_revision=rule["revision"]))
        self.assertEqual(conflict.exception.status_code, 409); save.assert_not_called()
        stored = json.loads(manager.PERSONAL_RULES_FILE.read_text())
        self.assertNotIn("revision", stored[0]); self.assertNotIn("expected_revision", stored[0])
        self.assertEqual(before, rule)
        self.assertEqual(manager.get_routing_schema()["personal_rules"][0]["revision"], updated["revision"])

    def test_other_rule_or_policy_changes_do_not_conflict_with_an_existing_rule(self):
        rule = manager.create_personal_rule(self.rule_input())
        manager.create_personal_rule(manager.PersonalRuleInput(code="OTHER", title="Other", entries=["other.example"]))
        manager.atomic_json(manager.ROUTING_SETTINGS_FILE, {"strategy": "select"})
        result = manager.update_personal_rule(rule["id"], self.rule_input(target="DIRECT", expected_revision=rule["revision"]))
        self.assertEqual(result["target"], "DIRECT")
        with patch.object(manager, "personal_rules", return_value=[]), self.assertRaises(manager.HTTPException) as missing:
            manager.update_personal_rule(rule["id"], self.rule_input(expected_revision=result["revision"]))
        self.assertEqual(missing.exception.status_code, 404)

    def test_presets_reject_stale_revision_and_accept_current_and_legacy(self):
        original = manager.get_routing_schema()
        presets = deepcopy(original["presets"])
        presets[0]["name"] = "Changed"
        result = manager.patch_profile_presets(manager.PresetSettingsPatch(presets=presets, expected_revision=original["presets_revision"]))
        self.assertNotEqual(result["revision"], original["presets_revision"])
        with patch.object(manager, "atomic_json") as write, self.assertRaises(manager.HTTPException) as conflict:
            manager.patch_profile_presets(manager.PresetSettingsPatch(presets=presets, expected_revision=original["presets_revision"]))
        self.assertEqual(conflict.exception.status_code, 409); write.assert_not_called()
        self.assertEqual(manager.get_routing_schema()["presets_revision"], result["revision"])
        for expected in (result["revision"], None):
            self.assertEqual(manager.patch_profile_presets(manager.PresetSettingsPatch(presets=presets, expected_revision=expected))["presets"], result["presets"])

    def test_dns_and_routing_versions_are_independent_and_success_returns_new_version(self):
        dns = manager.get_dns_settings()
        routing = manager.get_routing_schema()
        dns_values = {**dns["values"], "enhanced_mode": "redir-host" if dns["values"]["enhanced_mode"] == "fake-ip" else "fake-ip"}
        saved_dns = manager.patch_dns_settings(manager.ModuleSettingsPatch(values=dns_values, expected_revision=dns["revision"]))
        self.assertNotEqual(saved_dns["revision"], dns["revision"])
        self.assertEqual(manager.get_routing_schema()["revision"], routing["revision"])
        routing_values = {**routing["values"], "strategy": "select"}
        saved_routing = manager.patch_routing_settings(manager.ModuleSettingsPatch(values=routing_values, expected_revision=routing["revision"]))
        self.assertEqual(manager.get_routing_schema()["revision"], saved_routing["revision"])
        self.assertEqual(manager.get_dns_settings()["revision"], saved_dns["revision"])
        for endpoint, values, revision in ((manager.patch_dns_settings, dns_values, dns["revision"]),
                                            (manager.patch_routing_settings, routing_values, routing["revision"])):
            with patch.object(manager, "atomic_json") as write, self.assertRaises(manager.HTTPException) as conflict:
                endpoint(manager.ModuleSettingsPatch(values=values, expected_revision=revision))
            self.assertEqual(conflict.exception.status_code, 409); write.assert_not_called()
