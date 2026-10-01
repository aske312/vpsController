import json
import io
import sys
from contextlib import redirect_stdout
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api, manager
from component_registry import ComponentRegistry, RegistryError, fingerprint, inventory


class ComponentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.registry = ComponentRegistry(self.root / "data")
        self.config = self.root / "config.json"
        self.config.write_text('{"private_key":"fixture-secret"}', encoding="utf-8")
        self.context = {"id": "wg", "version": "1"}
        self.checksum = fingerprint(inventory([self.config]), self.context)

    def test_no_implicit_ownership_and_adoption_backs_up_without_changing_config(self):
        self.assertEqual(self.registry.management("wg")["state"], "unmanaged")
        with self.assertRaises(RegistryError):
            self.registry.require_managed("wg")
        before = self.config.read_bytes()
        with patch("subprocess.run", side_effect=AssertionError("Adoption must not run commands")):
            result = self.registry.adopt("wg", [self.config], self.context, self.checksum)
        self.assertEqual(result["state"], "managed")
        self.assertEqual(self.config.read_bytes(), before)
        backup = self.registry.data_dir / "component-backups" / "wg" / result["backup_id"]
        with tarfile.open(backup / "configuration.tar") as archive:
            self.assertEqual(archive.extractfile("files/0").read(), before)
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertEqual(self.registry.adopt("wg", [self.config], self.context, "0" * 64), result)
        self.assertEqual(len(list(backup.parent.iterdir())), 1)

    def test_install_admission_distinguishes_fresh_from_retained_or_owned_data(self):
        import component_registry
        manifest = self.root / "images" / "wg" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"id": "wg", "service": "wg-quick@wg0.service"}))
        argv = ["registry", "check-install", "--manifest", str(manifest), "--data-dir", str(self.registry.data_dir)]
        with patch.object(sys, "argv", argv), patch("component_state.image_files", return_value=[False]), \
             patch("service_state.observe_service", return_value={"active": False, "unit_present": False}):
            for owned in (False, True):
                if owned:
                    self.registry.write_receipt("wg", "installed", retained=True)
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(component_registry.main(), 0)
                self.assertEqual(output.getvalue().strip(), "existing" if owned else "fresh")

    def test_changed_config_or_failed_backup_never_grants_control(self):
        self.config.write_text("changed")
        with self.assertRaises(RegistryError):
            self.registry.adopt("wg", [self.config], self.context, self.checksum)
        self.checksum = fingerprint(inventory([self.config]), self.context)
        with patch("component_registry.tarfile.open", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.registry.adopt("wg", [self.config], self.context, self.checksum)
        self.assertIsNone(self.registry.receipt("wg"))
        self.assertEqual(self.config.read_text(), "changed")

    def test_corrupt_receipt_is_unknown_and_cannot_be_used_as_authorization(self):
        path = self.registry.receipt_path("wg")
        path.parent.mkdir(parents=True)
        for data in ("broken", "{}", '{"id":"wg","schema":1,"origin":"guessed"}'):
            path.write_text(data)
            self.assertEqual(self.registry.management("wg")["state"], "unknown")
            with self.assertRaises(RegistryError):
                self.registry.require_managed("wg")
        with self.assertRaises(RegistryError):
            self.registry.receipt_path("../other")

    def test_api_rejects_direct_and_indirect_mutations_before_running_commands(self):
        client = TestClient(api.app)
        self.assertEqual(client.post("/api/protocol-images/wg/adoption", json={"fingerprint": self.checksum, "confirmed": True}).status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        image = {"component_state": {"installation": {"state": "installed"}}}
        with patch.object(api, "DATA_DIR", self.registry.data_dir), patch.object(api, "protocol_image_manifests", return_value={"wg": image}), \
             patch.object(api, "managed_services", return_value={"wg": {"component_id": "wg"}}), \
             patch.object(api, "read_clients", return_value=[{"id": "one", "protocol": "wg"}]), \
             patch.object(api, "run", side_effect=AssertionError("Unauthorized mutation reached a command")):
            cases = [("POST", "/api/protocol-images/wg/install", {}), ("POST", "/api/protocol-images/wg/update", {}),
                     ("DELETE", "/api/protocol-images/wg", None), ("PATCH", "/api/protocols/wg/settings", {}),
                     ("POST", "/api/protocols/wg/restart", None), ("POST", "/api/clients", {"name": "fixture", "protocol": "wg"}),
                     ("DELETE", "/api/clients/one", None), ("POST", "/api/services/wg/action", {"action": "restart"}),
                     ("PUT", "/api/dns/settings", {"selected_id": "cloudflare"})]
            for method, path, payload in cases:
                with self.subTest(path=path):
                    self.assertEqual(client.request(method, path, json=payload).status_code, 409)

    def test_mihomo_readonly_does_not_reconfigure_on_observation_or_provision_device(self):
        with patch.object(manager, "DATA_ROOT", self.registry.data_dir / "mihomo"), \
             patch.object(manager, "run", side_effect=AssertionError("Observation restarted a service")), \
             patch.object(manager, "atomic_json", side_effect=AssertionError("Observation wrote configuration")):
            manager.ensure_policy_settings()
            manager.ensure_shadowsocks_protection()
            manager.ensure_reality_telemetry()
            manager.ensure_quic_telemetry("transport-tuic")
            manager.cleanup_profile_transitions()
            with self.assertRaises(api.HTTPException) as denied:
                manager.require_mihomo_management()
            self.assertEqual(denied.exception.status_code, 409)

    def test_adoption_api_requires_current_preview_and_explicit_confirmation(self):
        client = TestClient(api.app)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        plan = {"compatible": True, "blockers": []}
        with patch.object(api, "DATA_DIR", self.registry.data_dir), patch.object(api, "adoption_plan", return_value=(plan, [self.config], self.context)):
            self.assertEqual(client.post("/api/protocol-images/wg/adoption", json={"fingerprint": self.checksum}).status_code, 422)
            self.assertEqual(client.post("/api/protocol-images/wg/adoption", json={"fingerprint": "0" * 64, "confirmed": True}).status_code, 409)
            result = client.post("/api/protocol-images/wg/adoption", json={"fingerprint": self.checksum, "confirmed": True})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["state"], "managed")

    def test_retained_or_corrupted_receipt_cannot_enable_mutations(self):
        self.registry.write_receipt("wg", "installed", retained=True)
        with self.assertRaises(RegistryError):
            self.registry.require_managed("wg")
        self.registry.require_managed("wg", allow_retained=True)
        self.registry.write_receipt("wg", "installed", retained="false")
        self.assertEqual(self.registry.management("wg")["state"], "unknown")

    def test_shared_routes_reject_external_consumer_before_any_write(self):
        environment = self.root / "reality.env"
        environment.write_text("CDN_ENABLED=yes\nCDN_DOMAIN=legacy.example.com\n")
        with patch.object(api, "DATA_DIR", self.registry.data_dir), patch.object(api, "VLESS_ENV", environment), \
             patch.object(api, "MIHOMO_VLESS_CDN_ROUTES", self.root / "routes"), \
             patch.object(api, "remember_network_endpoint_retirement", side_effect=AssertionError("Unexpected write")), \
             patch.object(api.cdn_operation, "start", side_effect=AssertionError("Unexpected operation")):
            with self.assertRaises(api.HTTPException) as denied:
                api.delete_network_endpoint("cdn", "legacy.example.com")
            self.assertEqual(denied.exception.status_code, 409)
            with self.assertRaises(api.HTTPException) as denied:
                api.update_cdn_security(api.CdnSecuritySettings(authenticated_origin_pulls=False, operation_id="12345678123412341234123456789012"))
            self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(environment.read_text(), "CDN_ENABLED=yes\nCDN_DOMAIN=legacy.example.com\n")

    def test_mihomo_http_mutations_require_ownership_even_with_valid_login(self):
        manager.app.dependency_overrides[manager.auth_required] = lambda: None
        self.addCleanup(manager.app.dependency_overrides.clear)
        client = TestClient(manager.app)
        with patch.object(manager, "DATA_ROOT", self.registry.data_dir / "mihomo"), \
             patch.object(manager, "run", side_effect=AssertionError("Unauthorized system command")):
            for path in ("/api/mihomo/profiles", "/api/mihomo/modules/transport-wg/install"):
                response = client.post(path, json={})
                self.assertEqual(response.status_code, 409, (path, response.text))

    def test_purge_only_retained_owned_data_and_matching_clients(self):
        self.registry.write_receipt("wg", "installed")
        with self.assertRaises(RegistryError):
            self.registry.purge("wg", [self.config], lambda: None)
        self.assertTrue(self.config.exists())
        self.registry.write_receipt("wg", "installed", retained=True)
        clients = self.registry.data_dir / "clients.json"
        clients.write_text(json.dumps([{"protocol": "wg", "id": "one"}, {"protocol": "awg", "id": "keep"}]))
        shared = self.root / "shared.json"
        shared.write_text("preserve")
        def running():
            raise RegistryError("Runtime active")
        with self.assertRaises(RegistryError):
            self.registry.purge("wg", [self.config], running)
        self.assertTrue(self.config.exists())
        self.registry.purge("wg", [self.config], lambda: None)
        self.assertFalse(self.config.exists())
        self.assertEqual(shared.read_text(), "preserve")
        self.assertEqual(json.loads(clients.read_text()), [{"protocol": "awg", "id": "keep"}])
        self.assertEqual(self.registry.management("wg")["state"], "unmanaged")

    def test_purge_rejects_damaged_clients_before_removing_files(self):
        self.registry.write_receipt("wg", "installed", retained=True)
        (self.registry.data_dir / "clients.json").write_text("broken")
        with self.assertRaises(ValueError):
            self.registry.purge("wg", [self.config], lambda: None)
        self.assertTrue(self.config.exists())
        self.assertTrue(self.registry.management("wg")["retained"])
