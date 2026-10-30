"""Mihomo YAML exports and safe migration of saved profile settings."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
import yaml
from unittest.mock import Mock, patch

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

    def test_legacy_device_format_is_migrated_to_yaml(self):
        for device in ('common', 'phone'):
            text, extension = manager.render_client_profile(self.profile, device)
            self.assertEqual(extension, 'yaml')
            self.assertIn('proxies', yaml.safe_load(text))

    @unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Set Mihomo binary for offline configuration validation")
    def test_basic_profile_validates_without_downloading_geographic_databases(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(manager, "CORE_BIN", Path(os.environ["PRIVACY_MIHOMO_BIN"])), patch.object(manager, "CORE_HOME", Path(folder)):
            config = manager.render_profile(self.profile, "common")
            manager.validate_rendered_profile(config)
            self.assertFalse(any(path.suffix in {".dat", ".mmdb", ".metadb"} for path in Path(folder).rglob("*")))

    def test_retired_format_fragment_and_ad_settings_are_discarded(self):
        self.assertEqual(manager.validate_routing({'client_config_format':'singbox', 'tunnel_fragment':True,
            'tunnel_fragment_size':'200-100', 'block_ads':True, 'block_ads_rules':'@default'}, current={}), {})


    def test_non_yaml_explicit_exports_are_rejected(self):
        for format in ('singbox', 'xray', 'uri', 'invalid'):
            with self.subTest(format=format), self.assertRaises(manager.HTTPException) as rejected:
                manager.render_client_profile(self.profile, 'phone', format)
            self.assertEqual(rejected.exception.status_code, 422 if format == "invalid" else 410)

    def test_subscription_names_follow_routes_relay_name_in_yaml(self):
        connection = self.profile['connections'][0]
        connection['settings'].update(relay_host_id='a'*32, relay_cdn=True, cdn_domain='example.com')
        source = deepcopy(self.profile)
        control = Mock()
        control.endpoint.return_value = ('example.com', 443)
        with patch.object(manager, 'relay_connection_control', return_value=control):
            for name in ['Relay West', 'Москва · "CDN"']:
                control.read.return_value = {'hosts': [{'id': 'a'*32, 'name': name}]}
                with self.subTest(name=name):
                    control.read.reset_mock()
                    data, _ = manager.render_client_profile(self.profile, 'common')
                    config = yaml.safe_load(data)
                    label = config['proxies'][0]['name']
                    group = config['proxy-groups'][0]
                    self.assertEqual(group['name'], '312node.net')
                    self.assertEqual(group['proxies'], [label])
                    self.assertEqual(config['rules'][-1], 'MATCH,312node.net')
                    self.assertEqual(label, f'1 \u00b7 VLESS \u00b7 WebSocket \u00b7 TLS \u00b7 CDN \u00b7 Relay: {name}')
                    control.read.assert_called_once_with()
        self.assertEqual(self.profile, source)

    def test_relay_labels_keep_protocol_details_for_all_components_and_modes(self):
        for component in ['transport-wg', 'transport-awg', 'transport-shadowsocks',
                          'transport-hysteria2', 'transport-tuic', 'transport-reality']:
            for variant in ['direct', 'tls', 'cdn']:
                with self.subTest(component=component, variant=variant):
                    connection = {'component': component, 'credential': {}, 'settings': {}}
                    original = manager.connection_label(connection, 1, variant)
                    connection.update(_relay_name='North')
                    self.assertEqual(manager.connection_label(connection, 1, variant), original)
                    connection['settings']['relay_host_id'] = 'a'*32
                    self.assertEqual(manager.connection_label(connection, 1, variant), original + ' · Relay: North')

    def test_manual_device_settings_survive_input_and_normalization(self):
        device = manager.ProfileDeviceInput(id="phone", name="iPhone", manual=True, os="ios",
                                           routing={"client_config_format": "singbox"})
        self.profile["devices"][1] = device.model_dump()
        normalized = manager.normalize_profile(self.profile)
        self.assertEqual(normalized["devices"][1]["scope"], "manual")
        self.assertNotIn("client_config_format", manager.device_routing(normalized, "phone"))
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
                self.assertTrue(response.media_type.startswith("text/yaml"))
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
            config = yaml.safe_load(response.body)
            self.assertIn('.yaml', response.headers["content-disposition"])
            self.assertEqual(len([entry for entry in config["proxies"] if entry["type"] == "vless"]), 1)
            register.assert_not_called()
            self.assertEqual(len(self.profile["devices"]), 2)
            saved = save.call_args.args[0]
            self.assertTrue(saved["devices"][1]["hwid_hash"])
            self.assertEqual(saved["devices"][1]["scope"], "hwid")
            self.assertEqual(saved["devices"][1]["name"], "phone")
            self.assertNotIn("client_config_format", saved["devices"][1]["routing"])

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

    def test_download_device_yaml(self):
        with patch.object(manager, 'profiles', return_value=[self.profile]):
            response = manager.profile_config('profile', 'phone')
        self.assertIn('proxies', yaml.safe_load(response.body))
        self.assertIn('.yaml', response.headers['content-disposition'])


if __name__ == "__main__":
    unittest.main()
