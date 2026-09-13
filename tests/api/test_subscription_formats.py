"""Universal subscriptions select a device by HWID and a format by client."""
from contextlib import nullcontext
from copy import deepcopy
from itertools import product
import json
import unittest
from unittest.mock import patch

from tests.api.support import manager


class SubscriptionFormatTests(unittest.TestCase):
    def test_remove_registered_device_preserves_other_devices(self):
        self.fetch("koala-clash/1.0", "first")
        self.fetch("Happ/3.0", "second")
        profile = manager.profile_response(self.store[0])
        removed = profile["devices"][1]["id"]
        retained = [entry for entry in profile["connections"] if entry["device_id"] != removed]
        # Deletion must not validate/reprovision retained connections or require
        # their modules to be available. Only the target credentials are revoked.
        with patch.object(manager, "deprovision") as deprovision, patch.object(manager, "validate_connection_inputs", side_effect=AssertionError("unrelated settings validated")):
            manager.delete_profile_device("profile", removed)
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.assertEqual([entry["credential"] for entry in self.store[0]["connections"]], [entry["credential"] for entry in retained])
        deprovision.assert_called_once()
        with patch.object(manager, "deprovision") as deprovision:
            self.assertTrue(manager.delete_profile_device("profile", removed)["already_removed"])
            deprovision.assert_not_called()

    def test_device_delete_protects_common_and_preserves_state_on_failure(self):
        self.fetch("Happ/3.0")
        before = deepcopy(self.store)
        with self.assertRaises(manager.HTTPException) as error:
            manager.delete_profile_device("profile", "common")
        self.assertEqual(error.exception.status_code, 422)
        with patch.object(manager, "deprovision", side_effect=RuntimeError("revoke failed")), self.assertRaisesRegex(RuntimeError, "revoke failed"):
            manager.delete_profile_device("profile", before[0]["devices"][1]["id"])
        self.assertEqual(self.store, before)

    def test_device_delete_revokes_retiring_connections_and_batches_reality(self):
        self.fetch("Happ/3.0")
        device_id = self.store[0]["devices"][1]["id"]
        self.store[0]["connections"][1]["component"] = "transport-reality"
        self.store[0]["retiring_connections"] = [{"id": "old", "device_id": device_id, "component": "transport-reality", "credential": {"id": "old-credential"}}]
        with patch.object(manager, "deprovision") as revoke, patch.object(manager, "apply_batched_reality_runtime") as apply:
            manager.delete_profile_device("profile", device_id)
        self.assertEqual(revoke.call_count, 2)
        self.assertTrue(all(call.kwargs["defer_reality_restart"] for call in revoke.call_args_list))
        apply.assert_called_once()
        self.assertEqual(self.store[0]["retiring_connections"], [])

    def test_same_hwid_in_different_apps_registers_separate_devices(self):
        self.fetch("koala-clash/1.0")
        first = deepcopy(self.store[0]["devices"][1])
        self.fetch("Happ/3.0")
        second = deepcopy(self.store[0]["devices"][2])
        self.assertEqual(first["hwid_hash"], second["hwid_hash"])
        self.assertNotEqual(first["id"], second["id"])
        self.fetch("koala-clash/2.0")
        self.fetch("Happ/4.0")
        self.assertEqual(len(self.store[0]["devices"]), 3)
        self.assertEqual(self.provision.call_count, 2)
        self.assertEqual(self.store[0]["devices"][1]["id"], first["id"])

    def test_legacy_device_is_migrated_without_reprovisioning(self):
        self.fetch("Karing/1.0")
        device = self.store[0]["devices"][1]
        device.pop("client_identity_key")
        previous = deepcopy(self.store[0]["connections"])
        self.fetch("Karing/2.0")
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.assertEqual(self.provision.call_count, 1)
        self.assertEqual(self.store[0]["connections"], previous)
        self.assertEqual(self.store[0]["devices"][1]["client_identity_key"], "karing")

    def test_common_singbox_excludes_encrypted_credentials_without_changing_profile(self):
        encrypted = {"id": "encrypted", "device_id": "common", "component": "transport-reality",
                     "settings": {"transport": "tcp"}, "credential": {"encryption": "test-encryption"}}
        self.store[0]["connections"].append(encrypted)
        response = self.fetch("sing-box MT/1.0", hwid=None)
        config = json.loads(response.body)
        self.assertEqual(config["outbounds"][0]["password"], "test-only")
        self.assertFalse(any(entry["type"] == "vless" for entry in config["outbounds"]))
        self.assertEqual(self.store[0]["connections"][1], encrypted)
        self.provision.assert_not_called()

    def setUp(self):
        self.initial = {"id": "profile", "name": "Test", "common_device_id": "common", "subscription_token": "test-token",
                        "devices": [{"id": "common", "name": "Common", "routing": {}}],
                        "connections": [{"id": "common-ss", "device_id": "common", "component": "transport-shadowsocks", "settings": {},
                                         "credential": {"port": 12000, "method": "aes-128-gcm", "password": "test-only"}}]}
        self.store = [deepcopy(self.initial)]
        self.enterContext(patch.object(manager, "profiles", side_effect=lambda: deepcopy(self.store)))
        self.enterContext(patch.object(manager, "save_profiles", side_effect=lambda data: setattr(self, "store", deepcopy(data))))
        self.enterContext(patch.object(manager, "profile_runtime_transaction", side_effect=lambda *a, **k: nullcontext()))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "reconcile_profile_encryption"))
        self.enterContext(patch.object(manager, "routing_settings", return_value={}))
        self.enterContext(patch.object(manager, "dns_settings", return_value={"enhanced_mode": "fake-ip", "nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}))
        self.enterContext(patch.object(manager, "profile_rules", return_value=[]))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="192.0.2.1"))
        self.enterContext(patch.object(manager, "validate_rendered_profile"))
        self.provision = self.enterContext(patch.object(manager, "provision_connections", side_effect=lambda pid, definitions, **kw:
            [{**entry, "credential": {"port": 12001, "method": "aes-128-gcm", "password": entry["device_id"]}} for entry in definitions]))

    def fetch(self, agent, hwid="phone", query=b"", extra_headers=()):
        headers = [(b"user-agent", agent.encode()), *extra_headers]
        if hwid:
            headers.append((b"x-hwid", hwid.encode()))
        return manager.public_profile_subscription("test-token", manager.Request({"type": "http", "method": "GET", "path": "/s/test-token",
                                                                                   "query_string": query, "headers": headers}))

    def test_first_request_detects_format_and_repeat_keeps_device_credentials(self):
        for agent, extension, supported in [("ClashMeta/1.0", "yaml", ["mihomo"]), ("ClashMi/1.0", "yaml", ["mihomo"]),
                                            ("sing-box MT (sing-box 1.14.4)", "json", ["singbox"]), ("SFI/1.0", "json", ["singbox"]),
                                            ("Karing/1.2", "yaml", ["mihomo", "singbox"]), ("UnrecognizedClient/1.0", "yaml", [])]:
            with self.subTest(agent=agent):
                self.store = [deepcopy(self.initial)]
                first = self.fetch(agent)
                connections = deepcopy(self.store[0]["connections"])
                second = self.fetch(agent)
                self.assertIn(f".{extension}", first.headers["content-disposition"])
                self.assertEqual(first.body, second.body)
                self.assertEqual(len(self.store[0]["devices"]), 2)
                self.assertEqual(connections, self.store[0]["connections"])
                self.assertEqual(manager.profile_response(self.store[0])["devices"][1]["supported_formats"], supported)

    def test_application_signatures_precede_core_names_and_stale_client_name(self):
        cases = [
            ("HiddifyNext/2.5 (ios) like ClashMeta v2ray sing-box", "Hiddify", ("mihomo", "singbox")),
            ("HiddifyNextX/3.0 (android) like ClashMeta v2ray sing-box", "Hiddify", ("mihomo", "singbox")),
            ("FlClash/0.8", "FlClash", ("mihomo",)),
            ("Happ/3.13.0 (Windows) like sing-box", "Happ", ("xray",)),
            ("FlClash X/v0.3.0 core/v1.19 Platform/windows", "FlClashX", ("mihomo",)),
            ("FlClashX/0.2.0", "FlClashX", ("mihomo",)),
            ("prizrak-box/1.0.21", "Prizrak-Box", ("mihomo",)),
            ("Clash-Verge/2.0", "Clash Verge Rev", ("mihomo",)),
            ("ClashMi/1.0", "Clash Mi", ("mihomo",)),
            ("ClashMetaForAndroid/2.0", "Clash Meta for Android", ("mihomo",)),
            ("Mihomo-Party/1.0", "Clash Party", ("mihomo",)),
            ("Clash Party/1.0", "Clash Party", ("mihomo",)),
            ("Nyanpasu/2.0", "Clash Nyanpasu", ("mihomo",)),
            ("Sparkle/1.0", "Sparkle", ("mihomo",)),
            ("koala-clash/1.0", "Koala Clash", ("mihomo",)),
            ("singbox-launcher/1.0", "Sing-Box Launcher", ("singbox",)),
            ("sing-box MT/1.0", "sing-box MT", ("singbox",)),
            ("SFA/1.0", "sing-box for Android", ("singbox",)),
            ("SFM/1.0", "sing-box for Apple", ("singbox",)),
            ("SFW/1.0", "sing-box for Desktop", ("singbox",)),
        ]
        for agent, name, formats in cases:
            with self.subTest(agent=agent):
                self.assertEqual(manager.client_identity({"user_agent": agent, "client_name": "Karing"}), (name, formats))
        for agent in ("Unknown/1.0", "Throne/1.0"):
            self.assertEqual(manager.client_formats({"user_agent": agent}), ())

    def test_desktop_headers_register_and_reuse_device(self):
        clients = [("koala-clash/1.0", "Koala Clash"),
                   ("FlClash X/v0.3.0 core/v1.19 Platform/windows", "FlClashX"),
                   ("prizrak-box/1.0.21", "Prizrak-Box")]
        for (agent, name), platform in product(clients, ("Windows", "macOS", "Linux")):
            with self.subTest(client=name, platform=platform):
                self.store = [deepcopy(self.initial)]
                headers = [(b"x-device-os", platform.encode()), (b"x-ver-os", b"test-version"),
                           (b"x-device-model", b"Desktop model")]
                first = self.fetch(agent, hwid="desktop-id", extra_headers=headers)
                connections = deepcopy(self.store[0]["connections"])
                second = self.fetch(agent, hwid="desktop-id", extra_headers=headers)
                self.assertIn(".yaml", first.headers["content-disposition"])
                self.assertEqual(first.body, second.body)
                self.assertEqual(len(self.store[0]["devices"]), 2)
                device = self.store[0]["devices"][1]
                self.assertEqual(device["client_name"], name)
                self.assertEqual(device["os"], platform.lower())
                self.assertEqual(device["os_version"], "test-version")
                self.assertEqual(device["name"], "Desktop model")
                self.assertEqual(connections, self.store[0]["connections"])

    def test_desktop_clients_without_hwid_use_common_profile(self):
        for agent in ("FlClashX/0.2.0", "prizrak-box/1.0.21"):
            with self.subTest(agent=agent):
                response = self.fetch(agent, hwid=None)
                self.assertIn(".yaml", response.headers["content-disposition"])
                self.assertEqual(len(self.store[0]["devices"]), 1)
        self.provision.assert_not_called()

    def test_multiple_hwid_clients_receive_independent_profiles(self):
        first = self.fetch("HiddifyNext/2.5 (ios) like ClashMeta v2ray sing-box", "hiddify-phone")
        second = self.fetch("SFA/1.0", "android-phone")
        self.assertIn(".yaml", first.headers["content-disposition"])
        self.assertIn(".json", second.headers["content-disposition"])
        self.assertEqual(len(self.store[0]["devices"]), 3)
        first_device, second_device = self.store[0]["devices"][1:]
        self.assertNotEqual(first_device["id"], second_device["id"])
        self.assertEqual(first_device["client_name"], "Hiddify")
        self.assertEqual(second_device["client_name"], "sing-box for Android")
        self.assertEqual(manager.profile_response(self.store[0])["devices"][1]["supported_formats"], ["mihomo", "singbox"])
        self.assertEqual(json.loads(second.body)["outbounds"][0]["password"], second_device["id"])

    def test_happ_receives_xray_and_only_compatible_connections_are_provisioned(self):
        self.store[0]["connections"].append({"id": "awg", "device_id": "common", "component": "transport-awg", "settings": {}})
        self.store[0]["devices"][0]["routing"] = {"tunnel_ech": True, "tunnel_fragment": True, "direct_games_enabled": True}
        first = self.fetch("Happ/3.13.0", "desktop")
        device = self.store[0]["devices"][1]
        self.assertEqual(device["routing"]["client_config_format"], "xray")
        self.assertFalse(device["routing"]["tunnel_ech"])
        self.assertFalse(device["routing"]["tunnel_fragment"])
        config = json.loads(first.body)
        self.assertEqual(config[0]["outbounds"][0]["protocol"], "shadowsocks")
        self.assertEqual(config[0]["outbounds"][0]["settings"]["servers"][0]["password"], device["id"])
        self.assertEqual(first.media_type, "application/json")
        self.assertEqual(len(self.provision.call_args.args[1]), 1)
        self.assertEqual(self.fetch("Happ/3.13.0", "desktop").body, first.body)
        self.assertEqual(len(self.store[0]["devices"]), 2)

    def test_happ_without_hwid_does_not_use_shared_singbox(self):
        result = self.fetch("Happ/3.13.0", hwid=None)
        self.assertIsInstance(json.loads(result.body), list)
        self.assertEqual(len(self.store[0]["devices"]), 1)
        self.provision.assert_not_called()

    def test_v2rayn_receives_xray_json_from_universal_subscription(self):
        result = self.fetch("v2rayN/7.0", hwid=None)
        self.assertIsInstance(json.loads(result.body), list)
        self.assertEqual(json.loads(result.body)[0]["outbounds"][0]["protocol"], "shadowsocks")
        self.assertEqual(manager.client_identity({"user_agent": "v2rayN/7.0"}), ("v2rayN", ("xray",)))
        self.provision.assert_not_called()

    def test_launcher_registration_keeps_supported_extensions(self):
        self.store[0]["devices"][0]["routing"] = {"tunnel_privacy": True, "tunnel_ech": True}
        self.store[0]["connections"].append({"id": "awg", "device_id": "common", "component": "transport-awg", "settings": {}})
        awg = {"port": 51820, "private_key": "A" * 43 + "=", "server_public_key": "A" * 43 + "=", "ip": "10.0.0.2/32", "mtu": 1420, "amnezia": {"jc": 4}}
        self.provision.side_effect = lambda pid, definitions, **kw: [
            {**entry, "credential": awg if entry["component"] == "transport-awg" else {"port": 12001, "method": "aes-128-gcm", "password": entry["device_id"]}}
            for entry in definitions]
        response = self.fetch("singbox-launcher/1.0", "desktop")
        config = json.loads(response.body)
        self.assertEqual(config["endpoints"][0]["jc"], 4)
        device = self.store[0]["devices"][1]
        self.assertEqual(device["client_name"], "Sing-Box Launcher")
        self.assertTrue(device["routing"]["tunnel_privacy"])
        self.assertTrue(device["routing"]["tunnel_ech"])
        self.assertEqual(len(self.provision.call_args.args[1]), 2)
        manager.validate_client_capabilities(self.store[0])
        self.assertEqual(self.fetch("singbox-launcher/1.0", "desktop").body, response.body)

    def test_incompatible_template_does_not_create_empty_happ_device(self):
        self.store[0]["connections"][0]["component"] = "transport-awg"
        with self.assertRaises(manager.HTTPException) as error:
            self.fetch("Happ/3.13.0")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.store[0]["devices"]), 1)
        self.provision.assert_not_called()

    def test_api_rejects_unsupported_happ_features_and_connections_before_provisioning(self):
        self.fetch("Happ/3.13.0")
        self.provision.reset_mock()
        for routing in ({"tunnel_ech": True}, {"tunnel_fragment": True}, {"direct_games_enabled": True}, {"strategy": "fallback"}, {"client_config_format": "singbox"}):
            devices = deepcopy(self.store[0]["devices"])
            devices[1]["routing"].update(routing)
            with self.subTest(routing=routing), self.assertRaises(manager.HTTPException) as error:
                manager.update_profile("profile", manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(**d) for d in devices]))
            self.assertEqual(error.exception.status_code, 422)
        candidate = deepcopy(self.store[0])
        candidate["connections"][1]["component"] = "transport-tuic"
        with self.assertRaises(manager.HTTPException):
            manager.validate_client_capabilities(candidate)
        self.provision.assert_not_called()

    def test_karing_switches_format_on_same_link_without_reprovisioning(self):
        self.fetch("Karing/1.2")
        credentials = deepcopy(self.store[0]["connections"])
        original_id = self.store[0]["devices"][1]["id"]
        self.provision.reset_mock()
        for format, extension in [("singbox", "json"), ("mihomo", "yaml")]:
            devices = deepcopy(self.store[0]["devices"])
            devices[1]["routing"]["client_config_format"] = format
            # A stale editor cannot replace metadata captured from the client.
            devices[1]["user_agent"] = "stale"
            manager.update_profile("profile", manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(**item) for item in devices]))
            response = self.fetch("Karing/1.2", query=b"format=wrong")
            self.assertIn(f".{extension}", response.headers["content-disposition"])
            self.assertEqual(self.store[0]["connections"], credentials)
            self.assertEqual(self.store[0]["devices"][1]["id"], original_id)
            self.assertEqual(len(self.store[0]["devices"]), 2)
            if format == "singbox":
                self.assertEqual(json.loads(response.body)["outbounds"][0]["password"], original_id)
        self.provision.assert_not_called()

    def test_single_format_and_unknown_clients_cannot_switch_in_settings(self):
        for agent, target in [("ClashMi/1.0", "singbox"), ("SFI/1.0", "mihomo"), ("Unknown/1.0", "singbox")]:
            with self.subTest(agent=agent):
                self.store = [deepcopy(self.initial)]
                self.fetch(agent)
                devices = deepcopy(self.store[0]["devices"])
                devices[1]["routing"]["client_config_format"] = target
                with self.assertRaises(manager.HTTPException) as error:
                    manager.update_profile("profile", manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(**item) for item in devices]))
                self.assertEqual(error.exception.status_code, 422)

    def test_without_hwid_uses_common_json_even_when_legacy_shared_exists(self):
        self.store[0]["devices"].append({"id": "shared", "name": "Shared", "manual": True, "routing": {"client_config_format": "singbox"}})
        connection = deepcopy(self.initial["connections"][0])
        connection.update(id="shared-ss", device_id="shared")
        connection["credential"]["password"] = "shared-password"
        self.store[0]["connections"].append(connection)
        response = self.fetch("sing-box MT/1.14", hwid=None)
        self.assertEqual(json.loads(response.body)["outbounds"][0]["password"], "test-only")
        yaml = self.fetch("ClashMi/1.0", hwid=None)
        self.assertIn('.yaml', yaml.headers["content-disposition"])
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
