import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from tests.api.support import api, manager
import application_operation as operations


class CoreAuthTests(unittest.TestCase):
    def test_every_admin_route_rejects_missing_credentials_before_execution(self):
        import re
        totals = {"core": 0, "mihomo": 0}
        for name, module in (("core", api), ("mihomo", manager)):
            client = TestClient(module.app)
            for route in module.app.routes:
                if not route.path.startswith("/api/") or route.path in ("/api/health", "/api/auth/status") or "/subscriptions/" in route.path:
                    continue
                path = re.sub(r"\{[^}]+\}", "fixture", route.path)
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    with self.subTest(application=name, method=method, route=route.path):
                        response = client.request(method, path, json={} if method != "GET" else None)
                        self.assertEqual(response.status_code, 401)
                        totals[name] += 1
        self.assertGreater(totals["core"], 60)
        self.assertGreater(totals["mihomo"], 20)

    def test_non_ascii_credentials_and_current_password_fail_cleanly(self):
        with patch.object(api, "ADMIN_USER", "fixture"), patch.object(api, "ADMIN_PASSWORD", "previous"):
            for user, password in (("другой", "previous"), ("fixture", "неверный")):
                with self.assertRaises(HTTPException) as error:
                    api.require_token(HTTPBasicCredentials(username=user, password=password))
                self.assertEqual(error.exception.status_code, 401)
            with self.assertRaises(HTTPException) as error:
                api.change_admin_password(api.AdminPasswordChange(current_password="неверный", new_password="Fixture-New-12345!", confirm_password="Fixture-New-12345!"))
            self.assertEqual(error.exception.status_code, 400)

    def test_failed_password_replace_preserves_disk_and_active_credentials(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(api, "ADMIN_PASSWORD", "previous"):
            env = Path(temporary) / "environment"
            original = "ADMIN_PASSWORD=previous\nOTHER=value\n"
            env.write_text(original)
            with patch.object(api, "ENV_FILE", env), patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(HTTPException):
                    api.change_admin_password(api.AdminPasswordChange(current_password="previous", new_password="Fixture-New-12345!", confirm_password="Fixture-New-12345!"))
            self.assertEqual(env.read_text(), original)
            self.assertEqual(api.ADMIN_PASSWORD, "previous")
            self.assertEqual(list(Path(temporary).iterdir()), [env])

    def test_route_and_password_mutations_cannot_overlap_a_background_worker(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        with tempfile.TemporaryDirectory() as temporary, patch.object(api, "DATA_DIR", Path(temporary)), \
             patch.object(api, "write_network_endpoint_settings", side_effect=AssertionError("Must not write")):
            operations.atomic_json(Path(temporary) / "application-action.json", {"state": "running"})
            client = TestClient(api.app)
            for method, path in (("PUT", "/api/network/endpoints"), ("DELETE", "/api/network/endpoints/cdn/example.invalid"), ("PUT", "/api/security/admin-password")):
                self.assertEqual(client.request(method, path, json={}).status_code, 409)
