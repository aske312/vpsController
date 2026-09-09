"""Behavioral checks for retained YAML credentials and persisted expiration."""
from contextlib import ExitStack
from copy import deepcopy
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.api.support import manager


class ProfileTransitionTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.config_root = self.root / "config"
        self.config = self.config_root / "reality/config.json"
        self.config.parent.mkdir(parents=True)
        self.routes = self.config.parent / "routes"
        self.routes.mkdir()
        self.profile_file = self.root / "profiles.json"
        self.clock = 1000.0
        credential = {"uuid": "old-id", "encryption": "", "direct_tag": "mihomo-vless-original", "port": 13000,
                      "cdn_enabled": True, "cdn_domain": "example.com", "cdn_port": 13001, "cdn_path": "/old", "cdn_transport": "websocket",
                      "tls_enabled": True, "tls_domain": "example.com", "tls_port": 13002, "tls_path": "/old-tls", "tls_transport": "grpc"}
        self.profile = {"id": "profile", "name": "Profile", "last_operation_id": "saved-id", "common_device_id": "common",
                        "devices": [{"id": "common", "name": "Common", "routing": {"tunnel_privacy": True}}],
                        "connections": [{"id": "channel", "component": "transport-reality", "device_id": "common", "settings": {"port": 13000}, "credential": credential}]}
        self.original = {"inbounds": [{"protocol": "vless", "listen": "127.0.0.1", "port": 13000 + i,
                                     "tag": f"mihomo-vless-{prefix}original",
                                     "settings": {"clients": [{"id": "old-id", "email": "mihomo-profile-channel"}], "decryption": "none"},
                                     "streamSettings": stream}
                                    for i, (prefix, stream) in enumerate([
                                        ("", {"network": "tcp", "security": "none"}),
                                        ("cdn-", {"network": "websocket", "wsSettings": {"path": "/old"}}),
                                        ("tls-", {"network": "grpc", "grpcSettings": {"serviceName": "old-tls"}})])]}
        self.config.write_text(json.dumps(self.original))
        self.profile_file.write_text(json.dumps([self.profile]))
        for key, value in {"CONFIG_ROOT": self.config_root, "PROFILE_FILE": self.profile_file,
                           "VLESS_CDN_ROUTE_ROOT": self.routes, "ROUTING_SETTINGS_FILE": self.root / "routing.json"}.items():
            stack.enter_context(patch.object(manager, key, value))
        stack.enter_context(patch.object(manager.time, "time", side_effect=lambda: self.clock))
        stack.enter_context(patch.object(manager, "vless_encryption_pair", return_value=("private", "public")))
        stack.enter_context(patch.object(manager, "render_profile", return_value="yaml"))
        stack.enter_context(patch.object(manager, "validate_rendered_profile"))
        stack.enter_context(patch.object(manager, "systemctl_active", return_value=False))
        stack.enter_context(patch.object(manager, "module_is_installed", return_value=False))
        stack.enter_context(patch.object(manager, "write_action"))
        stack.enter_context(patch.object(manager, "apply_reality_config", side_effect=lambda path, value, **_: path.write_text(json.dumps(value))))
        self.apply_runtime = stack.enter_context(patch.object(manager, "apply_batched_reality_runtime"))
        for kind, prefix in (("cdn", ""), ("tls", "tls-")):
            manager.write_mihomo_vless_cdn(prefix + "original", True, credential[kind + "_domain"], credential[kind + "_path"], credential[kind + "_port"], credential[kind + "_transport"], rebuild=False)

    def transition(self):
        manager.reconcile_profile_encryption(self.profile)
        manager.save_profiles([self.profile])

    def test_old_and_new_routes_coexist_and_reconciliation_preserves_both(self):
        original_routes = {p.name: p.read_bytes() for p in self.routes.glob("*.json")}
        self.transition()
        config = json.loads(self.config.read_text())
        self.assertEqual(config["inbounds"][:3], self.original["inbounds"])
        self.assertEqual(len({row["port"] for row in config["inbounds"]}), 6)
        self.assertTrue(all(row["settings"]["decryption"] == "private" for row in config["inbounds"][3:]))
        for name, content in original_routes.items():
            self.assertEqual((self.routes / name).read_bytes(), content)
        self.assertEqual(len(list(self.routes.glob("*.json"))), 4)
        status = manager.profile_response(self.profile)
        self.assertNotIn("retiring_connections", status)
        self.assertEqual(status["protection_status"]["common"]["previous_valid_until"], 1900)
        report = manager.reconciliation_report()
        self.assertEqual(report["reality"]["orphan_tags"], [])
        self.assertEqual(report["reality"]["missing_tags"], [])
        self.assertEqual(report["reality"]["orphan_routes"], [])
        self.apply_runtime.assert_called_once()

    def test_toggle_back_reuses_generation_without_extending_unchanged_saves(self):
        self.transition()
        first = deepcopy(self.profile["connections"][0]["credential"])
        for enabled in (False, True, False, True):
            self.clock += 10
            self.profile["devices"][0]["routing"]["tunnel_privacy"] = enabled
            manager.reconcile_profile_encryption(self.profile)
            self.assertEqual(len(self.profile["retiring_connections"]), 1)
            self.assertEqual(len(json.loads(self.config.read_text())["inbounds"]), 6)
        self.assertEqual(self.profile["connections"][0]["credential"], first)
        deadline = self.profile["retiring_connections"][0]["expires_at"]
        self.clock += 10
        manager.reconcile_profile_encryption(self.profile)
        self.assertEqual(self.profile["retiring_connections"][0]["expires_at"], deadline)
        self.apply_runtime.assert_called_once()

    def test_expiration_from_persisted_state_removes_only_old_generation(self):
        self.transition()
        manager.cleanup_profile_transitions()
        self.apply_runtime.assert_called_once()
        self.clock = 1900
        manager.cleanup_profile_transitions()
        stored = manager.profiles()[0]
        self.assertEqual(stored["retiring_connections"], [])
        self.assertEqual(stored["last_operation_id"], "saved-id")
        config = json.loads(self.config.read_text())
        self.assertEqual(len(config["inbounds"]), 3)
        self.assertTrue(all(row["settings"]["decryption"] == "private" for row in config["inbounds"]))
        self.assertFalse((self.routes / "original.json").exists())
        self.assertFalse((self.routes / "tls-original.json").exists())
        self.assertEqual(len(list(self.routes.glob("*.json"))), 2)
        manager.cleanup_profile_transitions()
        self.assertEqual(self.apply_runtime.call_count, 2)

    def test_cleanup_failure_restores_old_routes_and_persisted_deadline(self):
        self.transition()
        before = self.profile_file.read_bytes(), self.config.read_bytes()
        self.clock = 2000
        self.apply_runtime.side_effect = RuntimeError("injected failure")
        with self.assertRaisesRegex(RuntimeError, "injected"):
            manager.cleanup_profile_transitions()
        self.assertEqual((self.profile_file.read_bytes(), self.config.read_bytes()), before)
        self.assertTrue((self.routes / "original.json").exists())

    def test_delete_revokes_both_generations(self):
        self.transition()
        manager.delete_profile("profile")
        self.assertEqual(manager.profiles(), [])
        self.assertEqual(json.loads(self.config.read_text())["inbounds"], [])
        self.assertEqual(list(self.routes.glob("*.json")), [])

    def test_removing_connection_revokes_retained_credentials(self):
        self.transition()
        manager.update_profile("profile", manager.ProfileUpdate(connections=[]))
        self.assertEqual(manager.profiles()[0]["retiring_connections"], [])
        self.assertEqual(json.loads(self.config.read_text())["inbounds"], [])

    def test_failed_profile_save_restores_both_new_routes_and_old_settings(self):
        before = self.profile_file.read_bytes(), self.config.read_bytes()
        self.apply_runtime.side_effect = RuntimeError("injected failure")
        with self.assertRaisesRegex(RuntimeError, "injected"):
            manager.update_profile("profile", manager.ProfileUpdate(name="Profile"))
        self.assertEqual((self.profile_file.read_bytes(), self.config.read_bytes()), before)
        self.assertEqual({p.stem for p in self.routes.glob("*.json")}, {"original", "tls-original"})

    def test_manager_startup_resumes_cleanup_without_a_browser_request(self):
        called = threading.Event()

        async def lifecycle():
            async with manager.manager_lifespan(manager.app):
                self.assertTrue(await asyncio.to_thread(called.wait, 2))

        with patch.object(manager, "cleanup_profile_transitions", side_effect=called.set):
            asyncio.run(lifecycle())
