import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT


class RealityAllocationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", return_value=True))

    def test_automatic_targets_are_unique_per_device_and_stable_after_save(self):
        inputs = [manager.ProfileConnectionInput(id=f"c{index}", device_id=device, component="transport-reality", settings={"route_mode": "direct"}) for device in ("common", "phone") for index in range(5)]
        for index, value in enumerate(inputs):
            value.id = f"c{index}"
        result = manager.validate_connection_inputs(inputs)
        for device in ("common", "phone"):
            self.assertEqual(len({row["settings"]["target"] for row in result if row["device_id"] == device}), 5)
        again = manager.validate_connection_inputs([manager.ProfileConnectionInput(**row) for row in result])
        self.assertEqual(result, again)
        for row in result:
            settings = row["settings"]
            stream = manager.vless_stream(settings, "private", "short")
            self.assertEqual(stream["realitySettings"]["serverNames"], [settings["target"].rsplit(":", 1)[0]])

    def test_manual_target_is_reserved_and_manual_duplicate_remains_allowed(self):
        target = manager.MIHOMO_REALITY_SNI_POOL[0] + ":443"
        values = [manager.ProfileConnectionInput(id=str(i), component="transport-reality", settings={"route_mode": "direct", **({"target": target} if i else {})}) for i in range(3)]
        result = manager.validate_connection_inputs(values)
        self.assertNotEqual(result[0]["settings"]["target"], target)
        self.assertEqual(result[1]["settings"]["target"], target)
        self.assertEqual(result[2]["settings"]["target"], target)

    def test_windows_location_requires_registered_windows_device(self):
        for os, hwid in (("windows", None), ("ios", "hash"), ("android", "hash"), ("macos", "hash"), ("windows", "hash")):
            with self.subTest(os=os, hwid=hwid):
                profile = {"common_device_id": "common", "devices": [{"id": "common", "routing": {}}, {"id": "device", "os": os, "hwid_hash": hwid, "routing": {"windows_geolocation": True}}], "connections": []}
                enabled = manager.device_routing(profile, "device")["windows_geolocation"]
                self.assertEqual(enabled, os == "windows" and hwid is not None)
                if not enabled:
                    with self.assertRaises(manager.HTTPException):
                        manager.validate_client_capabilities(profile)
        profile = {"common_device_id": "common", "devices": [{"id": "common", "os": "windows", "hwid_hash": "hash", "routing": {"windows_geolocation": True}}]}
        self.assertFalse(manager.device_routing(profile, "common")["windows_geolocation"])
        with self.assertRaises(manager.HTTPException):
            manager.validate_client_capabilities(profile)

    def test_edit_cannot_forge_windows_registration_metadata(self):
        current = {"common_device_id": "common", "devices": [{"id": "common", "name": "Common"}, {"id": "phone", "name": "Phone", "os": "ios", "hwid_hash": "a" * 64}], "connections": []}
        payload = manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(id="common", name="Common"), manager.ProfileDeviceInput(id="phone", name="Phone", os="windows", hwid_hash="b" * 64, routing={"windows_geolocation": True})])
        with self.assertRaises(manager.HTTPException):
            manager.preflight_client_export_update(current, payload)

    def test_normalization_retires_incompatible_legacy_location_flags(self):
        profile = {"common_device_id": "common", "connections": [], "devices": [
            {"id": "common", "routing": {"windows_geolocation": True}},
            {"id": "phone", "os": "ios", "hwid_hash": "a" * 64, "routing": {"windows_geolocation": True}},
            {"id": "pc", "os": "windows", "hwid_hash": "b" * 64, "routing": {"windows_geolocation": True}},
        ]}
        normalized = manager.normalize_profile(profile)
        self.assertEqual([device["routing"]["windows_geolocation"] for device in normalized["devices"]], [False, False, True])
        self.assertTrue(profile["devices"][0]["routing"]["windows_geolocation"])
