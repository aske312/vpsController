import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import load_module

manager = load_module("mihomo_manager_under_test", "protocol-images/mihomo/manager.py")


class MihomoTransactionTests(unittest.TestCase):
    def test_udp_domain_edit_preserves_credentials_and_does_not_restart_protocol(self):
        self.legacy_channel_fixture()
        credential = {'public_key': 'key', 'server_address': '195.14.118.112', 'port': 51830}
        saved = json.loads(self.profile_file.read_text())
        saved[0]['connections'] = [{'id': 'wg', 'component': 'transport-wg', 'device_id': 'common', 'settings': {'mtu': 1400}, 'credential': credential}]
        self.profile_file.write_text(json.dumps(saved))
        with patch.object(manager, 'mihomo_route_endpoint_selectable', return_value=True), patch.object(manager, 'public_endpoint', return_value='195.14.118.112'), patch.object(manager.server_addresses, 'primary_for_new_config', return_value='195.14.118.112'), patch.object(manager.socket, 'getaddrinfo', return_value=[(2, 2, 17, '', ('195.14.118.112', 0))]), patch.object(manager, 'preflight_client_export_update'), patch.object(manager, 'profile_response', side_effect=lambda item: item), patch.object(manager, 'provision') as provision, patch.object(manager, 'deprovision') as deprovision, patch.object(manager, 'run') as command:
            manager.update_profile('legacy', manager.ProfileUpdate(connections=[manager.ProfileConnectionInput(id='wg', component='transport-wg', device_id='common', settings={'mtu': 1400, 'udp_domain': 'direct.example'})]))
        connection = json.loads(self.profile_file.read_text())[0]['connections'][0]
        self.assertEqual(connection['settings']['udp_domain'], 'direct.example')
        self.assertEqual(connection['credential'], credential)
        self.assertEqual(self.external.read_text(), 'original peers')
        provision.assert_not_called()
        deprovision.assert_not_called()
        self.assertFalse(any(call.args[:2] == ('systemctl', 'restart') for call in command.call_args_list))

    def legacy_channel_fixture(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.profile_file = root / "profiles.json"
        self.external = root / "wg.conf"
        self.profile_file.write_text(json.dumps([{"id": "legacy", "name": "Legacy", "devices": [{"id": "common"}],
                                                "common_device_id": "common", "connections": [], "channels": [], "credentials": {}}]))
        self.external.write_text("original peers")
        self.original_profile = self.profile_file.read_bytes()
        self.service_state = "running"
        for name, value in {"PROFILE_FILE": self.profile_file, "CONFIG_ROOT": root / "config",
                            "ROUTING_SETTINGS_FILE": root / "routing.json",
                            "WG_CONFIG_BY_MODULE": {"transport-wg": self.external},
                            "SERVICE_BY_MODULE": {"transport-wg": "fixture.service"}}.items():
            self.enterContext(patch.object(manager, name, value))
        self.enterContext(patch.object(manager, "profiles", side_effect=lambda: json.loads(self.profile_file.read_text())))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", return_value=True))
        self.enterContext(patch.object(manager, "get_action_payload", return_value={}))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "reconcile_profile_encryption"))
        self.enterContext(patch.object(manager, "normalize_profile", side_effect=lambda item: dict(item)))
        self.enterContext(patch.object(manager, "manifest", side_effect=lambda module: {"name": module}))
        self.enterContext(patch.object(manager, "observe_service", side_effect=lambda _: {"unit_present": True, "runtime": {"state": self.service_state}}))

    def test_legacy_new_channel_rollback_restores_external_config_and_runtime(self):
        self.legacy_channel_fixture()
        def provision(*args, **kwargs):
            self.external.write_text("new peer")
            self.service_state = "stopped"
            return {"public_key": "fixture"}
        def run(*args, **kwargs):
            if args[:2] == ("systemctl", "restart"):
                self.assertEqual(self.external.read_text(), "original peers")
                self.service_state = "running"
        with (patch.object(manager, "provision", side_effect=provision),
              patch.object(manager, "run", side_effect=run) as command,
              patch.object(manager, "save_profiles", side_effect=OSError("profile write failed")),
              self.assertRaisesRegex(OSError, "profile write failed")):
            manager.update_profile("legacy", manager.ProfileUpdate(channels=["transport-wg"]))
        self.assertEqual(self.external.read_text(), "original peers")
        self.assertEqual(self.profile_file.read_bytes(), self.original_profile)
        self.assertEqual(self.service_state, "running")
        command.assert_any_call("systemctl", "restart", "fixture.service", check=True)
        self.assertFalse((self.profile_file.parent / "profile-recovery/manifest.json").exists())

    def test_legacy_new_channel_unknown_runtime_prevents_provision(self):
        self.legacy_channel_fixture()
        self.service_state = "unknown"
        with patch.object(manager, "provision") as provision, self.assertRaisesRegex(RuntimeError, "Неизвестно состояние"):
            manager.update_profile("legacy", manager.ProfileUpdate(channels=["transport-wg"]))
        provision.assert_not_called()
        self.assertEqual(self.external.read_text(), "original peers")
        self.assertEqual(self.profile_file.read_bytes(), self.original_profile)

    def test_invalid_legacy_channel_is_rejected_before_snapshot_or_provision(self):
        self.legacy_channel_fixture()
        with patch.object(manager, "provision") as provision, self.assertRaises(manager.HTTPException) as invalid:
            manager.update_profile("legacy", manager.ProfileUpdate(channels=["not-a-transport"]))
        self.assertEqual(invalid.exception.status_code, 422)
        provision.assert_not_called()
        self.assertFalse((self.profile_file.parent / "profile-recovery/manifest.json").exists())

    def test_legacy_channel_edit_preserves_all_retained_connection_instances(self):
        self.legacy_channel_fixture()
        connections = [{"id": f"wg-{index}", "component": "transport-wg", "device_id": device,
                        "settings": {"mtu": 1400 + index}, "credential": {"public_key": f"key-{index}"}}
                       for index, device in enumerate(("common", "phone"))]
        saved = json.loads(self.profile_file.read_text())
        saved[0].update(connections=connections, channels=["transport-wg"], credentials={"transport-wg": connections[0]["credential"]})
        saved[0]["devices"].append({"id": "phone"})
        self.profile_file.write_text(json.dumps(saved))
        with patch.object(manager, "provision") as provision, patch.object(manager, "deprovision") as deprovision, patch.object(manager, "profile_response", side_effect=lambda item: item):
            manager.update_profile("legacy", manager.ProfileUpdate(channels=["transport-wg"]))
        self.assertEqual(json.loads(self.profile_file.read_text())[0]["connections"], connections)
        provision.assert_not_called()
        deprovision.assert_not_called()

    def test_legacy_channel_removal_revokes_every_instance_including_retiring(self):
        self.legacy_channel_fixture()
        connections = [{"id": f"wg-{index}", "component": "transport-wg", "device_id": "common",
                        "settings": {}, "credential": {"public_key": f"key-{index}"}} for index in range(2)]
        saved = json.loads(self.profile_file.read_text())
        saved[0].update(connections=connections, channels=["transport-wg"], credentials={"transport-wg": connections[0]["credential"]},
                        retiring_connections=[{**connections[0], "credential": {"public_key": "retiring-key"}}])
        self.profile_file.write_text(json.dumps(saved))
        with patch.object(manager, "deprovision") as deprovision, patch.object(manager, "profile_response", side_effect=lambda item: item):
            manager.update_profile("legacy", manager.ProfileUpdate(channels=[]))
        self.assertEqual([call.args[2]["public_key"] for call in deprovision.call_args_list], ["retiring-key", "key-0", "key-1"])
        result = json.loads(self.profile_file.read_text())[0]
        self.assertEqual(result["connections"], [])
        self.assertEqual(result.get("retiring_connections", []), [])
        self.assertEqual(result["credentials"], {})

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
                patch.object(manager, "observe_service", return_value={"unit_present": True, "runtime": {"state": "running"}}), patch.object(manager, "systemctl_active", return_value=True),
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
                patch.object(manager, "observe_service", return_value={"unit_present": True, "runtime": {"state": "running"}}), patch.object(manager, "systemctl_active", return_value=True),
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
                    patch.object(manager, "observe_service", return_value={"unit_present": True, "runtime": {"state": "running"}}), patch.object(manager, "systemctl_active", return_value=True),
                    patch.object(manager, "run", side_effect=check_restart) as run,
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        with manager.profile_runtime_transaction({module_id}):
                            config.write_text("changed peers", encoding="utf-8")
                            raise RuntimeError("injected")
                    run.assert_any_call("systemctl", "restart", service, check=True)
                self.assertEqual(config.read_text(encoding="utf-8"), "original peers")

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
                patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                patch.object(manager, "SERVICE_BY_MODULE", {}),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with manager.profile_runtime_transaction({"transport-reality"}):
                        (config / "adapter.json").write_text('{"after": true}\n', encoding="utf-8")
                        profile.write_text('[{"id":"after"}]\n', encoding="utf-8")
                        raise RuntimeError("injected")
            self.assertEqual(json.loads((config / "adapter.json").read_text()), {"before": True})
            self.assertEqual(json.loads(profile.read_text()), [{"id": "before"}])

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
            patch.object(manager.Path, "is_file", return_value=True),
        ):
            manager.apply_batched_reality_runtime()
        self.assertEqual(sum(call[:2] == ("systemctl", "restart") for call in calls), 1)
        self.assertEqual(sum(call[:2] == ("systemctl", "reload") for call in calls), 1)
        self.assertIn(
            ("/usr/bin/python3", str(manager.APP_ROOT / "api" / "cdn_security.py"), "firewall"),
            calls,
        )


if __name__ == "__main__":
    unittest.main()
