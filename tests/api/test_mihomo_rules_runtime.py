"""Export mapping and real route decisions, without public destinations."""
from copy import deepcopy
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port


def profile():
    return {"id": "audit", "name": "audit", "common_device_id": "common",
            "devices": [{"id": "common", "routing": {}}],
            "connections": [{"id": "c", "device_id": "common", "component": "transport-shadowsocks",
                             "credential": {"port": 18888, "method": "aes-256-gcm", "password": "test"}}]}


class RuleExportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="127.0.0.1"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))

    def test_each_mihomo_switch_changes_its_exported_setting(self):
        fields = {"sniffer": ("sniffer", "enable"), "tcp_concurrent": ("tcp-concurrent",),
                  "tun_enabled": ("tun", "enable"), "tun_force": ("tun", "enable"),
                  "tun_strict_route": ("tun", "strict-route"), "dns_ipv6": ("dns", "ipv6"),
                  "dns_prefer_h3": ("dns", "prefer-h3")}
        for key, path in fields.items():
            for enabled in (True, False):
                with self.subTest(key=key, enabled=enabled):
                    item = profile()
                    item["devices"][0]["routing"] = {key: enabled}
                    result = yaml.safe_load(manager.render_profile(item))
                    for part in path:
                        result = result[part]
                    self.assertEqual(result, enabled)
        for enabled in (True, False):
            item = profile()
            item["devices"][0]["routing"] = {"dns_hijack_force": enabled, "dns_fake_ip": enabled}
            result = yaml.safe_load(manager.render_profile(item))
            self.assertEqual("dns-hijack" in result["tun"], enabled)
            self.assertEqual(result["dns"]["enhanced-mode"], "fake-ip" if enabled else "redir-host")

    def test_dns_policy_and_secure_override(self):
        settings = {"enhanced_mode": "redir-host", "nameserver": "1.1.1.1", "fallback": "8.8.8.8",
                    "ipv6": True, "prefer_h3": True, "cache_algorithm": "arc", "fake_ip_filter": "*.test"}
        with patch.object(manager, "dns_settings", return_value=settings):
            item = profile()
            result = yaml.safe_load(manager.render_profile(item))["dns"]
            self.assertEqual(result["nameserver"], ["1.1.1.1"])
            self.assertEqual(result["fallback"], ["8.8.8.8"])
            self.assertEqual(result["cache-algorithm"], "arc")
            self.assertTrue(result["ipv6"])
            self.assertTrue(result["prefer-h3"])
            item["devices"][0]["routing"] = {"dns_secure": True, "dns_fake_ip": True}
            result = yaml.safe_load(manager.render_profile(item))["dns"]
            self.assertTrue(all(value.startswith("https://") for value in result["nameserver"] + result["fallback"]))
            self.assertEqual(result["fake-ip-filter"], ["*.test"])

    def test_presets_are_conditional_and_custom_lists_replace_defaults(self):
        for key, rules in manager.DIRECT_RULE_PRESETS.items():
            with self.subTest(key=key):
                self.assertTrue(rules)
                self.assertTrue(set(rules).issubset(manager.profile_rules({key: True})))
                self.assertEqual(manager.profile_rules({key: False}), [])
                if key != "windows_geolocation":
                    self.assertEqual(manager.profile_rules({key: True, key + "_rules": "DOMAIN,audit.test,DIRECT"}), ["DOMAIN,audit.test,DIRECT"])
        self.assertIn("PROCESS-NAME,qbittorrent.exe,DIRECT", manager.profile_rules({"direct_p2p_enabled": True, "direct_p2p_clients": "qbittorrent"}))
        self.assertIn("NETWORK,UDP,DIRECT", manager.profile_rules({"direct_games_udp_enabled": True}))
        self.assertTrue(any(rule.startswith("PROCESS-NAME") for rule in manager.profile_rules({"direct_games_enabled": True})))

    def test_strategy_parameters_reach_group(self):
        for strategy in ("select", "url-test", "fallback"):
            item = profile()
            item["devices"][0]["routing"] = {"strategy": strategy, "interval": 77, "test_url": "http://audit.test/check", "health_timeout": 1700, "max_failed_times": 4, "tolerance": 99}
            shared = {**manager.routing_settings(), "interval": 77, "test_url": "http://audit.test/check", "health_timeout": 1700, "max_failed_times": 4, "tolerance": 99}
            item["devices"][0]["routing"].update(interval=1, test_url="http://stale.test", tolerance=1, health_timeout=1, max_failed_times=1)
            item["routing"] = {"interval": 2, "test_url": "http://stale-profile.test"}
            with patch.object(manager, "routing_settings", return_value=shared):
                group = yaml.safe_load(manager.render_profile(item))["proxy-groups"][0]
            self.assertEqual(group["type"], strategy)
            if strategy != "select":
                self.assertEqual((group["interval"], group["timeout"], group["max-failed-times"], group["url"]), (77, 1700, 4, "http://audit.test/check"))
            if strategy == "url-test":
                self.assertEqual(group["tolerance"], 99)

    def test_export_uses_applied_quic_settings_without_mutating_saved_credentials(self):
        connections = [{"component": "transport-hysteria2", "credential": {"port": 1234, "password": "client", "obfs": False}}]
        with patch.object(manager, "load_json", return_value={"inbounds": [{"type": "hysteria2", "listen_port": 5678, "up_mbps": 123, "down_mbps": 456, "obfs": {"type": "salamander", "password": "applied-obfs"}}]}):
            result = manager.effective_client_connections(connections)
        self.assertEqual(result[0]["credential"]["port"], 5678)
        self.assertEqual(result[0]["credential"]["up_mbps"], 123)
        self.assertEqual(result[0]["credential"]["obfs_password"], "applied-obfs")
        self.assertEqual(connections[0]["credential"], {"port": 1234, "password": "client", "obfs": False})

    def test_unsupported_custom_fragment_ranges_are_not_silently_saved(self):
        with self.assertRaises(manager.HTTPException):
            manager.validate_routing({"tunnel_fragment_size": "300-500"}, current={})
        with self.assertRaises(manager.HTTPException):
            manager.validate_routing({"tunnel_fragment_interval": "50-100"}, current={})

    @unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Requires real Mihomo")
    def test_real_core_routes_direct_blocks_and_respects_personal_override(self):
        class Origin(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                self.send_response(200, "Connection established")
                self.end_headers()
                # Consume the tunneled HTTP request and return the relay marker.
                self.rfile.readline()
                while self.rfile.readline().strip():
                    pass
                body = self.server.marker
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                self.wfile.flush()
            def do_GET(self):
                body = self.server.marker
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        servers = []
        for marker in (b"via-direct", b"via-tunnel"):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            server.marker = marker
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                item = profile()
                item["devices"][0]["routing"] = {"strategy": "select", "direct_ru_banks": True,
                    "block_ads": True, "block_ads_rules": "DOMAIN-SUFFIX,ads.audit.test,REJECT"}
                with patch.object(manager, "personal_rule_lines_for_device", return_value=["DOMAIN,allowed.ads.audit.test,DIRECT"]):
                    config = yaml.safe_load(manager.render_profile(item))
                config["hosts"] = {name: "127.0.0.1" for name in ("sberbank.ru", "ads.audit.test", "allowed.ads.audit.test", "unmatched.audit.test")}
                config["dns"]["enable"] = False
                config["log-level"] = "debug"
                config["mixed-port"] = port = free_port()
                # Isolate routing from the transport layer already covered by
                # the encrypted and QUIC traffic tests.
                proxy_name = config["proxies"][0]["name"]
                config["proxies"] = [{"name": proxy_name, "type": "http", "server": "127.0.0.1", "port": servers[1].server_port}]
                path = root / "config.yaml"
                path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                with open(root / "core.log", "w") as log:
                    process = subprocess.Popen([os.environ["PRIVACY_MIHOMO_BIN"], "-d", str(root), "-f", str(path)], stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    try:
                        wait_port(port, process)
                        for domain, expected in (("sberbank.ru", b"via-direct"), ("unmatched.audit.test", b"via-tunnel"), ("ads.audit.test", b"502"), ("allowed.ads.audit.test", b"via-direct")):
                            with self.subTest(domain=domain), socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                                connection.settimeout(5)
                                connection.sendall(f"GET http://{domain}:{servers[0].server_port}/ HTTP/1.1\r\nHost: {domain}:{servers[0].server_port}\r\nConnection: close\r\n\r\n".encode())
                                response = b""
                                while chunk := connection.recv(8192):
                                    response += chunk
                                self.assertIn(expected, response)
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
                        log.flush()
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
