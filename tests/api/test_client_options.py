"""Client format selection and sing-box TLS fragmentation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager, managed_mihomo_fixture


class ClientOptionsTests(unittest.TestCase):
    def setUp(self):
        managed_mihomo_fixture(self)
        self.profile = {
            "id": "profile", "name": "Profile", "common_device_id": "common",
            "devices": [{"id": device, "name": device, "routing": {}} for device in ("common", "phone")],
            "connections": [{"id": device, "component": "transport-reality", "device_id": device,
                             "settings": {"route_mode": "cdn"}, "credential": {"uuid": "00000000-0000-4000-8000-000000000001",
                             "cdn_enabled": True, "cdn_domain": "example.com", "cdn_transport": "websocket", "cdn_path": "/test"}}
                            for device in ("common", "phone")],
        }
        self.enterContext(patch.object(manager, "routing_settings", return_value={}))
        self.enterContext(patch.object(manager, "dns_settings", return_value={"enhanced_mode": "fake-ip", "nameserver": "https://dns.google/dns-query", "fallback": "https://cloudflare-dns.com/dns-query"}))
        self.enterContext(patch.object(manager, "profile_rules", return_value=[]))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="192.0.2.1"))

    def test_default_mihomo_and_device_scoped_singbox_export(self):
        self.assertEqual(manager.render_client_profile(self.profile, "common")[1], "yaml")
        self.profile["devices"][1]["routing"] = {"client_config_format": "singbox", "tunnel_fragment": True}
        data, extension = manager.render_client_profile(self.profile, "phone")
        self.assertEqual(extension, "json")
        config = json.loads(data)
        tls = config["outbounds"][0]["tls"]
        self.assertTrue(tls["fragment"])
        self.assertTrue(tls["record_fragment"])
        self.assertEqual(manager.render_client_profile(self.profile, "common")[1], "yaml")

    @unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Set Mihomo binary for offline configuration validation")
    def test_basic_profile_validates_without_downloading_geographic_databases(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(manager, "CORE_BIN", Path(os.environ["PRIVACY_MIHOMO_BIN"])), patch.object(manager, "CORE_HOME", Path(folder)):
            config = manager.render_profile(self.profile, "common")
            manager.validate_rendered_profile(config)
            self.assertFalse(any(path.suffix in {".dat", ".mmdb", ".metadb"} for path in Path(folder).rglob("*")))

    def test_validation_rejects_unknown_format_and_invalid_fragment_ranges(self):
        for key, value in (("client_config_format", "unknown"),
                           ("tunnel_fragment", "true"), ("tunnel_fragment_size", "200-100"), ("tunnel_fragment_interval", "1001")):
            with self.subTest(key=key, value=value), self.assertRaises(manager.HTTPException):
                manager.validate_routing({key: value}, current={})

    def test_singbox_does_not_misrepresent_amnezia_wireguard(self):
        profile = deepcopy(self.profile)
        profile["devices"][1]["routing"] = {"client_config_format": "singbox"}
        profile["connections"].append({"id": "awg", "component": "transport-awg", "device_id": "phone", "credential": {
            "port": 51820, "private_key": "key", "server_public_key": "public", "ip": "10.0.0.2/32", "mtu": 1420,
            "amnezia": {"jc": 4, "jmin": 40, "jmax": 70, "s1": 1, "s2": 2, "h1": 3, "h2": 4, "h3": 5, "h4": 6},
        }})
        config = json.loads(manager.render_client_profile(profile, "phone")[0])
        self.assertFalse(any(item["type"] == "wireguard" for item in config["outbounds"]))
        with self.assertRaises(manager.HTTPException):
            manager.validate_client_capabilities(profile)

    def test_requested_format_overrides_device_default(self):
        data, extension = manager.render_client_profile(self.profile, "phone", "singbox")
        self.assertEqual(extension, "json")
        self.assertIn('"outbounds"', data)

    def test_manual_device_settings_survive_input_and_normalization(self):
        device = manager.ProfileDeviceInput(id="phone", name="iPhone", manual=True, os="ios",
                                           routing={"client_config_format": "singbox"})
        self.profile["devices"][1] = device.model_dump()
        normalized = manager.normalize_profile(self.profile)
        self.assertEqual(normalized["devices"][1]["scope"], "manual")
        self.assertEqual(manager.device_routing(normalized, "phone")["client_config_format"], "singbox")
        self.assertEqual(normalized["common_device_id"], "common")

    def test_shared_singbox_creation_is_disabled_but_existing_remain_editable(self):
        common = {"id": "common", "routing": {}}
        shared = {"id": "shared", "manual": True, "routing": {"client_config_format": "singbox"}}
        manager.validate_profile_devices([common, shared], "common", [common, shared])
        cases = [
            [common, shared],
            [common, shared, {**shared, "id": "second"}],
            [common, {**shared, "routing": {"client_config_format": "mihomo"}}],
            [common, {"id": "new-yaml", "routing": {}}],
            [common, {**shared, "manual": False}],
        ]
        for devices in cases:
            with self.subTest(devices=devices), self.assertRaises(manager.HTTPException) as error:
                manager.validate_profile_devices(devices, "common")
            self.assertEqual(error.exception.status_code, 422)
        # Existing automatically registered YAML devices remain editable.
        hwid = {"id": "hwid-phone", "hwid_hash": "a" * 64, "routing": {}}
        manager.validate_profile_devices([common, shared, hwid], "common", [common, shared, hwid])

    def test_shared_singbox_is_not_bound_to_hwid_and_serves_multiple_clients(self):
        self.profile["subscription_token"] = "test-profile-token"
        self.profile["devices"][1].update(manual=True, routing={"client_config_format": "singbox"})
        store = [deepcopy(self.profile)]
        with (patch.object(manager, "profiles", side_effect=lambda: deepcopy(store)),
              patch.object(manager, "save_subscription_profile", side_effect=lambda profile: store.__setitem__(0, deepcopy(profile))),
              patch.object(manager, "subscription_device") as register):
            token = manager.profile_subscription("profile", "phone")["path"].rsplit("/", 1)[-1]
            bodies = []
            for hwid in (None, "first", "second", None):
                request = manager.Request({"type": "http", "method": "GET", "path": "/s/test", "query_string": b"",
                                           "headers": [(b"x-hwid", hwid.encode())] if hwid else []})
                response = manager.public_profile_subscription(token, request)
                bodies.append(response.body)
                self.assertEqual(response.media_type, "application/json")
                self.assertFalse(store[0]["devices"][1].get("hwid_hash"))
            self.assertTrue(all(body == bodies[0] for body in bodies))
            self.assertEqual(len(store[0]["devices"]), 2)
            register.assert_not_called()

    def test_device_link_delivers_only_selected_device_and_binds_hwid(self):
        self.profile["subscription_token"] = "test-profile-token"
        self.profile["devices"][1]["routing"] = {"client_config_format": "singbox"}
        with (patch.object(manager, "profiles", return_value=[self.profile]),
              patch.object(manager, "save_subscription_profile") as save,
              patch.object(manager, "subscription_device") as register):
            link = manager.profile_subscription("profile", "phone")["path"]
            self.assertNotEqual(link, manager.profile_subscription("profile")["path"])
            self.assertEqual(link, manager.profile_subscription("profile", "phone")["path"])
            request = manager.Request({"type": "http", "method": "GET", "path": link,
                                       "query_string": b"device_id=common", "headers": [(b"x-hwid", b"unexpected-device")]})
            response = manager.public_profile_subscription(link.rsplit("/", 1)[-1], request)
            config = json.loads(response.body)
            self.assertEqual(response.media_type, "application/json")
            self.assertIn('.json', response.headers["content-disposition"])
            self.assertEqual(len([entry for entry in config["outbounds"] if entry["type"] == "vless"]), 1)
            register.assert_not_called()
            self.assertEqual(len(self.profile["devices"]), 2)
            saved = save.call_args.args[0]
            self.assertTrue(saved["devices"][1]["hwid_hash"])
            self.assertEqual(saved["devices"][1]["scope"], "hwid")
            self.assertEqual(saved["devices"][1]["name"], "phone")

    def test_device_binding_allows_same_hwid_and_rejects_other_or_missing(self):
        self.profile["subscription_token"] = "test-profile-token"
        self.profile["devices"][1]["routing"] = {"client_config_format": "singbox"}
        store = [deepcopy(self.profile)]
        with (patch.object(manager, "profiles", side_effect=lambda: deepcopy(store)),
              patch.object(manager, "save_subscription_profile", side_effect=lambda profile: store.__setitem__(0, deepcopy(profile))),
              patch.object(manager, "provision_connections") as provision):
            token = manager.profile_subscription("profile", "phone")["path"].rsplit("/", 1)[-1]

            def fetch(hwid):
                return manager.public_profile_subscription(token, manager.Request({"type": "http", "method": "GET", "path": "/s/test",
                    "query_string": b"", "headers": [(b"x-hwid", hwid.encode())] if hwid else []}))

            fetch(None)
            self.assertFalse(store[0]["devices"][1].get("hwid_hash"))
            fetch("first-phone")
            bound_hash = store[0]["devices"][1]["hwid_hash"]
            fetch("first-phone")
            for hwid, status in (("another-phone", 403), (None, 403), ("x" * 257, 422)):
                with self.subTest(hwid=hwid), self.assertRaises(manager.HTTPException) as error:
                    fetch(hwid)
                self.assertEqual(error.exception.status_code, status)
            self.assertEqual(store[0]["devices"][1]["hwid_hash"], bound_hash)
            self.assertEqual(store[0]["connections"], self.profile["connections"])
            provision.assert_not_called()
            # The common subscription must recognize the same binding, without a second device.
            _, device_id = manager.subscription_device(store[0], "first-phone", "test-profile-token", {})
            self.assertEqual(device_id, "phone")
            self.assertEqual(len(store[0]["devices"]), 2)

    def test_existing_hwid_cannot_claim_second_device(self):
        self.profile["subscription_token"] = "test-profile-token"
        self.profile["devices"][1]["hwid_hash"] = manager.hmac.new(b"test-profile-token", b"phone", manager.hashlib.sha256).hexdigest()
        self.profile["devices"].append({"id": "other", "name": "Other", "routing": {}})
        with patch.object(manager, "profiles", return_value=[self.profile]), patch.object(manager, "save_subscription_profile") as save:
            token = manager.profile_subscription("profile", "other")["path"].rsplit("/", 1)[-1]
            request = manager.Request({"type": "http", "method": "GET", "path": "/s/test", "query_string": b"", "headers": [(b"x-hwid", b"phone")]})
            with self.assertRaises(manager.HTTPException) as error:
                manager.public_profile_subscription(token, request)
            self.assertEqual(error.exception.status_code, 409)
            save.assert_not_called()

    def test_removed_device_or_rotated_profile_token_revokes_device_link(self):
        self.profile["subscription_token"] = "test-profile-token"
        request = manager.Request({"type": "http", "method": "GET", "path": "/s/test",
                                   "query_string": b"", "headers": []})
        with patch.object(manager, "profiles", return_value=[self.profile]):
            link = manager.profile_subscription("profile", "phone")["path"]
            token = link.rsplit("/", 1)[-1]
            for change in ("rotate", "remove"):
                with self.subTest(change=change):
                    if change == "rotate":
                        self.profile["subscription_token"] = "new-profile-token"
                    else:
                        self.profile["subscription_token"] = "test-profile-token"
                        self.profile["devices"] = self.profile["devices"][:1]
                    with self.assertRaises(manager.HTTPException) as error:
                        manager.public_profile_subscription(token, request)
                    self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(manager.HTTPException) as error:
                manager.profile_subscription("profile", "missing")
            self.assertEqual(error.exception.status_code, 404)

    def test_download_device_json(self):
        self.profile["devices"][1]["routing"] = {"client_config_format": "singbox"}
        with patch.object(manager, "profiles", return_value=[self.profile]):
            response = manager.profile_config("profile", "phone")
            self.assertIn("outbounds", json.loads(response.body))
            self.assertIn('attachment;', response.headers["content-disposition"])
            self.assertIn('.json', response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
