"""Optimistic edits protect profile/device data without telemetry conflicts."""
from copy import deepcopy
from contextlib import nullcontext
import unittest
from unittest.mock import patch

from tests.api.support import manager


def profile():
    return {"id": "one", "name": "Original", "common_device_id": "common",
            "devices": [{"id": "common", "name": "Common", "scope": "common", "routing": {}}],
            "connections": [{"id": "c", "name": "WG", "component": "transport-wg", "device_id": "common",
                             "settings": {}, "credential": {"private_key": "secret-value"}}]}


class ProfileRevisionTests(unittest.TestCase):
    def test_relay_switch_preserves_native_credentials_without_reprovision(self):
        item = profile()
        connection = item['connections'][0]
        connection['device_id'] = 'common'
        definition = {key: deepcopy(value) for key, value in connection.items() if key != 'credential'}
        definition['settings']['relay_host_id'] = 'a'*32
        with (patch.object(manager, 'profiles', return_value=[item]),
              patch.object(manager, 'profile_runtime_transaction', return_value=nullcontext()),
              patch.object(manager, 'preflight_client_export_update'),
              patch.object(manager, 'validate_connection_inputs', return_value=[definition]),
              patch.object(manager, 'prepare_profile_relays') as prepare,
              patch.object(manager, 'provision') as provision,
              patch.object(manager, 'deprovision') as deprovision,
              patch.object(manager, 'reconcile_profile_encryption'),
              patch.object(manager, 'save_profiles'), patch.object(manager, 'write_action'),
              patch.object(manager, 'verified_profile_clients', return_value=[])):
            manager.update_profile('one', manager.ProfileUpdate(connections=[definition]))
        self.assertEqual(item['connections'][0]['credential'], {'private_key': 'secret-value'})
        self.assertEqual(item['connections'][0]['settings']['relay_host_id'], 'a'*32)
        provision.assert_not_called()
        deprovision.assert_not_called()
        prepare.assert_called_once()

    def test_public_receipt_is_invalidated_by_another_edit_but_not_telemetry(self):
        item = profile()
        item.update(last_operation_id="saved-op", last_operation_revision=manager.profile_revision(item))
        with patch.object(manager, "verified_profile_clients", return_value=[]):
            public = manager.profile_response(item)
            self.assertEqual(public["last_operation_id"], "saved-op")
            self.assertNotIn("last_operation_revision", public)
            item["devices"][0]["last_seen_at"] = "later"
            self.assertEqual(manager.profile_response(item)["last_operation_id"], "saved-op")
            item["devices"][0]["personal_rule_ids"] = ["personal-aaaaaaaaaaaa"]
            self.assertEqual(manager.profile_response(item)["last_operation_id"], "")

    def test_successful_update_records_revision_and_legacy_update_clears_receipt(self):
        item = profile()
        with (patch.object(manager, "profiles", return_value=[item]),
              patch.object(manager, "profile_runtime_transaction", return_value=nullcontext()),
              patch.object(manager, "preflight_client_export_update"),
              patch.object(manager, "reconcile_profile_encryption"),
              patch.object(manager, "save_profiles"), patch.object(manager, "write_action"),
              patch.object(manager, "verified_profile_clients", return_value=[])):
            public = manager.update_profile("one", manager.ProfileUpdate(name="Saved", operation_id="saved-op"))
            self.assertEqual(public["last_operation_id"], "saved-op")
            self.assertEqual(item["last_operation_revision"], manager.profile_revision(item))
            public = manager.update_profile("one", manager.ProfileUpdate(name="Legacy"))
            self.assertEqual(public["last_operation_id"], "")
            self.assertEqual(item["last_operation_revision"], "")

    def test_creation_records_immutable_revision_without_hashing_credentials(self):
        item = profile()
        payload = manager.ProfileCreate(name="Created", channels=["transport-wg"], operation_id="create-op")
        with (patch.object(manager, "profiles", return_value=[]),
              patch.object(manager, "validate_channels", return_value=["transport-wg"]),
              patch.object(manager, "legacy_connection_inputs", return_value=item["connections"]),
              patch.object(manager, "validate_profile_devices"),
              patch.object(manager, "validate_personal_rule_ids"), patch.object(manager, "validate_client_capabilities"),
              patch.object(manager, "profile_runtime_transaction", return_value=nullcontext()),
              patch.object(manager, "provision_connections", return_value=item["connections"]),
              patch.object(manager, "save_profiles"), patch.object(manager, "write_action"),
              patch.object(manager, "verified_profile_clients", return_value=[])):
            public = manager.create_profile(payload)
        self.assertEqual(public["create_revision"], public["revision"])
        self.assertEqual(public["create_operation_id"], "create-op")

    def test_revision_is_stable_without_mutating_source_or_hashing_credentials(self):
        item = profile()
        before = deepcopy(item)
        revision = manager.profile_revision(item)
        self.assertEqual(item, before)
        item["connections"][0]["credential"]["private_key"] = "rotated-secret"
        item["devices"][0].update(last_seen_at="later", user_agent="refreshed")
        item.update(updated_at="later", last_operation_id="different", subscriptions={"device": "private-url"},
                    retiring_connections=[{"yaml_served_at": "now"}])
        self.assertEqual(manager.profile_revision(item), revision)

    def test_editable_changes_and_new_devices_invalidate_revision(self):
        mutations = [lambda item: item.update(name="Other"),
                     lambda item: item["devices"].append({"id": "hwid-new", "name": "New"}),
                     lambda item: item["devices"][0].update(personal_rule_ids=["personal-aaaaaaaaaaaa"]),
                     lambda item: item["connections"][0]["settings"].update(mtu=1400),
                     lambda item: item["devices"][0]["routing"].update(strategy="fallback")]
        for mutation in mutations:
            item = profile()
            revision = manager.profile_revision(item)
            mutation(item)
            with self.subTest(mutation=mutation):
                self.assertNotEqual(manager.profile_revision(item), revision)

    def test_admission_rejects_stale_revision_without_starting_worker(self):
        item = profile()
        revision = manager.profile_revision(item)
        item["name"] = "Changed elsewhere"
        with patch.object(manager, "profiles", return_value=[item]), patch.object(manager, "start_profile_operation") as start:
            with self.assertRaises(manager.HTTPException) as conflict:
                manager.submit_profile_update("one", manager.ProfileUpdate(name="Draft", expected_revision=revision), None)
            self.assertEqual(conflict.exception.status_code, 409)
            start.assert_not_called()

    def test_worker_rechecks_after_admission_before_snapshot_and_side_effects(self):
        item = profile()
        revision = manager.profile_revision(item)
        item["devices"].append({"id": "hwid-new", "name": "New device"})
        with (patch.object(manager, "profiles", return_value=[item]),
              patch.object(manager, "profile_runtime_transaction") as snapshot,
              patch.object(manager, "save_profiles") as save,
              patch.object(manager, "provision") as provision,
              self.assertRaises(manager.HTTPException) as conflict):
            manager.update_profile("one", manager.ProfileUpdate(name="Draft", expected_revision=revision))
        self.assertEqual(conflict.exception.status_code, 409)
        snapshot.assert_not_called()
        save.assert_not_called()
        provision.assert_not_called()

    def test_matching_revision_is_forwarded_and_completed_repeat_is_not_reapplied(self):
        item = profile()
        payload = manager.ProfileUpdate(name="Draft", operation_id="saved-op", expected_revision=manager.profile_revision(item))
        with patch.object(manager, "profiles", return_value=[item]), patch.object(manager, "start_profile_operation") as start:
            manager.submit_profile_update("one", payload, None)
            self.assertEqual(start.call_args.args[1]["settings"]["expected_revision"], payload.expected_revision)
        item.update(name="Draft", last_operation_id="saved-op")
        with (patch.object(manager, "profiles", return_value=[item]), patch.object(manager, "profile_runtime_transaction") as snapshot,
              patch.object(manager, "profile_response", side_effect=lambda saved: saved)):
            self.assertEqual(manager.update_profile("one", payload)["name"], "Draft")
        snapshot.assert_not_called()

    def test_deleted_profile_is_rejected_on_admission(self):
        with patch.object(manager, "profiles", return_value=[]), self.assertRaises(manager.HTTPException) as missing:
            manager.submit_profile_update("missing", manager.ProfileUpdate(expected_revision="a" * 64), None)
        self.assertEqual(missing.exception.status_code, 404)
