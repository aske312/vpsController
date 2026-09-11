"""Client format selection and sing-box TLS fragmentation."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tests.api.support import manager


class ClientOptionsTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "id": "profile", "name": "Profile", "common_device_id": "common",
            "devices": [{"id": device, "name": device, "routing": {}} for device in ("common", "phone")],
            "connections": [{"id": device, "component": "transport-reality", "device_id": device,
                             "settings": {"route_mode": "cdn"}, "credential": {"uuid": "00000000-0000-4000-8000-000000000001",
                             "cdn_enabled": True, "cdn_domain": "example.com", "cdn_transport": "xhttp", "cdn_path": "/test"}}
                            for device in ("common", "phone")],
        }
        self.enterContext(patch.object(manager, "routing_settings", return_value={}))
        self.enterContext(patch.object(manager, "dns_settings", return_value={"enhanced_mode": "fake-ip", "nameserver": "https://dns.google/dns-query", "fallback": "https://cloudflare-dns.com/dns-query"}))
        self.enterContext(patch.object(manager, "profile_rules", return_value=[]))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="192.0.2.1"))

    def test_default_mihomo_and_device_scoped_singbox_export(self):
        self.assertEqual(manager.render_client_profile(self.profile, "common")[1], "yaml")
        self.profile["devices"][1]["routing"] = {"client_config_format": "singbox", "tunnel_fragment": True}
        data, extension = manager.render_client_profile(self.profile, "phone")
        self.assertEqual(extension, "json")
        config = json.loads(data)
        tls = config["outbounds"][0]["tls"]
        self.assertTrue(tls["fragment"])
        self.assertTrue(tls["record_fragment"])
        self.assertEqual(manager.render_client_profile(self.profile, "common")[1], "yaml")

    def test_validation_rejects_unknown_format_and_invalid_fragment_ranges(self):
        for key, value in (("client_config_format", "xray"), ("client_config_format", "unknown"),
                           ("tunnel_fragment", "true"), ("tunnel_fragment_size", "200-100"), ("tunnel_fragment_interval", "1001")):
            with self.subTest(key=key, value=value), self.assertRaises(manager.HTTPException):
                manager.validate_routing({key: value}, current={})

    def test_singbox_rejects_amnezia_wireguard_until_supported(self):
        profile = deepcopy(self.profile)
        profile["devices"][1]["routing"] = {"client_config_format": "singbox"}
        profile["connections"].append({"id": "awg", "component": "transport-awg", "device_id": "phone", "credential": {}})
        with self.assertRaises(manager.HTTPException) as caught:
            manager.render_client_profile(profile, "phone")
        self.assertEqual(caught.exception.status_code, 422)

    def test_requested_format_overrides_device_default(self):
        data, extension = manager.render_client_profile(self.profile, "phone", "singbox")
        self.assertEqual(extension, "json")
        self.assertIn('"outbounds"', data)


if __name__ == "__main__":
    unittest.main()
