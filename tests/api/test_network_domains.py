import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class NetworkDomainsTests(unittest.TestCase):
    def test_shared_panel_and_cdn_domain_retains_both_roles(self):
        with patch.object(api, 'PUBLIC_DOMAIN', 'shared.example.com'), patch.object(api, 'VLESS_CDN_DOMAIN', ''), patch.object(api.cdn_security, 'read_routes', return_value=[{'domain': 'shared.example.com'}]), patch.object(api.socket, 'getaddrinfo', return_value=[]), patch.object(api, 'run', return_value=''):
            domains = api.network_status()['domains']
        self.assertEqual(len(domains), 1)
        self.assertEqual(domains[0]['role'], 'panel, VLESS CDN')

    def test_gateway_domains_are_visible_without_global_cdn_setting(self):
        routes = [
            {"domain": "cdn.example.com", "cloudflare": True},
            {"domain": "cdn.example.com", "cloudflare": True},
            {"domain": "tls.example.com", "cloudflare": False},
        ]
        with patch.object(api, "PUBLIC_DOMAIN", "panel.example.com"), patch.object(api, "VLESS_CDN_DOMAIN", ""), patch.object(api.cdn_security, "read_routes", return_value=routes), patch.object(api.socket, "getaddrinfo", return_value=[]), patch.object(api, "run", return_value=""):
            domains = api.network_status()["domains"]
        self.assertEqual([(d["value"], d["role"], d["source"]) for d in domains], [
            ("panel.example.com", "panel", "environment"),
            ("cdn.example.com", "VLESS CDN", "gateway"),
            ("tls.example.com", "VLESS TLS", "gateway"),
        ])

    def test_transport_endpoints_are_saved_and_exposed_as_network_domains(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            endpoint_file = data_dir / "network-endpoints.json"
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", endpoint_file):
                saved = api.write_network_endpoint_settings({
                    "cdn_domain": "CDN.Example.com", "tls_relay_domain": "tls.example.com", "udp_relay_domain": "udp.example.com",
                })
                self.assertEqual(saved["cdn_domain"], "cdn.example.com")
                self.assertEqual(json.loads(endpoint_file.read_text())["tls_relay_domain"], "tls.example.com")
                self.assertEqual(api.read_network_endpoint_settings()["udp_relay_domain"], "udp.example.com")
                with patch.object(api, "network_status", return_value={"transport_endpoints": saved}):
                    response = api.update_network_endpoints(api.NetworkEndpointSettings(**saved), None)
                self.assertEqual(response["transport_endpoints"], saved)
                with self.assertRaises(api.HTTPException):
                    api.update_network_endpoints(api.NetworkEndpointSettings(cdn_domain="not a domain"), None)

    def test_protected_channel_mode_requires_matching_endpoint_and_is_exposed_to_channel_settings(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", data_dir / "network-endpoints.json"), patch.object(api, "PROTECTED_CHANNELS_FILE", data_dir / "protected-channels.json"):
                api.write_network_endpoint_settings({"cdn_domain": "", "tls_relay_domain": "", "udp_relay_domain": "udp.example.com"})
                api.save_channel_mode("wg", "udp_relay")
                self.assertEqual(api.channel_mode_for("wg"), "udp_relay")
                self.assertEqual(api.channel_mode_endpoint("wg", "198.51.100.1"), ("udp.example.com", "udp_relay"))
                self.assertEqual(api.editable_protocol_settings("wg", {})[0]["key"], "channel_mode")
                with self.assertRaises(api.HTTPException):
                    api.save_channel_mode("trojan", "udp_relay")
