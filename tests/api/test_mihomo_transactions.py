import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import load_module

manager = load_module("mihomo_manager_under_test", "protocol-images/mihomo/manager.py")


class MihomoTransactionTests(unittest.TestCase):
    def test_shadowsocks_rollback_restores_instances_and_removes_new_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config"
            ss = config / "shadowsocks"
            ss.mkdir(parents=True)
            prefix = "vps-control-mihomo-ss@"
            states = {
                f"{prefix}deleted.service": [True, "enabled"],
                f"{prefix}stopped.service": [False, "enabled"],
                f"{prefix}manual.service": [True, "disabled"],
                f"{prefix}runtime.service": [False, "enabled-runtime"],
                f"{prefix}masked.service": [False, "masked"],
            }
            before = {unit: list(value) for unit, value in states.items()}
            for unit in states:
                (ss / f"{unit[len(prefix):-8]}.json").write_text('"original"', encoding="utf-8")
            calls = []

            def fake_run(*args, **kwargs):
                calls.append(args)
                action = args[1]
                unit = args[2] if action == "show" else args[-1]
                output = ""
                if action in {"list-units", "list-unit-files"}:
                    output = "\n".join(states)
                elif action == "show":
                    active, enabled = states[unit]
                    output = f"ActiveState={'active' if active else 'inactive'}\nUnitFileState={enabled}\n"
                elif action == "is-active":
                    output = "active" if states[unit][0] else "inactive"
                elif action == "disable":
                    states[unit][1] = "disabled"
                    if "--now" in args:
                        states[unit][0] = False
                elif action == "unmask":
                    if states[unit][1].startswith("masked"):
                        states[unit][1] = "disabled"
                elif action in {"enable", "mask"}:
                    states[unit][1] = ("enabled" if action == "enable" else "masked") + ("-runtime" if "--runtime" in args else "")
                elif action == "restart":
                    self.assertEqual((ss / f"{unit[len(prefix):-8]}.json").read_text(), '"original"')
                    states[unit][0] = True
                elif action == "stop":
                    states[unit][0] = False
                else:
                    self.fail(f"Unexpected command: {args}")
                return subprocess.CompletedProcess(args, 0, output, "")

            with (
                patch.object(manager, "CONFIG_ROOT", config),
                patch.object(manager, "PROFILE_FILE", root / "profiles.json"),
                patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                patch.object(manager, "systemctl_active", return_value=True),
                patch.object(manager, "run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with manager.profile_runtime_transaction({"transport-shadowsocks"}):
                        states[f"{prefix}deleted.service"] = [False, "disabled"]
                        (ss / "deleted.json").unlink()
                        states[f"{prefix}stopped.service"][0] = True
                        states[f"{prefix}runtime.service"][1] = "disabled"
                        states[f"{prefix}masked.service"][1] = "disabled"
                        states[f"{prefix}created.service"] = [True, "enabled"]
                        (ss / "created.json").write_text('"new"', encoding="utf-8")
                        raise RuntimeError("injected")
            self.assertEqual({unit: states[unit] for unit in before}, before)
            self.assertEqual(states[f"{prefix}created.service"], [False, "disabled"])
            self.assertFalse((ss / "created.json").exists())
            self.assertNotIn(("systemctl", "restart", f"{prefix}manual.service"), calls)

    def test_shadowsocks_rollback_failure_is_visible_and_other_instances_restore(self):
        before = {"one": (True, "enabled", b"config"), "two": (True, "enabled", b"config")}
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[1] == "restart" and args[-1] == "one":
                raise RuntimeError("restart failed")
            return subprocess.CompletedProcess(args, 0, "inactive", "")

        with self.assertRaisesRegex(RuntimeError, "rollback incomplete.*one.*restart failed"):
            manager.ss_runtime.restore(fake_run, before, before)
        self.assertIn(("systemctl", "restart", "two"), calls)

    def test_shadowsocks_snapshot_failure_prevents_mutation(self):
        with patch.object(manager.ss_runtime, "snapshot", side_effect=RuntimeError("systemd unavailable")):
            with self.assertRaisesRegex(RuntimeError, "systemd unavailable"):
                with manager.profile_runtime_transaction({"transport-shadowsocks"}):
                    self.fail("Mutation must not begin without a snapshot")

    def test_shadowsocks_rollback_reports_original_and_recovery_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(manager, "CONFIG_ROOT", root / "config"),
                patch.object(manager, "PROFILE_FILE", root / "profiles.json"),
                patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                patch.object(manager, "systemctl_active", return_value=True),
                patch.object(manager.ss_runtime, "snapshot", return_value={}),
                patch.object(manager.ss_runtime, "restore", side_effect=RuntimeError("recovery failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "mutation failed; rollback incomplete: recovery failed"):
                    with manager.profile_runtime_transaction({"transport-shadowsocks"}):
                        raise RuntimeError("mutation failed")

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

    def test_reality_rollback_restores_dynamic_user_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            config = root / "config"
            reality = config / "reality"
            reality.mkdir(parents=True)
            (reality / "config.json").write_text('{}\n', encoding="utf-8")
            with (
                patch.object(manager, "CONFIG_ROOT", config),
                patch.object(manager, "PROFILE_FILE", root / "profiles.json"),
                patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                patch.object(manager, "systemctl_active", return_value=False),
                patch.object(manager.shutil, "chown") as chown,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with manager.profile_runtime_transaction({"transport-reality"}):
                        raise RuntimeError("injected")
            chown.assert_any_call(reality, user="root", group="nogroup")
            chown.assert_any_call(reality / "config.json", user="root", group="nogroup")

    def test_reality_apply_repairs_directory_traversal_before_restart(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "reality/config.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"inbounds": []}', encoding="utf-8")
            calls = []

            def run(*args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, "", "")

            with (
                patch.object(manager, "run", side_effect=run),
                patch.object(manager.shutil, "chown"),
                patch.object(manager, "ensure_reality_config_permissions") as permissions,
            ):
                manager.apply_reality_config(config, {"inbounds": []})

            permissions.assert_called_once_with(config)
            self.assertLess(
                calls.index((str(manager.REALITY_XRAY_BIN), "run", "-test", "-config", str(config.with_name("config.candidate.json")))),
                calls.index(("systemctl", "restart", "vps-control-mihomo-reality.service")),
            )

    def test_caddy_validation_uses_service_identity_and_storage(self):
        with patch.object(manager, "run") as run:
            manager.validate_caddy_config()
        run.assert_called_once_with(
            "runuser", "-u", "caddy", "--", "env",
            "HOME=/var/lib/caddy", "XDG_DATA_HOME=/var/lib/caddy/.local/share",
            "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
            check=True,
        )

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
