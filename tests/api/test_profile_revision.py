"""Optimistic edits protect profile/device data without telemetry conflicts."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.api.support import manager


def profile():
    return {"id": "one", "name": "Original", "common_device_id": "common",
            "devices": [{"id": "common", "name": "Common", "scope": "common", "routing": {}}],
            "connections": [{"id": "c", "name": "WG", "component": "transport-wg", "device_id": "common",
                             "settings": {}, "credential": {"private_key": "secret-value"}}]}


class ProfileRevisionTests(unittest.TestCase):
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
