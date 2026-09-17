import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT


class ConnectionSettingsTests(unittest.TestCase):
    def test_legacy_shadowsocks_cipher_survives_new_editor_defaults(self):
        profile = manager.normalize_profile({"id": "old", "connections": [{"id": "c", "component": "transport-shadowsocks", "credential": {"method": "aes-256-gcm"}}]})
        self.assertEqual(profile["connections"][0]["settings"]["method"], "aes-256-gcm")

    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", return_value=True))

    def test_invalid_values_fail_before_provisioning(self):
        for module, values in (("wg", {"mtu": 9000}), ("shadowsocks", {"method": "invalid"}),
                               ("hysteria2", {"up_mbps": -1}), ("tuic", {"heartbeat": "invalid"}),
                               ("shadowsocks", {"no_delay": "false"})):
            with self.subTest(module=module), self.assertRaises(manager.HTTPException):
                manager.validate_connection("transport-" + module, values)

    def test_per_user_quic_settings_override_shared_defaults_in_both_exports(self):
        for module, values, expected in (
            ("tuic", {"congestion_control": "cubic", "heartbeat": "30s", "udp_relay_mode": "quic"}, {"congestion-controller": "cubic", "heartbeat-interval": 30000, "udp-relay-mode": "quic"}),
            ("hysteria2", {"up_mbps": 23, "down_mbps": 45}, {"up": "23 Mbps", "down": "45 Mbps"}),
        ):
            with self.subTest(module=module):
                credential = {"port": 1234, "uuid": "00000000-0000-4000-8000-000000000001", "password": "test", "up_mbps": 100, "down_mbps": 100}
                row = {"id": "a", "component": "transport-" + module, "settings": values, "credential": credential}
                with patch.object(manager, "load_json", return_value={"inbounds": [{"type": module, "listen_port": 5678, "heartbeat": "10s", "up_mbps": 100, "down_mbps": 100}]}):
                    result = manager.effective_client_connections([row])[0]
                with patch.object(manager, "public_endpoint", return_value="127.0.0.1"):
                    exported = yaml.safe_load("proxies:\n" + "\n".join(manager.render_proxy(row["component"], result["credential"], "test")))["proxies"][0]
                for key, value in expected.items():
                    self.assertEqual(exported[key], value)
                config = manager.build_singbox_config([result], {}, {"nameserver": "1.1.1.1", "fallback": "8.8.8.8"}, [], "127.0.0.1", lambda: {}, None)
                for key, value in values.items():
                    self.assertEqual(config["outbounds"][0][key], value)
                self.assertEqual(exported["port"], 5678)
                self.assertEqual(credential["port"], 1234)

    def test_shadowsocks_settings_reach_independent_server_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(manager, "CONFIG_ROOT", Path(folder)), patch.object(manager, "module_settings", return_value={"port_start": 31000}), patch.object(manager, "used_ss_ports", return_value=set()), patch.object(manager, "free_module_port", return_value=31001), patch.object(manager.port_allocation, "release"), patch.object(manager, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(manager.shutil, "which", return_value=None):
                credential = manager.provision("profile", "transport-shadowsocks", "device", {"method": "aes-128-gcm", "timeout": 90, "mtu": 1300, "no_delay": False})
            config = json.loads((Path(folder) / "shadowsocks/profile-device.json").read_text())
            self.assertEqual(credential["method"], "aes-128-gcm")
            self.assertEqual((config["timeout"], config["mtu"], config["no_delay"]), (90, 1300, False))

    def test_wireguard_client_mtu_does_not_change_shared_interface(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "wg.conf"
            config.write_text("[Interface]\nListenPort = 51830\nMTU = 1420\n")
            with patch.object(manager, "WG_CONFIG_BY_MODULE", {"transport-wg": config}):
                rows = manager.effective_client_connections([
                    {"component": "transport-wg", "settings": {"mtu": mtu}, "credential": {}}
                    for mtu in (1280, 1380)
                ])
            self.assertEqual([row["credential"]["mtu"] for row in rows], [1280, 1380])
            self.assertIn("MTU = 1420", config.read_text())
