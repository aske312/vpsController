"""Port allocation respects both live sockets and stopped module configurations."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import ROOT, manager


class ModulePortsTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager.port_allocation, "CONFIG_ROOT", self.root / "config"))
        self.enterContext(patch.object(manager.port_allocation, "DATA_ROOT", self.root / "data"))
        self.enterContext(patch.object(manager.port_allocation, "ENV_FILE", self.root / "environment"))
        (self.root / "environment").write_text(f'WG_CONFIG="{self.root.as_posix()}/wg.conf"\nAWG_CONFIG="{self.root.as_posix()}/awg.conf"\n')
        self.enterContext(patch.object(manager, "CONFIG_ROOT", self.root / "config/mihomo"))
        self.enterContext(patch.object(manager, "SETTINGS_ROOT", self.root / "data/mihomo/settings"))
        self.enterContext(patch.object(manager, "profiles", return_value=[]))

    def listeners(self, output="", code=0):
        return patch.object(manager, "run", return_value=subprocess.CompletedProcess([], code, output, ""))

    def test_allocator_skips_live_ipv4_ipv6_and_stopped_configuration_ports(self):
        manager.atomic_json(manager.CONFIG_ROOT / "reality/config.json", {"inbounds": [{"port": 31002}]})
        manager.atomic_json(manager.CONFIG_ROOT / "shadowsocks/stopped.json", {"server_port": 31003})
        output = "tcp LISTEN 0 128 127.0.0.1:31000 0.0.0.0:*\nudp UNCONN 0 0 [::]:31001 [::]:*\n"
        with self.listeners(output):
            self.assertEqual(manager.free_module_port(31000, {"tcp", "udp"}), 31004)
            self.assertEqual(manager.next_vless_port({"inbounds": [{"port": 31004}]}, 31002), 31005)
            # TCP and UDP can share a number without a bind conflict.
            self.assertEqual(manager.free_module_port(31001, {"tcp"}), 31001)

    def test_install_reassigns_conflicting_port_and_persists_reservation_for_next_module(self):
        for module in ("transport-hysteria2", "transport-tuic"):
            manager.atomic_json(manager.SETTINGS_ROOT / f"{module}.json", {"port": 20000})
        with self.listeners("udp UNCONN 0 0 *:20000 *:*\n"), patch.object(manager, "systemctl_active", return_value=False):
            manager.prepare_module_port("transport-hysteria2")
            manager.prepare_module_port("transport-tuic")
        self.assertEqual(manager.module_settings("transport-hysteria2")["port"], 20001)
        self.assertEqual(manager.module_settings("transport-tuic")["port"], 20002)
        self.assertIn(20001, manager.reserved_module_ports({"udp"}))

    def test_direct_module_release_clears_all_installer_reservations(self):
        claims = {
            "panel:hysteria2:HYSTERIA2_PORT": {"port": 8443, "protocols": ["udp"]},
            "panel:ss:client-one": {"port": 30000, "protocols": ["tcp", "udp"]},
            "panel:ss:client-two": {"port": 30001, "protocols": ["tcp", "udp"]},
            "mihomo:transport-reality": {"port": 10086, "protocols": ["tcp"]},
        }
        manager.port_allocation.DATA_ROOT.mkdir(parents=True)
        manager.port_allocation.write_claims(claims)
        manager.port_allocation.release_module("shadowsocks")
        manager.port_allocation.release_module("hysteria2")
        remaining = manager.port_allocation.read_json(manager.port_allocation.DATA_ROOT / "port-reservations.json")
        self.assertEqual(set(remaining), {"mihomo:transport-reality"})

    def test_defaults_are_reserved_and_settings_conflict_is_rejected(self):
        port = manager.default_settings("transport-awg")["port"]
        with self.listeners(), patch.object(manager, "atomic_json") as save:
            self.assertNotEqual(manager.free_module_port(port, {"udp"}, exclude="transport-wg"), port)
            with self.assertRaises(manager.HTTPException) as error:
                manager.patch_module_settings("transport-wg", manager.ModuleSettingsPatch(values={"port": port}))
        self.assertEqual(error.exception.status_code, 409)
        save.assert_not_called()

    def test_profiles_including_retiring_connections_prevent_automatic_port_change(self):
        for key in ("connections", "retiring_connections"):
            with self.subTest(key=key), patch.object(manager, "profiles", return_value=[{key: [{"component": "transport-tuic"}]}]), patch.object(manager, "check_module_port", side_effect=manager.ModulePortConflict("occupied")), patch.object(manager, "atomic_json") as save:
                with self.assertRaises(manager.ModulePortConflict):
                    manager.prepare_module_port("transport-tuic")
                save.assert_not_called()

    def test_failed_or_malformed_listener_probe_does_not_allocate(self):
        for output, code in (("", 1), ("unrecognized output", 0), ("udp UNCONN 0 0 *:invalid *:*", 0)):
            with self.subTest(output=output, code=code), self.listeners(output, code):
                with self.assertRaises(RuntimeError):
                    manager.free_module_port(31000, {"tcp", "udp"})

    def test_range_exhaustion_and_fixed_port_wrap_at_65535(self):
        with self.listeners("udp UNCONN 0 0 *:65535 *:*\n"):
            with self.assertRaises(manager.ModulePortConflict):
                manager.free_module_port(65535, {"udp"})
            self.assertEqual(manager.free_module_port(65535, {"udp"}, wrap=True), 1024)

    def test_wireguard_own_kernel_port_is_not_reassigned(self):
        for module, tool, interface in (("transport-wg", "wg", "mh-wg0"), ("transport-awg", "awg", "mh-awg0")):
            port = manager.module_settings(module)["port"]
            with self.subTest(module=module), patch.object(manager, "systemctl_active", return_value=True), self.listeners(str(port)) as run:
                manager.check_module_port(module, port)
                run.assert_called_once_with(tool, "show", interface, "listen-port")

    def test_vless_internal_port_is_allocated_and_installer_and_stats_use_it(self):
        source = (ROOT / "protocol-images/mihomo/modules/transport-reality/install.sh").read_text(encoding="utf-8")
        program = source.split('"${API_PORT}" <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
        config_path = manager.CONFIG_ROOT / "reality/config.json"
        inbound = {"tag": "mihomo-vless-existing", "port": 9443, "settings": {"clients": [{"id": "test-only"}]}}
        manager.atomic_json(config_path, {"inbounds": [inbound, {"tag": "api", "port": 10086}]})
        with self.listeners("tcp LISTEN 0 128 *:10086 *:*\n"), patch.object(manager, "systemctl_active", return_value=False):
            manager.prepare_module_port("transport-reality")
        port = manager.module_settings("transport-reality")["api_port"]
        self.assertEqual(port, 10089)  # Direct VLESS reserves 10087/10088.
        self.assertEqual(manager.reality_api_server(), "127.0.0.1:10086")
        candidate = self.root / "candidate.json"
        result = subprocess.run([sys.executable, "-", str(config_path), str(candidate), "1.1.1.1", "warning", str(port)], input=program, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(candidate.read_text())
        self.assertEqual(config["inbounds"][0], inbound)
        self.assertEqual(config["inbounds"][1]["listen"], "127.0.0.1")
        manager.atomic_json(config_path, config)
        self.assertEqual(manager.reality_api_server(), f"127.0.0.1:{port}")
        with patch.object(manager, "systemctl_active", return_value=True), patch.object(manager, "run") as run:
            manager.check_module_port("transport-reality", port)
            run.assert_not_called()

    def test_explicit_vless_port_conflict_is_rejected_before_runtime_changes(self):
        manager.atomic_json(manager.CONFIG_ROOT / "reality/config.json", {"inbounds": []})
        with self.listeners("tcp LISTEN 0 128 *:31000 *:*\n"), patch.object(manager, "validate_connection", return_value={"port": 31000, "route_mode": "direct"}), patch.object(manager, "apply_reality_config") as apply:
            with self.assertRaisesRegex(RuntimeError, "already used"):
                manager.add_reality_credential("profile", "connection", {})
            apply.assert_not_called()

    def test_shadowsocks_retiring_and_uncommitted_instances_reserve_ports(self):
        manager.atomic_json(manager.CONFIG_ROOT / "shadowsocks/pending.json", {"server_port": 31000})
        profile = {"retiring_connections": [{"component": "transport-shadowsocks", "credential": {"port": 31001}}]}
        with patch.object(manager, "profiles", return_value=[profile]), self.listeners(), patch.object(manager.shutil, "which", return_value=None):
            credential = manager.add_ss_credential("profile", "new")
        self.assertEqual(credential["port"], 31002)
        saved = json.loads((manager.CONFIG_ROOT / "shadowsocks/profile-new.json").read_text())
        self.assertEqual(saved["server_port"], credential["port"])

    @unittest.skipUnless(os.name == "posix", "Requires Linux ss and real loopback sockets")
    def test_real_tcp_and_udp_listeners_are_excluded(self):
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.bind(("127.0.0.1", 0))
            tcp.listen()
            udp.bind(("127.0.0.1", 0))
            udp.connect(("127.0.0.1", 9))
            ports = manager.unavailable_module_ports({"tcp", "udp"})
            for sock in (tcp, udp):
                port = sock.getsockname()[1]
                self.assertIn(port, ports)
                self.assertNotEqual(manager.free_module_port(port, {"tcp", "udp"}, wrap=True), port)
