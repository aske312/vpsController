import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.api.support import manager, ROOT


class MihomoConnectionCountTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", return_value=True))

    def validate(self, values):
        with (patch.object(manager, "connection_defaults", return_value={}),
              patch.object(manager, "validate_connection", side_effect=lambda component, settings, **kwargs: dict(settings))):
            return manager.validate_connection_inputs([manager.ProfileConnectionInput(**value) for value in values])

    def test_each_protocol_accepts_more_than_five_connections_on_one_device(self):
        for component in manager.TRANSPORTS:
            with self.subTest(component=component):
                values = [{"id": f"connection-{index}", "device_id": "phone", "component": component,
                           "settings": {"target": "example.com:443"}} for index in range(8)]
                result = self.validate(values)
                self.assertEqual(len(result), 8)
                self.assertEqual({item["id"] for item in result}, {item["id"] for item in values})

    def test_repeated_vless_tls_cdn_and_combined_routes_remain_separate(self):
        for mode in ("direct", "tls", "cdn", "both"):
            with self.subTest(mode=mode):
                values = [{"id": f"connection-{index}", "device_id": "phone", "component": "transport-reality",
                           "settings": {"route_mode": mode, "target": "example.com:443"}} for index in range(8)]
                self.assertEqual([item["settings"]["route_mode"] for item in self.validate(values)], [mode] * 8)

    def test_automatic_sni_selection_reuses_pool_after_all_hosts_are_used(self):
        values = [{"id": f"connection-{index}", "device_id": "phone", "component": "transport-reality"} for index in range(8)]
        with patch.object(manager, "MIHOMO_REALITY_SNI_POOL", ("first.example", "second.example")):
            result = self.validate(values)
        self.assertEqual(len(result), 8)
        self.assertEqual({item["settings"]["target"] for item in result[:2]}, {"first.example:443", "second.example:443"})
        self.assertTrue(all(item["settings"]["target"] in {"first.example:443", "second.example:443"} for item in result))

    def test_duplicate_connection_ids_are_still_rejected(self):
        values = [{"id": "same", "component": "transport-wg"}, {"id": "same", "component": "transport-wg"}]
        with self.assertRaisesRegex(manager.HTTPException, "Duplicate connection id"):
            self.validate(values)

    def test_presets_preserve_repeated_protocols_instead_of_silently_dropping_them(self):
        for component in manager.TRANSPORTS:
            with self.subTest(component=component):
                components = [{"id": component, "label": f"Channel {index}"} for index in range(8)]
                result = manager.validate_profile_presets([{"id": "repeated", "components": components}])
                self.assertEqual([item["label"] for item in result[0]["components"]], [item["label"] for item in components])

    def test_export_keeps_all_repeated_protocol_connections_and_their_credentials(self):
        connections = []
        for component in ("transport-shadowsocks", "transport-hysteria2", "transport-tuic"):
            for index in range(8):
                connections.append({"id": f"{component}-{index}", "device_id": "common", "component": component,
                                    "credential": {"port": 18443, "method": "aes-256-gcm", "up_mbps": 100, "down_mbps": 100,
                                                   "congestion_control": "bbr", "heartbeat": "10s", "uuid": f"00000000-0000-4000-8000-{index:012d}",
                                                   "password": f"fixture-{component}-{index}"}})
        item = {"id": "fixture", "name": "Repeated", "common_device_id": "common",
                "devices": [{"id": "common", "routing": {}}], "connections": connections}
        with patch.object(manager, "public_endpoint", return_value="192.0.2.1"):
            exported = yaml.safe_load(manager.render_profile(item))["proxies"]
        self.assertEqual(len(exported), len(connections))
        self.assertEqual(len({entry["name"] for entry in exported}), len(connections))
        self.assertEqual({entry["password"] for entry in exported}, {entry["credential"]["password"] for entry in connections})

    def test_retired_limit_does_not_return_from_saved_settings_or_old_client(self):
        old = {"vless_max_connections_per_device": 1, "strategy": "fallback"}
        self.assertNotIn("vless_max_connections_per_device", manager.routing_settings(old))
        current = manager.validate_routing(old, current=old)
        self.assertNotIn("vless_max_connections_per_device", current)
        self.assertEqual(current["strategy"], "fallback")
        self.assertEqual(old["vless_max_connections_per_device"], 1)

    def test_device_priority_reaches_fallback_and_manual_groups(self):
        connections = [{"id": key, "name": key, "device_id": device, "component": "transport-shadowsocks",
                        "settings": {}, "credential": {"port": 18443, "method": "aes-256-gcm", "password": key}}
                       for key, device in [("primary", "phone"), ("unrelated", "common"), ("reserve", "phone")]]
        for strategy in ("fallback", "select"):
            item = {"id": "fixture", "name": "Priority", "common_device_id": "common",
                    "devices": [{"id": device, "routing": {"strategy": strategy}} for device in ("common", "phone")],
                    "connections": connections}
            with self.subTest(strategy=strategy), patch.object(manager, "public_endpoint", return_value="192.0.2.1"):
                before = yaml.safe_load(manager.render_profile(item, "phone"))
                item["connections"] = list(reversed(connections))
                after = yaml.safe_load(manager.render_profile(item, "phone"))
                self.assertEqual(after["proxy-groups"][0]["type"], strategy)
                def group_passwords(config):
                    by_name = {proxy["name"]: proxy["password"] for proxy in config["proxies"]}
                    return [by_name[name] for name in config["proxy-groups"][0]["proxies"]]
                self.assertEqual(group_passwords(before), ["primary", "reserve"])
                self.assertEqual(group_passwords(after), ["reserve", "primary"])
                self.assertEqual([proxy["password"] for proxy in after["proxies"]], ["reserve", "primary"])
                self.assertEqual([proxy["password"] for proxy in yaml.safe_load(manager.render_profile(item, "common"))["proxies"]], ["unrelated"])

    def test_multiple_quic_credentials_have_independent_secrets_and_revocation(self):
        for component in ("transport-hysteria2", "transport-tuic"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings = {"port": 10443, "congestion_control": "bbr", "heartbeat": "10s", "up_mbps": 0, "down_mbps": 0, "obfs": False}
                with (patch.object(manager, "quic_root", return_value=root),
                      patch.object(manager, "module_settings", return_value=settings),
                      patch.object(manager, "write_quic_runtime")):
                    first = manager.add_quic_credential("profile", component, "first")
                    second = manager.add_quic_credential("profile", component, "second")
                    self.assertNotEqual(first["instance_id"], second["instance_id"])
                    self.assertNotEqual(first["password"], second["password"])
                    second_path = root / "credentials" / f"{second['instance_id']}.json"
                    self.assertEqual(json.loads(second_path.read_text()), second)
                    manager.remove_quic_credential(component, first)
                    self.assertEqual(json.loads(second_path.read_text()), second)
