import unittest
from unittest.mock import patch

from tests.api.support import api


class NetworkDomainsTests(unittest.TestCase):
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
