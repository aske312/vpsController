"""Validate exported Happ profiles against the real Xray parser when available."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.api.support import manager, ROOT
from client_xray import build_xray_configs, xray_rules
from client_singbox import UnsupportedClientConfig, _transport, build_singbox_config


class XrayExportTests(unittest.TestCase):
    def test_transports_and_rules_pass_xray_parser(self):
        binary = Path(os.environ.get("PRIVACY_XRAY_BIN", ROOT / ".runtime/privacy-binaries/xray/xray.exe"))
        if not binary.is_file():
            self.skipTest("Set PRIVACY_XRAY_BIN to validate with Xray")
        dns = {"nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}
        credential = {"uuid": "00000000-0000-4000-8000-000000000001", "port": 443, "direct_tag": "direct", "servername": "example.com", "public_key": "A" * 43, "short_id": "0123456789abcdef", "path": "/test", "cdn_domain": "example.com", "cdn_path": "/cdn", "cdn_enabled": True, "tls_domain": "example.com", "tls_path": "/tls"}
        for route, transports in (("direct", ("raw", "grpc", "xhttp")), ("cdn", ("websocket", "httpupgrade", "grpc", "xhttp")), ("tls", ("websocket", "xhttp"))):
            for transport in transports:
                with self.subTest(route=route, transport=transport):
                    data = {**credential, "transport": transport, "cdn_transport": transport, "tls_transport": transport}
                    configs = build_xray_configs([{"component": "transport-reality", "settings": {"route_mode": route}, "credential": data}], {}, dns, ["DOMAIN-SUFFIX,example.org,DIRECT", "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve"], "192.0.2.1", lambda: {})
                    stream = configs[0]["outbounds"][0]["streamSettings"]
                    self.assertEqual(stream["security"], "reality" if route == "direct" else "tls")
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "config.json"
                        path.write_text(json.dumps(configs[0]), encoding="utf-8")
                        result = subprocess.run([str(binary), "run", "-test", "-config", str(path)], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unrepresentable_rules_and_transport_are_rejected(self):
        with self.assertRaises(UnsupportedClientConfig):
            xray_rules(["PROCESS-NAME,game.exe,DIRECT"])
        with self.assertRaises(UnsupportedClientConfig):
            _transport("xhttp", "/")

    def test_singbox_preserves_domain_matching_and_obfuscation(self):
        connections = [{"component": "transport-hysteria2", "credential": {"port": 443, "password": "test", "up_mbps": 50, "down_mbps": 100, "obfs": True, "obfs_password": "test-obfs"}}]
        result = build_singbox_config(connections, {}, {"nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}, ["DOMAIN-SUFFIX,example.com,DIRECT", "DOMAIN-KEYWORD,example,REJECT"], "192.0.2.1", lambda: {}, None)
        self.assertEqual(result["outbounds"][0]["obfs"], {"type": "salamander", "password": "test-obfs"})
        self.assertEqual(result["route"]["rules"][1]["domain_suffix"], ["example.com"])
        self.assertEqual(result["route"]["rules"][2]["domain_keyword"], ["example"])
        self.assertEqual(result["dns"]["servers"][1]["type"], "https")

    def test_singbox_export_passes_current_core_parser(self):
        candidates = list((ROOT / ".runtime/singbox-check").glob("*/sing-box.exe"))
        binary = Path(os.environ.get("PRIVACY_SINGBOX_BIN", candidates[0] if candidates else "missing-sing-box"))
        if not binary.is_file():
            self.skipTest("Set PRIVACY_SINGBOX_BIN to validate with sing-box")
        connections = [{"component": "transport-shadowsocks", "credential": {"port": 443, "method": "aes-128-gcm", "password": "test"}},
                       {"component": "transport-reality", "settings": {"route_mode": "cdn"}, "credential": {"uuid": "00000000-0000-4000-8000-000000000001", "cdn_enabled": True, "cdn_domain": "example.com", "cdn_path": "/ws", "cdn_transport": "websocket"}}]
        result = build_singbox_config(connections, {}, {"nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}, ["DOMAIN-SUFFIX,example.org,DIRECT", "DOMAIN,ads.example.org,REJECT"], "192.0.2.1", lambda: {}, True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            checked = subprocess.run([str(binary.resolve()), "check", "-c", str(path)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
