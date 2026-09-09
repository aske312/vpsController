"""Tunnel privacy regression tests; optional real-core tests use local binaries."""
import http.server
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port


class TunnelPrivacyTests(unittest.TestCase):
    def test_saved_legacy_flag_is_repaired_while_previous_yaml_remains_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_root = root / "config"
            config = config_root / "reality/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"inbounds": [{"protocol": "vless", "port": 12345, "settings": {"clients": [{"id": "identity"}], "decryption": "none"}}]}))
            profile_file = root / "profiles.json"
            item = {"id": "profile", "common_device_id": "common", "devices": [{"id": "common", "name": "Profile", "routing": {}}, {"id": "device", "name": "Device", "routing": {"tunnel_privacy": True, "tunnel_ech": False}}], "connections": [{"id": "channel", "component": "transport-reality", "device_id": "device", "credential": {"uuid": "identity"}}]}
            profile_file.write_text(json.dumps([item]))
            status = manager.profile_response(item)["protection_status"]
            self.assertTrue(status["device"]["encryption_pending"])
            self.assertFalse(status["common"]["encryption_pending"])
            with patch.object(manager, "CONFIG_ROOT", config_root), patch.object(manager, "PROFILE_FILE", profile_file), patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"), patch.object(manager, "profiles", side_effect=lambda: json.loads(profile_file.read_text())), patch.object(manager, "systemctl_active", return_value=False), patch.object(manager, "write_action"), patch.object(manager, "vless_encryption_pair", return_value=("private", "public")), patch.object(manager, "render_profile", return_value="yaml"), patch.object(manager, "validate_rendered_profile"), patch.object(manager, "apply_batched_reality_runtime"), patch.object(manager, "apply_reality_config", side_effect=lambda path, value, **_: path.write_text(json.dumps(value))), patch.object(manager, "provision") as provision:
                updated = manager.update_profile("profile", manager.ProfileUpdate(name="Profile"))
            provision.assert_not_called()
            self.assertFalse(updated["protection_status"]["device"]["encryption_pending"])
            self.assertNotEqual(updated["connections"][0]["credential"]["uuid"], "identity")
            self.assertEqual(updated["connections"][0]["credential"]["encryption"], "public")
            self.assertNotIn("retiring_connections", updated)
            self.assertEqual(updated["protection_status"]["device"]["previous_connections"], 1)
            inbound = json.loads(config.read_text())["inbounds"][0]
            self.assertEqual(inbound["port"], 12345)
            self.assertEqual(inbound["settings"]["decryption"], "none")
            self.assertEqual(json.loads(config.read_text())["inbounds"][1]["settings"]["decryption"], "private")
            self.assertFalse(updated["devices"][0]["routing"].get("tunnel_privacy", False))
            self.assertFalse(updated["devices"][1]["routing"]["tunnel_ech"])

    def test_device_only_update_rolls_back_profile_and_runtime_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_root = root / "config"
            config = config_root / "reality/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"inbounds": [{"protocol": "vless", "settings": {"clients": [{"id": "identity"}], "decryption": "none"}}]}))
            profile_file = root / "profiles.json"
            profile_file.write_text(json.dumps([{"id": "profile", "common_device_id": "one", "devices": [{"id": "one", "name": "Device", "routing": {}}], "connections": [{"id": "channel", "component": "transport-reality", "device_id": "one", "credential": {"uuid": "identity"}}]}]))
            original_profile, original_config = profile_file.read_bytes(), config.read_bytes()
            def fail(path, value, **_):
                path.write_text(json.dumps(value))
                raise RuntimeError("injected runtime failure")
            with patch.object(manager, "CONFIG_ROOT", config_root), patch.object(manager, "PROFILE_FILE", profile_file), patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"), patch.object(manager, "profiles", side_effect=lambda: json.loads(profile_file.read_text())), patch.object(manager, "systemctl_active", return_value=False), patch.object(manager, "validate_routing", side_effect=lambda values, **_: values), patch.object(manager, "write_action"), patch.object(manager, "vless_encryption_pair", return_value=("private", "public")), patch.object(manager, "render_profile", return_value="yaml"), patch.object(manager, "validate_rendered_profile"), patch.object(manager, "apply_reality_config", side_effect=fail), patch.object(manager, "provision") as provision:
                payload = manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(id="one", name="Device", routing={"tunnel_privacy": True})])
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    manager.update_profile("profile", payload)
                provision.assert_not_called()
            self.assertEqual(profile_file.read_bytes(), original_profile)
            self.assertEqual(config.read_bytes(), original_config)

    def test_ech_and_encryption_are_independent_per_device(self):
        dns = {"enhanced_mode": "fake-ip", "nameserver": "https://dns.google/dns-query", "fallback": "https://cloudflare-dns.com/dns-query"}
        for encrypted in (False, True):
            for ech in (False, True):
                with self.subTest(encrypted=encrypted, ech=ech):
                    profile = {"common_device_id": "device", "routing": {"tunnel_privacy": not encrypted, "tunnel_ech": not ech},
                               "devices": [{"id": "device", "routing": {"tunnel_privacy": encrypted, "tunnel_ech": ech}}],
                               "connections": [{"component": "transport-reality", "device_id": "device", "settings": {"route_mode": "cdn"}, "credential": {"uuid": str(uuid.uuid4()), "encryption": "public" if encrypted else "", "cdn_enabled": True, "route_mode": "cdn", "cdn_domain": "example.com", "cdn_path": "/test"}}]}
                    with patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "routing_settings", return_value={}), patch.object(manager, "dns_settings", return_value=dns), patch.object(manager, "profile_rules", return_value=[]), patch.object(manager, "cdn_supports_ech", return_value=True):
                        proxy = yaml.safe_load(manager.render_profile(profile))["proxies"][0]
                    self.assertEqual(bool(proxy.get("encryption")), encrypted)
                    self.assertEqual(bool(proxy.get("ech-opts", {}).get("enable")), ech)

    def test_device_change_preserves_previous_listener_and_other_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "reality/config.json"
            path.parent.mkdir()
            original = {"inbounds": [{"protocol": "vless", "port": 11000 + i, "settings": {"clients": [{"id": identity}], "decryption": "none"}} for i, identity in enumerate(("one", "two"))]}
            path.write_text(json.dumps(original))
            profile = {"common_device_id": "one", "devices": [{"id": name, "routing": {"tunnel_privacy": name == "one"}} for name in ("one", "two")], "connections": [{"id": name, "component": "transport-reality", "device_id": name, "credential": {"uuid": name, "cdn_path": "/keep"}} for name in ("one", "two")]}
            with patch.object(manager, "CONFIG_ROOT", root), patch.object(manager, "vless_encryption_pair", return_value=("private", "public")), patch.object(manager, "render_profile", return_value="yaml"), patch.object(manager, "validate_rendered_profile"), patch.object(manager, "apply_batched_reality_runtime"), patch.object(manager, "apply_reality_config") as apply:
                manager.reconcile_profile_encryption(profile)
                candidate = apply.call_args.args[1]
                self.assertEqual(candidate["inbounds"][0], original["inbounds"][0])
                self.assertEqual(candidate["inbounds"][2]["settings"]["decryption"], "private")
                self.assertEqual(candidate["inbounds"][1], original["inbounds"][1])
                self.assertEqual(candidate["inbounds"][0]["port"], 11000)
                self.assertEqual(profile["retiring_connections"][0]["credential"]["uuid"], "one")
                self.assertNotEqual(profile["connections"][0]["credential"]["uuid"], "one")
                self.assertEqual(profile["connections"][0]["credential"]["cdn_path"], "/keep")
                manager.reconcile_profile_encryption(profile)
                self.assertEqual(apply.call_count, 1)

    def test_create_provisions_encryption_from_each_device(self):
        profile = {"routing": {"tunnel_privacy": True}, "devices": [{"id": "a", "routing": {}}, {"id": "b", "routing": {"tunnel_privacy": True}}]}
        definitions = [{"id": name, "device_id": name, "component": "transport-reality"} for name in ("a", "b")]
        with patch.object(manager, "provision", return_value={}) as provision, patch.object(manager, "apply_batched_reality_runtime"):
            manager.provision_connections("profile", definitions, profile=profile)
        self.assertEqual([call.kwargs["privacy_enabled"] for call in provision.call_args_list], [False, True])

    def test_vless_panel_route_is_local_only(self):
        config = {"routing": {"rules": []}, "outbounds": [{"protocol": "freedom", "tag": "direct"}]}
        manager.ensure_vless_panel_route(config)
        self.assertEqual(next(item for item in config["outbounds"] if item["tag"] == "panel-local")["settings"]["redirect"], "127.0.0.1:80")
        self.assertEqual(config["routing"]["rules"][0]["domain"], ["full:admin.312.net"])
        manager.ensure_vless_panel_route(config)
        self.assertEqual(sum(item.get("tag") == "panel-local" for item in config["outbounds"]), 1)

    def test_vless_panel_route_repairs_existing_redirect_before_private_network_block(self):
        block = {"type": "field", "ip": ["127.0.0.0/8", "10.0.0.0/8"], "outboundTag": "blocked"}
        direct = {"protocol": "freedom", "tag": "direct"}
        config = {"routing": {"rules": [block, {"type": "field", "domain": ["full:admin.312.net"], "outboundTag": "panel-local"}]},
                  "outbounds": [direct, {"tag": "panel-local", "protocol": "freedom", "settings": {"redirect": "127.0.0.1:8080"}}]}
        manager.ensure_vless_panel_route(config)
        self.assertEqual(config["outbounds"][1]["settings"]["redirect"], "127.0.0.1:80")
        self.assertEqual(config["outbounds"][0], direct)
        self.assertEqual(config["routing"]["rules"][0]["domain"], ["full:admin.312.net"])
        self.assertEqual(config["routing"]["rules"][1], block)
        repaired = json.dumps(config)
        manager.ensure_vless_panel_route(config)
        self.assertEqual(json.dumps(config), repaired)

    def test_global_toggle_preserves_channels_and_skips_other_protocols(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "reality/config.json"
            config_path.parent.mkdir()
            inbounds = [{"protocol": "vless", "port": 10443 + index, "settings": {"clients": [{"id": identity}], "decryption": "none"}} for index, identity in enumerate(("one", "two"))]
            config_path.write_text(json.dumps({"inbounds": inbounds}))
            connections = [{"component": "transport-reality", "device_id": identity, "settings": {"privacy_mode": "standard"}, "credential": {"uuid": identity, "cdn_path": "/keep-" + identity}} for identity in ("one", "two")]
            untouched = {"component": "transport-wg", "device_id": "one", "credential": {"private_key": "unchanged"}}
            connections.append(untouched)
            items = [{"common_device_id": "one", "connections": connections}]
            def apply(path, value):
                path.write_text(json.dumps(value))
            with patch.object(manager, "CONFIG_ROOT", root), patch.object(manager, "profiles", return_value=items), patch.object(manager, "vless_encryption_pair", return_value=("private", "public")) as pair, patch.object(manager, "apply_reality_config", side_effect=apply) as runtime, patch.object(manager, "render_profile", return_value="yaml") as render, patch.object(manager, "validate_rendered_profile"), patch.object(manager, "save_profiles"):
                manager.apply_tunnel_privacy(True)
                self.assertEqual(pair.call_count, 2)
                self.assertEqual(render.call_count, 2)
                self.assertEqual(runtime.call_count, 1)
                for index, connection in enumerate(connections[:2]):
                    self.assertEqual(connection["credential"]["encryption"], "public")
                    self.assertEqual(connection["credential"]["cdn_path"], "/keep-" + connection["credential"]["uuid"])
                    self.assertEqual(json.loads(config_path.read_text())["inbounds"][index]["port"], 10443 + index)
                self.assertEqual(untouched["credential"], {"private_key": "unchanged"})
                manager.apply_tunnel_privacy(True)
                self.assertEqual(pair.call_count, 2)
                self.assertEqual(runtime.call_count, 1)
                pair.return_value = ("none", "")
                manager.apply_tunnel_privacy(False)
                self.assertTrue(all(entry["settings"]["decryption"] == "none" for entry in json.loads(config_path.read_text())["inbounds"]))

    @unittest.skip("Privacy is profile-scoped")
    def test_failed_global_toggle_restores_setting_profiles_and_server_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config"
            config.mkdir()
            server = config / "config.json"
            profile = root / "profiles.json"
            routing = root / "settings/routing.json"
            routing.parent.mkdir()
            for path, content in ((server, "{}"), (profile, "[]"), (routing, '{"tunnel_privacy": false}')):
                path.write_text(content)
            def fail(_):
                server.write_text("changed")
                profile.write_text("changed")
                raise RuntimeError("validation failed")
            with patch.object(manager, "CONFIG_ROOT", config), patch.object(manager, "PROFILE_FILE", profile), patch.object(manager, "ROUTING_SETTINGS_FILE", routing), patch.object(manager, "routing_settings", return_value={"tunnel_privacy": False}), patch.object(manager, "validate_routing", return_value={"tunnel_privacy": True}), patch.object(manager, "systemctl_active", return_value=False), patch.object(manager, "apply_tunnel_privacy", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "validation failed"):
                    manager.patch_routing_settings(manager.ModuleSettingsPatch(values={"tunnel_privacy": True}))
            self.assertEqual(json.loads(routing.read_text()), {"tunnel_privacy": False})
            self.assertEqual(profile.read_text(), "[]")
            self.assertEqual(server.read_text(), "{}")

    def test_ech_capability_is_optional_and_cached(self):
        manager.ech_capability_cache.clear()
        with patch.object(manager.urllib.request, "urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps({"Status": 0, "Answer": [{"type": 65, "data": '1 . alpn="h2" ech="ABC123=="'}]}).encode()
            self.assertTrue(manager.cdn_supports_ech("supported.example"))
            self.assertTrue(manager.cdn_supports_ech("supported.example"))
            self.assertEqual(request.call_count, 1)
            request.side_effect = OSError("unavailable")
            self.assertFalse(manager.cdn_supports_ech("unsupported.example"))

    def test_ech_requires_encrypted_resolvers_and_preserves_direct_rules(self):
        profile = {"common_device_id": "common", "routing": {"direct_ru_sites": True}, "connections": [{"component": "transport-reality", "device_id": "common", "settings": {"route_mode": "cdn", "cdn_ech": True}, "credential": {"uuid": str(uuid.uuid4()), "cdn_enabled": True, "route_mode": "cdn", "cdn_domain": "example.com", "cdn_path": "/test"}}]}
        dns = {"enhanced_mode": "fake-ip", "nameserver": "https://cloudflare-dns.com/dns-query", "fallback": "https://dns.google/dns-query"}
        rules = ["DOMAIN-SUFFIX,example.ru,DIRECT"]
        profile["connections"][0]["credential"]["encryption"] = "public-key"
        profile["routing"]["tunnel_privacy"] = True
        profile["routing"]["tunnel_ech"] = True
        with patch.object(manager, "routing_settings", return_value={"tunnel_privacy": True}), patch.object(manager, "cdn_supports_ech", return_value=True), patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "dns_settings", return_value=dns), patch.object(manager, "profile_rules", return_value=rules):
            config = yaml.safe_load(manager.render_profile(profile))
            self.assertEqual(config["dns"]["proxy-server-nameserver"], [dns["nameserver"], dns["fallback"]])
            self.assertEqual(config["rules"], [*rules, "MATCH,GATE.312"])
            self.assertEqual(config["proxies"][0]["ech-opts"], {"enable": True})
            dns["fallback"] = "8.8.8.8"
            config = yaml.safe_load(manager.render_profile(profile))
            self.assertEqual(config["dns"]["fallback"], ["https://dns.google/dns-query"])

    def test_old_client_core_is_rejected_before_key_generation(self):
        with patch.object(manager.Path, "is_file", return_value=True), patch.object(manager, "run", return_value=subprocess.CompletedProcess([], 0, "Mihomo Meta v1.18.0", "")) as run:
            with self.assertRaises(manager.HTTPException) as error:
                manager.vless_encryption_pair({"privacy_mode": "encrypted"})
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(run.call_count, 1)

    def test_encryption_is_applied_only_to_owned_inbounds(self):
        manifest = json.loads((ROOT / "protocol-images/mihomo/modules/transport-reality/manifest.json").read_text())
        defaults = {field["key"]: field.get("default") for field in manifest["connection_settings"]}
        settings = {**defaults, "privacy_mode": "encrypted", "route_mode": "both", "cdn_enabled": True, "tls_enabled": False, "cdn_domain": "example.com"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reality").mkdir()
            existing = {"tag": "unrelated", "port": 8001, "protocol": "vless", "settings": {"decryption": "none"}}
            (root / "reality/config.json").write_text(json.dumps({"inbounds": [existing]}))
            with patch.object(manager, "CONFIG_ROOT", root), patch.object(manager, "validate_connection", return_value=settings), patch.object(manager, "module_settings", return_value={"port_start": 9443, "cdn_port_start": 10443}), patch.object(manager, "reality_env", return_value={"PRIVATE_KEY": "reality-private", "PUBLIC_KEY": "reality-public"}), patch.object(manager, "vless_encryption_pair", return_value=("server-private", "client-public")), patch.object(manager, "validate_rendered_profile") as client_validation, patch.object(manager, "write_mihomo_vless_cdn"), patch.object(manager, "apply_reality_config") as apply:
                credential = manager.add_reality_credential("profile", "connection", settings, restart_service=False, reload_caddy=False)
                candidate = apply.call_args.args[1]
                self.assertEqual(candidate["inbounds"][0], existing)
                self.assertEqual([item["settings"]["decryption"] for item in candidate["inbounds"][1:]], ["server-private", "server-private"])
                self.assertEqual(credential["encryption"], "client-public")
                self.assertNotIn("server-private", json.dumps(credential))
                client_validation.assert_called_once()

    def test_client_validation_failure_leaves_server_untouched(self):
        manifest = json.loads((ROOT / "protocol-images/mihomo/modules/transport-reality/manifest.json").read_text())
        settings = {field["key"]: field.get("default") for field in manifest["connection_settings"]}
        settings.update({"privacy_mode": "encrypted", "route_mode": "cdn", "cdn_enabled": True, "tls_enabled": False, "cdn_domain": "example.com"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reality").mkdir()
            config = root / "reality/config.json"
            config.write_text('{"inbounds": []}')
            with patch.object(manager, "CONFIG_ROOT", root), patch.object(manager, "validate_connection", return_value=settings), patch.object(manager, "module_settings", return_value={"port_start": 9443, "cdn_port_start": 10443}), patch.object(manager, "reality_env", return_value={"PRIVATE_KEY": "private", "PUBLIC_KEY": "public"}), patch.object(manager, "vless_encryption_pair", return_value=("private", "public")), patch.object(manager, "validate_rendered_profile", side_effect=RuntimeError("unsupported client")), patch.object(manager, "write_mihomo_vless_cdn") as caddy, patch.object(manager, "apply_reality_config") as apply:
                with self.assertRaisesRegex(RuntimeError, "unsupported client"):
                    manager.add_reality_credential("profile", "connection", settings)
                caddy.assert_not_called()
                apply.assert_not_called()
                self.assertEqual(config.read_text(), '{"inbounds": []}')

    def test_default_mode_never_generates_keys(self):
        with patch.object(manager, "run") as run:
            self.assertEqual(manager.vless_encryption_pair({}), ("none", ""))
            run.assert_not_called()

    def test_generator_uses_matching_pair_without_zero_rtt(self):
        output = '\n'.join([
            '"decryption": "mlkem768x25519plus.native.600s.first-private"',
            '"encryption": "mlkem768x25519plus.native.0rtt.first-public"',
            '"decryption": "mlkem768x25519plus.native.600s.second-private"',
            '"encryption": "mlkem768x25519plus.native.0rtt.second-public"',
        ])
        with patch.object(manager.Path, "is_file", return_value=True), patch.object(manager, "run", side_effect=[subprocess.CompletedProcess([], 0, "Mihomo Meta v1.19.30", ""), subprocess.CompletedProcess([], 0, output, "")]):
            server, client = manager.vless_encryption_pair({"privacy_mode": "encrypted"})
        self.assertEqual(server, "mlkem768x25519plus.random.0s.second-private")
        self.assertEqual(client, "mlkem768x25519plus.random.1rtt.second-public")
        self.assertNotIn("private", client)

    def test_unsupported_generator_fails_without_disclosing_output(self):
        with patch.object(manager.Path, "is_file", return_value=True), patch.object(manager, "run", side_effect=[subprocess.CompletedProcess([], 0, "Mihomo Meta v1.19.30", ""), subprocess.CompletedProcess([], 1, "sensitive-output", "")]):
            with self.assertRaises(manager.HTTPException) as caught:
                manager.vless_encryption_pair({"privacy_mode": "encrypted"})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("sensitive-output", caught.exception.detail)

    def test_default_fields_do_not_change_legacy_settings(self):
        manifest = json.loads((ROOT / "protocol-images/mihomo/modules/transport-reality/manifest.json").read_text())
        with patch.object(manager, "manifest", return_value=manifest), patch.object(manager, "module_is_installed", return_value=True):
            legacy = manager.validate_connection("transport-reality", {"route_mode": "cdn", "cdn_domain": "example.com"})
            self.assertNotIn("privacy_mode", legacy)
            self.assertNotIn("cdn_ech", legacy)
            self.assertEqual(legacy, manager.validate_connection("transport-reality", {**legacy, "privacy_mode": "standard", "cdn_ech": False}))
            self.assertEqual(legacy, manager.validate_connection("transport-reality", {**legacy, "privacy_mode": "encrypted", "cdn_ech": True}))

    def test_cdn_export_carries_encryption_and_optional_ech(self):
        credential = {"cdn_domain": "example.com", "cdn_path": "/test", "uuid": str(uuid.uuid4()), "encryption": "test-public-configuration"}
        for transport in ("websocket", "xhttp", "httpupgrade", "grpc"):
            with self.subTest(transport=transport):
                for ech in (False, True):
                    config = yaml.safe_load("proxies:\n" + "\n".join(manager.render_vless_cdn({**credential, "cdn_transport": transport, "cdn_ech": ech}, "Connection 1")))
                    proxy = config["proxies"][0]
                    self.assertEqual(proxy.get("encryption"), credential["encryption"])
                    self.assertEqual(proxy.get("ech-opts"), {"enable": True} if ech else None)
                    self.assertTrue(proxy["tls"])
                    self.assertFalse(proxy.get("skip-cert-verify", False))

    def test_missing_encryption_never_silently_downgrades(self):
        profile = {"common_device_id": "common", "routing": {"tunnel_privacy": True}, "connections": [{"component": "transport-reality", "device_id": "common", "settings": {"privacy_mode": "encrypted"}, "credential": {}}]}
        with patch.object(manager, "routing_settings", return_value={"tunnel_privacy": True}), patch.object(manager, "normalize_profile", return_value=profile):
            with self.assertRaises(manager.HTTPException):
                manager.render_profile(profile)


@unittest.skipUnless(os.getenv("PRIVACY_XRAY_BIN") and os.getenv("PRIVACY_MIHOMO_BIN"), "Set local test binary paths to run transport integration tests")
class RealCorePrivacyTests(unittest.TestCase):
    def test_real_core_accepts_encrypted_ech_export(self):
        xray = Path(os.environ["PRIVACY_XRAY_BIN"]).resolve()
        mihomo = Path(os.environ["PRIVACY_MIHOMO_BIN"]).resolve()
        with tempfile.TemporaryDirectory() as temp, patch.object(manager, "REALITY_XRAY_BIN", xray), patch.object(manager, "CORE_BIN", mihomo), patch.object(manager, "CORE_HOME", Path(temp)):
            _, encryption = manager.vless_encryption_pair({"privacy_mode": "encrypted"})
            for transport in ("websocket", "xhttp", "httpupgrade", "grpc"):
                profile = {"common_device_id": "common", "connections": [{"component": "transport-reality", "device_id": "common", "settings": {"route_mode": "cdn", "cdn_ech": True, "privacy_mode": "encrypted"}, "credential": {"uuid": str(uuid.uuid4()), "encryption": encryption, "cdn_enabled": True, "route_mode": "cdn", "cdn_domain": "example.com", "cdn_path": "/test", "cdn_transport": transport}}]}
                dns = {"enhanced_mode": "fake-ip", "nameserver": "https://cloudflare-dns.com/dns-query", "fallback": "https://dns.google/dns-query"}
                with patch.object(manager, "routing_settings", return_value={"tunnel_privacy": True}), patch.object(manager, "cdn_supports_ech", return_value=True), patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "dns_settings", return_value=dns), patch.object(manager, "profile_rules", return_value=[]):
                    profile["routing"] = {"tunnel_privacy": True, "tunnel_ech": True}
                    config = yaml.safe_load(manager.render_profile(profile))
                    self.assertTrue(config["proxies"][0]["ech-opts"]["enable"])
                    # This compatibility check must not depend on downloading GeoIP data.
                    config["dns"]["fallback-filter"] = {"geoip": False}
                    manager.validate_rendered_profile(yaml.safe_dump(config))

    def test_encrypted_transports_hide_payload_from_relay(self):
        xray = Path(os.environ["PRIVACY_XRAY_BIN"]).resolve()
        mihomo = Path(os.environ["PRIVACY_MIHOMO_BIN"]).resolve()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with patch.object(manager, "REALITY_XRAY_BIN", xray), patch.object(manager, "CORE_BIN", mihomo):
            decryption, encryption = manager.vless_encryption_pair({"privacy_mode": "encrypted"})
            _, wrong_encryption = manager.vless_encryption_pair({"privacy_mode": "encrypted"})
        transports = (os.environ["PRIVACY_TEST_TRANSPORT"],) if os.getenv("PRIVACY_TEST_TRANSPORT") else ("websocket", "xhttp", "httpupgrade", "grpc")
        for transport in transports:
            with self.subTest(transport=transport), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                upstream_port, proxy_port, relay_port = free_port(), free_port(), free_port()
                identity = str(uuid.uuid4())
                marker = "private-probe-" + uuid.uuid4().hex
                received = []

                class Origin(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        received.append(self.headers.get("X-Privacy-Probe"))
                        body = marker.encode()
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                    def log_message(self, *_):
                        pass

                origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
                threading.Thread(target=origin.serve_forever, daemon=True).start()
                captured = bytearray()
                stop = threading.Event()
                sockets = []
                relay = socket.socket()
                relay.bind(("127.0.0.1", relay_port))
                relay.listen()
                relay.settimeout(.2)

                def copy_stream(source, target):
                    try:
                        while not stop.is_set():
                            data = source.recv(65536)
                            if not data:
                                break
                            captured.extend(data)
                            target.sendall(data)
                    except OSError:
                        pass
                    finally:
                        try:
                            target.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass

                def relay_loop():
                    while not stop.is_set():
                        try:
                            client, _ = relay.accept()
                        except socket.timeout:
                            continue
                        except OSError:
                            break
                        try:
                            upstream = socket.create_connection(("127.0.0.1", upstream_port), timeout=10)
                            upstream.settimeout(None)
                        except OSError:
                            client.close()
                            continue
                        sockets.extend([client, upstream])
                        threading.Thread(target=copy_stream, args=(client, upstream), daemon=True).start()
                        threading.Thread(target=copy_stream, args=(upstream, client), daemon=True).start()

                processes = []
                try:
                    stream = manager.edge_stream(transport, "/opaque-path", "auto")
                    server = {"log": {"loglevel": "error"}, "inbounds": [{"listen": "127.0.0.1", "port": upstream_port, "protocol": "vless", "settings": {"clients": [{"id": identity}], "decryption": decryption}, "streamSettings": stream}], "outbounds": [{"protocol": "freedom"}]}
                    (root / "server.json").write_text(json.dumps(server))
                    server_process = subprocess.Popen([str(xray), "run", "-config", str(root / "server.json")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                    processes.append(server_process)
                    wait_port(upstream_port, server_process)
                    threading.Thread(target=relay_loop, daemon=True).start()
                    for key, should_work in ((encryption, True), (wrong_encryption, False)):
                        credential = {"cdn_domain": "127.0.0.1", "cdn_path": "/opaque-path", "cdn_transport": transport, "uuid": identity, "encryption": key}
                        proxy = yaml.safe_load("proxies:\n" + "\n".join(manager.render_vless_cdn(credential, "Connection 1")))["proxies"][0]
                        # Test the stream exposed after CDN TLS termination.
                        proxy.update({"tls": False, "port": relay_port})
                        client_config = {"port": proxy_port, "bind-address": "127.0.0.1", "allow-lan": False, "mode": "rule", "log-level": "warning", "proxies": [proxy], "rules": ["MATCH,Connection 1"]}
                        (root / "client.yaml").write_text(yaml.safe_dump(client_config))
                        client_process = subprocess.Popen([str(mihomo), "-d", str(root), "-f", str(root / "client.yaml")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
                        processes.append(client_process)
                        try:
                            wait_port(proxy_port, client_process)
                        except AssertionError:
                            client_process.terminate()
                            output, _ = client_process.communicate(timeout=10)
                            self.fail(output.decode(errors="replace")[-1500:])
                        # Raw HTTP proxy request avoids environment proxy bypasses.
                        with socket.create_connection(("127.0.0.1", proxy_port), timeout=8) as connection:
                            connection.settimeout(8)
                            connection.sendall(f"GET http://127.0.0.1:{origin.server_port}/probe HTTP/1.1\r\nHost: 127.0.0.1:{origin.server_port}\r\nX-Privacy-Probe: {marker}\r\nConnection: close\r\n\r\n".encode())
                            response = b""
                            try:
                                while chunk := connection.recv(65536):
                                    response += chunk
                            except TimeoutError:
                                pass
                        if should_work:
                            self.assertIn(marker.encode(), response, transport)
                            self.assertEqual(received, [marker])
                        else:
                            self.assertNotIn(marker.encode(), response)
                            self.assertEqual(received, [marker], "Wrong key must not reach origin")
                        client_process.terminate()
                        client_process.communicate(timeout=10)
                    self.assertGreater(len(captured), 100)
                    self.assertNotIn(marker.encode(), captured)
                    self.assertNotIn(uuid.UUID(identity).bytes, captured)
                finally:
                    stop.set()
                    relay.close()
                    for sock in sockets:
                        sock.close()
                    for process in reversed(processes):
                        if process.poll() is None:
                            process.terminate()
                            process.communicate(timeout=10)
                    origin.shutdown()
                    origin.server_close()


if __name__ == "__main__":
    unittest.main()
