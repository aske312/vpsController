import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import load_module

manager = load_module("mihomo_manager_under_test", "protocol-images/mihomo/manager.py")


class MihomoTransactionTests(unittest.TestCase):
    def test_failed_peer_removal_preserves_config(self):
        for module_id in ("transport-wg", "transport-awg"):
            with self.subTest(module=module_id), tempfile.TemporaryDirectory() as root:
                config = Path(root) / "tunnel.conf"
                before = "[Interface]\n# mihomo-profile:profile:connection\n[Peer]\nPublicKey = public\n"
                config.write_text(before, encoding="utf-8")
                with (
                    patch.object(manager, "WG_CONFIG_BY_MODULE", {module_id: config}),
                    patch.object(manager.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, "mh-wg0 mh-awg0", ""),
                        subprocess.CompletedProcess([], 1, "", "peer removal failed"),
                    ]),
                ):
                    with self.assertRaisesRegex(RuntimeError, "peer removal failed"):
                        manager.remove_wg_credential("profile", module_id, {"public_key": "public", "marker": "profile:connection"})
                self.assertEqual(config.read_text(encoding="utf-8"), before)

    def test_stopped_tunnel_can_remove_saved_peer(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "tunnel.conf"
            config.write_text("[Interface]\n# mihomo-profile:profile:connection\n[Peer]\nPublicKey = public\n", encoding="utf-8")
            with (
                patch.object(manager, "WG_CONFIG_BY_MODULE", {"transport-wg": config}),
                patch.object(manager.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run,
            ):
                manager.remove_wg_credential("profile", "transport-wg", {"public_key": "public", "marker": "profile:connection"})
            self.assertEqual(config.read_text(encoding="utf-8"), "[Interface]\n")
            self.assertEqual(run.call_count, 1)

    def test_failed_shadowsocks_stop_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shadowsocks" / "profile-connection.json"
            config.parent.mkdir()
            config.write_text('{"password":"retained"}', encoding="utf-8")
            with (
                patch.object(manager, "CONFIG_ROOT", Path(root)),
                patch.object(manager.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "stop failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "stop failed"):
                    manager.remove_ss_credential("profile", {"instance_id": "profile-connection"})
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"password": "retained"})

    def test_rollback_restores_external_tunnel_config_before_restart(self):
        for module_id in ("transport-wg", "transport-awg"):
            with self.subTest(module=module_id), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                config = root / "external" / "tunnel.conf"
                config.parent.mkdir()
                config.write_text("original peers", encoding="utf-8")
                service = manager.SERVICE_BY_MODULE[module_id]

                def check_restart(*args, **kwargs):
                    if args[:2] == ("systemctl", "restart"):
                        self.assertEqual(config.read_text(encoding="utf-8"), "original peers")

                with (
                    patch.object(manager, "CONFIG_ROOT", root / "managed"),
                    patch.object(manager, "PROFILE_FILE", root / "profiles.json"),
                    patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                    patch.object(manager, "WG_CONFIG_BY_MODULE", {module_id: config}),
                    patch.object(manager, "systemctl_active", return_value=True),
                    patch.object(manager, "run", side_effect=check_restart) as run,
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        with manager.profile_runtime_transaction({module_id}):
                            config.write_text("changed peers", encoding="utf-8")
                            raise RuntimeError("injected")
                    run.assert_any_call("systemctl", "restart", service)
                self.assertEqual(config.read_text(encoding="utf-8"), "original peers")

    def test_vless_limit_is_applied_separately_per_route(self):
        def connections(route, count, device="phone"):
            return [{"component": "transport-reality", "device_id": device, "settings": {"route_mode": route}} for _ in range(count)]

        with patch.object(manager, "max_vless_connections_per_device", return_value=5):
            manager.validate_vless_connection_limit(connections("direct", 5) + connections("cdn", 5) + connections("tls", 5))
            with self.assertRaisesRegex(manager.HTTPException, "режиме REALITY"):
                manager.validate_vless_connection_limit(connections("direct", 6))
            with self.assertRaisesRegex(manager.HTTPException, "режиме CDN"):
                manager.validate_vless_connection_limit(connections("cdn", 6))
            with self.assertRaisesRegex(manager.HTTPException, "режиме TLS"):
                manager.validate_vless_connection_limit(connections("tls", 6))

    def test_fault_during_mutation_restores_profile_and_adapter_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            config = root / "config"
            profile = root / "profiles.json"
            config.mkdir()
            (config / "adapter.json").write_text('{"before": true}\n', encoding="utf-8")
            profile.write_text('[{"id":"before"}]\n', encoding="utf-8")
            with (
                patch.object(manager, "CONFIG_ROOT", config),
                patch.object(manager, "PROFILE_FILE", profile),
                patch.object(manager, "SERVICE_BY_MODULE", {}),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with manager.profile_runtime_transaction({"transport-reality"}):
                        (config / "adapter.json").write_text('{"after": true}\n', encoding="utf-8")
                        profile.write_text('[{"id":"after"}]\n', encoding="utf-8")
                        raise RuntimeError("injected")
            self.assertEqual(json.loads((config / "adapter.json").read_text()), {"before": True})
            self.assertEqual(json.loads(profile.read_text()), [{"id": "before"}])

    def test_batched_reality_apply_restarts_and_reloads_once(self):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch.object(manager, "load_json", return_value={"inbounds": []}),
            patch.object(manager, "apply_reality_config"),
            patch.object(manager, "rebuild_vless_cdn_snippet"),
            patch.object(manager, "service_stably_active", return_value=True),
            patch.object(manager, "run", side_effect=fake_run),
            patch.object(manager.Path, "is_file", return_value=False),
        ):
            manager.apply_batched_reality_runtime()
        self.assertEqual(sum(call[:2] == ("systemctl", "restart") for call in calls), 1)
        self.assertEqual(sum(call[:2] == ("systemctl", "reload") for call in calls), 1)


if __name__ == "__main__":
    unittest.main()
