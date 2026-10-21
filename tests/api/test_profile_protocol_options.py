import os
import copy
import hashlib
import ssl
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT


class ProfileProtocolOptionsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", return_value=True))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="127.0.0.1"))

    def test_sni_validation_allows_custom_domains_but_rejects_invalid_labels(self):
        for name in ("custom.example", "a-b.example", "xn--e1afmkfd.xn--p1ai"):
            self.assertEqual(manager.validate_connection("transport-hysteria2", {"sni": name})["sni"], name)
            self.assertEqual(manager.validate_connection("transport-reality", {"target": name + ":8443"})["target"], name + ":8443")
        for name in ("bad..example", "-bad.example", "bad-.example", "https://example.com", "a" * 64 + ".com"):
            for module, values in (("hysteria2", {"sni": name}), ("reality", {"target": name + ":443"})):
                with self.subTest(name=name, module=module), self.assertRaises(manager.HTTPException):
                    manager.validate_connection("transport-" + module, values)

    def advanced_proxies(self):
        proxies = []
        for module, settings in (
            ("hysteria2", {"up_mbps": 0, "down_mbps": 0, "handshake_timeout": 12, "udp_mtu": 1197, "initial_stream_receive_window": 1048576, "max_stream_receive_window": 2097152, "initial_connection_receive_window": 4194304, "max_connection_receive_window": 8388608}),
            ("tuic", {"max_udp_relay_packet_size": 1200, "max_open_streams": 24, "recv_window_conn": 2097152, "recv_window": 8388608, "max_datagram_frame_size": 1300, "disable_mtu_discovery": True}),
        ):
            values = manager.validate_connection("transport-" + module, {**settings, "bbr_profile": "conservative", "cwnd": 40, "certificate_fingerprint": "ab" * 32, "name_cert_verify": "custom.example", "ip_version": "ipv4"})
            credential = {"port": 18443, "uuid": "00000000-0000-4000-8000-000000000001", "password": "test", "up_mbps": 100, "down_mbps": 100}
            original = dict(credential)
            with patch.object(manager, "load_json", return_value={}):
                effective = manager.effective_client_connections([{"component": "transport-" + module, "settings": values, "credential": credential}])[0]["credential"]
            self.assertEqual(credential, original)
            proxy = yaml.safe_load("proxies:\n" + "\n".join(manager.render_proxy("transport-" + module, effective, module)))["proxies"][0]
            self.assertEqual(proxy["fingerprint"], "ab" * 32)
            self.assertEqual(proxy["name-cert-verify"], "custom.example")
            for key, value in settings.items():
                if key not in {"up_mbps", "down_mbps"}:
                    self.assertEqual(proxy[key.replace("_", "-")], value)
            proxies.append(proxy)
        return proxies

    def test_individual_options_reach_yaml_without_mutating_server_credentials(self):
        self.advanced_proxies()

    def test_real_mihomo_accepts_advanced_quic_options(self):
        binary = Path(os.environ.get("PRIVACY_MIHOMO_BIN", "missing"))
        if not binary.is_file():
            self.skipTest("Set PRIVACY_MIHOMO_BIN")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({"proxies": self.advanced_proxies(), "rules": ["MATCH,DIRECT"]}), encoding="utf-8")
            result = subprocess.run([str(binary.resolve()), "-t", "-d", directory, "-f", str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_awg_noise_settings_are_individual_and_keep_shared_handshake_parameters(self):
        credential = {"amnezia": {"jc": 6, "jmin": 8, "jmax": 80, "s1": 64, "h1": 150000000}}
        with tempfile.TemporaryDirectory() as directory, patch.object(manager, "WG_CONFIG_BY_MODULE", {"transport-awg": Path(directory) / "absent"}):
            rows = manager.effective_client_connections([{"component": "transport-awg", "credential": credential, "settings": {"jc": count, "jmin": 10, "jmax": 100}} for count in (2, 8)])
        self.assertEqual([row["credential"]["amnezia"]["jc"] for row in rows], [2, 8])
        self.assertEqual(credential["amnezia"]["jc"], 6)
        self.assertEqual(rows[0]["credential"]["amnezia"]["s1"], 64)

    def test_invalid_advanced_ranges_are_rejected(self):
        for module, values in (("awg", {"jmin": 100, "jmax": 10}), ("hysteria2", {"initial_stream_receive_window": 2000, "max_stream_receive_window": 1000}), ("tuic", {"max_open_streams": -1}), ("tuic", {"certificate_fingerprint": "not-sha256"}), ("reality", {"tfo": "false"})):
            with self.subTest(module=module), self.assertRaises(manager.HTTPException):
                manager.validate_connection("transport-" + module, values)

    def test_mihomo_pins_the_applied_quic_certificate_without_changing_credentials_or_sni(self):
        certificate = ROOT / "api/resources/cloudflare-origin-pull-ca.pem"
        expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
        for module in ("tuic", "hysteria2"):
            credential = {"port": 18443, "uuid": "00000000-0000-4000-8000-000000000001", "password": "fixture", "up_mbps": 100, "down_mbps": 100, "sni": "cover.example", "skip_cert_verify": True}
            original = copy.deepcopy(credential)
            config = {"inbounds": [{"type": module, "tls": {"certificate_path": str(certificate)}}]}
            with self.subTest(module=module), patch.object(manager, "load_json", return_value=config):
                node = yaml.safe_load("proxies:\n" + "\n".join(manager.render_proxy("transport-" + module, credential, "QUIC")))["proxies"][0]
            self.assertEqual(node["fingerprint"], expected)
            self.assertEqual(node["sni"], "cover.example")
            self.assertEqual(credential, original)

    def test_explicit_quic_trust_choices_are_preserved_and_bad_applied_certificates_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_certificate = Path(directory) / "bad.pem"
            bad_certificate.write_text("not a certificate")
            config = {"inbounds": [{"type": "tuic", "tls": {"certificate_path": str(bad_certificate)}}]}
            with patch.object(manager, "load_json", return_value=config):
                with self.assertRaises(manager.HTTPException) as raised:
                    manager.mihomo_quic_certificate("transport-tuic", {"skip_cert_verify": True})
                self.assertEqual(raised.exception.status_code, 409)
                for credential in ({"skip_cert_verify": False}, {"skip_cert_verify": True, "certificate_fingerprint": "ab" * 32}):
                    self.assertIs(manager.mihomo_quic_certificate("transport-tuic", credential), credential)
