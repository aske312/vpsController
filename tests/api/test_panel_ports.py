"""Panel and Mihomo installers share port reservations and actual client ports."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import ROOT, api, manager

ports = api.port_allocation
REAL_RUN = subprocess.run


class PanelPortsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(ports, "CONFIG_ROOT", self.root / "config"))
        self.enterContext(patch.object(ports, "DATA_ROOT", self.root / "data"))
        self.enterContext(patch.object(ports, "ENV_FILE", self.root / "environment"))
        ports.ENV_FILE.write_text(f'WG_CONFIG="{self.root.as_posix()}/wg.conf"\nAWG_CONFIG="{self.root.as_posix()}/awg.conf"\n')
        self.sockets = self.enterContext(patch.object(ports, "socket_ports", return_value=set()))
        self.enterContext(patch.object(ports.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)))

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_every_installable_listener_uses_free_port_and_skips_other_modules(self):
        self.sockets.return_value = {(protocol, default) for entries in ports.LISTENERS.values()
                                     for _, protocol, default, _ in entries}
        for module in ports.LISTENERS:
            if module == "ikev2":
                continue
            with self.subTest(module=module):
                selected = ports.prepare(module)
                for key, protocol, old, _ in ports.module_ports(module):
                    self.assertNotEqual(selected[key], old)
                    self.assertNotIn((protocol, selected[key]), self.sockets.return_value)
                    self.assertIn(selected[key], ports.reserved_ports({protocol}))
        # Direct TUIC and Mihomo TUIC must not receive the same UDP port.
        self.assertNotEqual(ports.prepare("tuic")["TUIC_PORT"], 10443)

    def test_saved_wireguard_port_is_preserved_when_its_service_owns_socket(self):
        (self.root / "wg.conf").write_text('[Interface]\nListenPort = 20123\n[Peer]\n')
        self.sockets.return_value = {("udp", 20123)}
        with patch.object(ports.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(ports.prepare("wg")["WG_PORT"], 20123)

    def test_stopped_existing_service_cannot_silently_move_client_port(self):
        self.save(ports.CONFIG_ROOT / "hysteria2/settings.json", {"port": 20123})
        self.sockets.return_value = {("udp", 20123)}
        with self.assertRaises(ports.PortConflict):
            ports.prepare("hysteria2")
        self.assertEqual(ports.read_json(ports.CONFIG_ROOT / "hysteria2/settings.json")["port"], 20123)

    def test_partial_multi_port_failure_releases_only_its_pending_reservations(self):
        self.save(ports.CONFIG_ROOT / "hysteria2/settings.json", {"stats_port": 20003})
        self.sockets.return_value = {("tcp", 20003)}
        unrelated = ports.allocate("unrelated", 20000, {"tcp"})
        with self.assertRaises(ports.PortConflict):
            ports.prepare("hysteria2")
        claims = ports.read_json(ports.DATA_ROOT / "port-reservations.json")
        self.assertEqual(set(claims), {"unrelated"})
        self.assertEqual(claims["unrelated"]["port"], unrelated)

    def test_panel_and_mihomo_concurrent_allocations_do_not_share_ports(self):
        self.enterContext(patch.object(manager, "unavailable_module_ports", return_value=set()))
        def allocate(index):
            owner = f"panel:ss:{index}" if index % 2 else f"mihomo:ss:{index}"
            if index % 2:
                return ports.allocate(owner, 32000, {"tcp", "udp"})
            return manager.free_module_port(32000, {"tcp", "udp"}, owner=owner)
        with ThreadPoolExecutor(max_workers=8) as pool:
            selected = list(pool.map(allocate, range(16)))
        self.assertEqual(set(selected), set(range(32000, 32016)))

    def test_ikev2_keeps_standard_ports_and_rejects_conflicts(self):
        self.assertEqual(ports.prepare("ikev2"), {"IKE_PORT": 500, "IKE_NAT_PORT": 4500})
        self.sockets.return_value = {("udp", 500)}
        with self.assertRaises(ports.PortConflict):
            ports.prepare("ikev2")

    def test_openvpn_saved_tcp_mode_does_not_conflict_with_udp(self):
        self.save(ports.CONFIG_ROOT / "openvpn/settings.json", {"port": 1194, "protocol": "tcp"})
        self.sockets.return_value = {("udp", 1194)}
        self.assertEqual(ports.prepare("openvpn")["OPENVPN_PORT"], 1194)

    def test_saved_port_takes_precedence_over_stale_api_process_environment(self):
        with ports.ENV_FILE.open("a") as env:
            env.write("TUIC_PORT=20123\n")
        with patch.dict(os.environ, {"TUIC_PORT": "20124"}):
            self.assertEqual(ports.prepare("tuic")["TUIC_PORT"], 20123)

    def test_existing_vless_ports_and_local_api_are_preserved(self):
        folder = ports.CONFIG_ROOT / "vless-reality-xhttp"
        self.save(folder / "config.json", {"api": {"listen": "127.0.0.1:20124"}, "inbounds": [{"tag": "vless-reality-xhttp", "port": 20123}]})
        (folder / "reality.env").write_text('PORT=20123\nCDN_PORT=20125\nTLS_PORT=20126\n')
        self.sockets.return_value = {("tcp", port) for port in range(20123, 20127)}
        with patch.object(ports.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            selected = ports.prepare("vless-reality-xhttp")
        self.assertEqual(selected, {"VLESS_REALITY_PORT": 20123, "VLESS_CDN_PORT": 20125, "VLESS_TLS_PORT": 20126, "VLESS_API_PORT": 20124})

    def test_shadowsocks_client_uri_runtime_and_saved_record_share_selected_port(self):
        self.save(ports.DATA_ROOT / "mihomo/settings/transport-tuic.json", {"port": 30001})
        self.sockets.return_value = {("udp", 30000)}
        with patch.object(api, "SHADOWSOCKS_CONFIG_DIR", ports.CONFIG_ROOT / "shadowsocks/clients"), patch.object(api, "SHADOWSOCKS_PORT_START", 30000), patch.object(api, "read_clients", return_value=[]), patch.object(api, "write_clients") as saved, patch.object(api, "shadowsocks_firewall"), patch.object(api, "run", side_effect=lambda *args, **kwargs: "loaded" if "show" in args else "active"), patch.object(api, "channel_mode_endpoint", return_value=("192.0.2.1", "direct")):
            result = api.create_shadowsocks_client(api.ClientCreate(name="test", protocol="shadowsocks"), "example", "example")
            config = ports.read_json(api.SHADOWSOCKS_CONFIG_DIR / "example.json")
        self.assertEqual(config["server_port"], 30002)
        self.assertIn("192.0.2.1:30002", result["config"])
        self.assertEqual(saved.call_args.args[0][0]["port"], 30002)
        self.assertNotIn("panel:ss:example", ports.read_json(ports.DATA_ROOT / "port-reservations.json"))

    def test_port_probe_failure_never_creates_a_reservation(self):
        self.sockets.side_effect = RuntimeError("probe unavailable")
        with self.assertRaises(RuntimeError):
            ports.allocate("panel:ss:test", 30000, {"tcp", "udp"})
        self.assertEqual(ports.read_json(ports.DATA_ROOT / "port-reservations.json"), {})

    def test_legacy_stopped_listeners_and_delivered_profiles_remain_reserved(self):
        self.save(ports.CONFIG_ROOT / "mihomo/quic/hysteria2/config.json", {"inbounds": [{"listen_port": 32000}]})
        self.save(ports.DATA_ROOT / "clients.json", [{"id": "old", "protocol": "shadowsocks", "port": 32001}])
        self.save(ports.DATA_ROOT / "mihomo/profiles.json", [{"id": "profile", "retiring_connections": [{"component": "transport-reality", "credential": {"port": 32002, "cdn_port": 32003}}]}])
        self.assertEqual(ports.allocate("panel:ss:new", 32000, {"tcp", "udp"}), 32004)

    def test_vless_installer_uses_selected_public_and_local_ports(self):
        source = (ROOT / "protocol-images/vless-reality-xhttp/install.sh").read_text(encoding="utf-8")
        program = source.split('"${TLS_XHTTP_MODE}" <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
        output = self.root / "vless.json"
        args = [str(output), "20123", "example.com:443", "example.com", "private", "12345678", "/test",
                "cdn.example.com", "20124", "/cdn", "yes", "websocket", "auto",
                "tls.example.com", "20125", "/tls", "yes", "websocket", "auto"]
        # Restore the actual subprocess runner just for the extracted generator.
        with patch.object(ports.subprocess, "run", wraps=REAL_RUN):
            result = subprocess.run([sys.executable, "-", *args], input=program, text=True, capture_output=True,
                                    env={**os.environ, "VLESS_API_PORT": "20126"})
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(output.read_text())
        self.assertEqual({row["port"] for row in config["inbounds"]}, {20123, 20124, 20125})
        self.assertEqual(config["api"]["listen"], "127.0.0.1:20126")

    @unittest.skipUnless(os.name == "posix", "Requires Linux flock across independent processes")
    def test_independent_processes_cannot_reserve_same_port(self):
        program = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import port_allocation as p
p.CONFIG_ROOT=Path(sys.argv[2]); p.DATA_ROOT=Path(sys.argv[3]); p.ENV_FILE=Path(sys.argv[4])
p.socket_ports=lambda: set()
print(p.allocate('process:'+sys.argv[5], 35000, {'tcp','udp'}))
"""
        def allocate(index):
            result = REAL_RUN([sys.executable, "-c", program, str(ROOT / "api"), str(ports.CONFIG_ROOT),
                              str(ports.DATA_ROOT), str(ports.ENV_FILE), str(index)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return int(result.stdout)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(set(pool.map(allocate, range(8))), set(range(35000, 35008)))

    @unittest.skipUnless(os.name == "posix", "Requires Linux installer environment")
    def test_shell_installer_receives_and_persists_allocated_port(self):
        helper = (ROOT / "api/port_allocation.py").read_text()
        helper = helper.replace('CONFIG_ROOT = Path("/etc/vps-control")', f'CONFIG_ROOT = Path({str(ports.CONFIG_ROOT)!r})')
        helper = helper.replace('DATA_ROOT = Path("/var/lib/vps-control")', f'DATA_ROOT = Path({str(ports.DATA_ROOT)!r})')
        install_root = self.root / "install"
        (install_root / "api").mkdir(parents=True)
        (install_root / "api/port_allocation.py").write_text(helper)
        source = (ROOT / "scripts/vps-control.sh").read_text()
        function = source.split('prepare_protocol_ports() {', 1)[1].split('\ninstall_protocol_image() {', 1)[0]
        script = """set -euo pipefail
die() { echo "$*" >&2; exit 1; }
set_env_value() { printf '%s=%s\\n' "$1" "$2" >> "$ENV_FILE"; }
prepare_protocol_ports() {""" + function + '\nprepare_protocol_ports tuic\nprintf "selected=%s\\n" "$TUIC_PORT"\n'
        result = REAL_RUN(["bash", "-c", script], capture_output=True, text=True,
                          env={**os.environ, "INSTALL_DIR": str(install_root), "ENV_FILE": str(ports.ENV_FILE)})
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = int(result.stdout.strip().split("selected=")[-1])
        self.assertEqual(int(ports.read_env(ports.ENV_FILE)["TUIC_PORT"]), selected)
        self.assertIn(selected, ports.reserved_ports({"udp"}))
