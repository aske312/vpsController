"""Public response and export checks; no server or real credentials required."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

try:
    import fcntl
except ImportError:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.flock = lambda *_: None
    sys.modules["fcntl"] = fcntl

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load_module("privacy_api", "api/main.py")
manager = load_module("privacy_manager", "protocol-images/mihomo/manager.py")


class PrivacyTests(unittest.TestCase):
    def test_export_filename_is_stable_per_profile_and_contains_no_secrets(self):
        profile = {"id": "random-profile-a", "name": "VLESS personal", "subscription_token": "secret-token"}
        filename = manager.profile_export_filename(profile)
        self.assertRegex(filename, r"^[0-9a-f]{24}\.yaml$")
        self.assertEqual(filename, manager.profile_export_filename({**profile, "name": "renamed", "subscription_token": "rotated-token"}))
        self.assertNotEqual(filename, manager.profile_export_filename({"id": "random-profile-b"}))

    def test_download_and_profile_metadata_use_same_filename(self):
        profile = {"id": "test", "common_device_id": "common", "devices": [{"id": "common"}]}
        with patch.object(manager, "profiles", return_value=[profile]), patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "render_profile", return_value="proxies: []\n"), patch.object(manager, "validate_rendered_profile"):
            response = manager.profile_config("test")
            filename = manager.profile_response(profile)["export_filename"]
            self.assertEqual(response.headers["content-disposition"], f'attachment; filename="{filename}"')
            self.assertEqual(response.body, b"proxies: []\n")

    def test_certificate_identity_matches_new_and_existing_certificates(self):
        for identity in ("endpoint.internal", "tuic.local", "trojan.local", "hysteria2.local"):
            with patch.object(api, "run", return_value=f"X509v3 Subject Alternative Name:\n    DNS:{identity}"):
                self.assertEqual(api.certificate_server_name(Path("server.crt")), identity)
        with patch.object(api, "run", return_value=""):
            with self.assertRaises(api.HTTPException):
                api.certificate_server_name(Path("server.crt"))

    def test_public_api_does_not_disclose_identity_or_schema(self):
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        with patch.object(api, "ADMIN_USER", "private-admin"), patch.object(api, "ADMIN_PASSWORD", "secret"):
            self.assertEqual(client.get("/api/health").json(), {"ok": True})
            self.assertEqual(client.get("/api/auth/status").json(), {"configured": True})
        for path in ("/openapi.json", "/docs", "/redoc"):
            self.assertEqual(client.get(path).status_code, 404)
        self.assertEqual(client.get("/api/clients").status_code, 401)

    def test_export_aliases_are_neutral_and_references_resolve(self):
        credential = {"port": 51820, "ip": "10.0.0.2/32", "private_key": "test-private", "server_public_key": "test-public", "mtu": 1280}
        profile = {"common_device_id": "common", "connections": [
            {"component": "transport-wg", "device_id": "common", "name": name, "credential": credential}
            for name in ("WireGuard private", "AWG backup")
        ]}
        dns = {"enhanced_mode": "fake-ip", "nameserver": "1.1.1.1", "fallback": "1.0.0.1"}
        with patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "public_endpoint", return_value="example.invalid"), patch.object(manager, "profile_rules", return_value=[]), patch.object(manager, "dns_settings", return_value=dns):
            config = yaml.safe_load(manager.render_profile(profile))
        self.assertEqual([proxy["name"] for proxy in config["proxies"]], ["Connection 1", "Connection 2"])
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["Connection 1", "Connection 2"])
        self.assertEqual(config["proxies"][0]["type"], "wireguard")
        self.assertEqual(config["proxies"][0]["private-key"], "test-private")

    def test_subscription_alias_preserves_token_checks_and_legacy_urls(self):
        from fastapi.testclient import TestClient
        client = TestClient(manager.app)
        profile = {"id": "test", "subscription_token": "test-token", "common_device_id": "common"}
        with patch.object(manager, "profiles", return_value=[profile]), patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "record_common_subscription_access", return_value=profile), patch.object(manager, "render_profile", return_value="proxies: []\n"), patch.object(manager, "validate_rendered_profile"):
            for path in ("/s/test-token", "/api/mihomo/subscriptions/test-token"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-disposition"], f'inline; filename="{manager.profile_export_filename(profile)}"')
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("x-profile-device", response.headers)
            self.assertEqual(client.get("/s/incorrect-token").status_code, 404)


if __name__ == "__main__":
    unittest.main()
