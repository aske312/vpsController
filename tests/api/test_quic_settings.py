"""QUIC settings apply checks config, preserves users and restores failures."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT
import profile_recovery
import quic_settings


def config(module, port):
    return {"log": {"level": "warn"}, "inbounds": [{"type": module, "tag": f"{module}-in", "listen": "::", "listen_port": port,
            "users": [{"name": "fixture", "password": "fixture", **({"uuid": "00000000-0000-4000-8000-000000000001"} if module == "tuic" else {})}],
            "tls": {"enabled": True, "server_name": "fixture", "alpn": ["h3"]},
            **({"up_mbps": 100, "down_mbps": 100, "obfs": {"type": "salamander", "password": "fixture"}} if module == "hysteria2" else
               {"congestion_control": "bbr", "heartbeat": "10s", "auth_timeout": "3s", "zero_rtt_handshake": False})}],
            "route": {"final": "direct"}, "outbounds": [{"type": "direct", "tag": "direct"}],
            "services": [{"type": "api", "tag": "panel-telemetry", "listen": "127.0.0.1", "listen_port": 19100, "secret": "fixture"}]}


class QuicSettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {"PROFILE_FILE": self.root / "profiles.json", "CONFIG_ROOT": self.root / "config",
                            "SETTINGS_ROOT": self.root / "settings", "ROUTING_SETTINGS_FILE": self.root / "routing.json",
                            "SUBMODULE_ROOT": ROOT / "protocol-images/mihomo/modules"}.items():
            self.enterContext(patch.object(manager, name, value))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "check_module_port"))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "get_action_payload", return_value={}))
        self.enterContext(patch.object(manager, "profiles", return_value=[]))
        self.enterContext(patch.object(manager, "quic_credentials", return_value=[]))
        self.enterContext(patch.object(manager.shutil, "which", return_value=None))
        self.install = self.enterContext(patch.object(manager, "call_module_script"))
        self.telemetry = self.enterContext(patch.object(manager, "start_quic_telemetry"))
        self.enterContext(patch.object(manager, "observe_service", side_effect=lambda _: {"unit_present": True, "unit_file_state": "disabled", "runtime": {"state": self.state}}))
        self.enterContext(patch.object(manager, "service_stably_active", side_effect=lambda _: not self.fail_health))
        self.events = []
        def run(*args, **kwargs):
            self.events.append(args)
            if args[0] == str(manager.SINGBOX_BIN) and self.reject:
                return subprocess.CompletedProcess(args, 1, "", "fixture private credential")
            if args[:2] == ("systemctl", "restart"):
                if self.path.read_bytes() != self.original:
                    self.state = "error"
                    if self.interrupt:
                        raise KeyboardInterrupt()
                    if self.fail_restart:
                        raise RuntimeError("candidate failed")
                self.state = "running"
            elif args[:2] == ("systemctl", "stop"):
                self.state = "stopped"
            return subprocess.CompletedProcess(args, 0, "", "")
        self.enterContext(patch.object(manager, "run", side_effect=run))

    def fixture(self, module):
        self.module = "transport-" + module
        self.state = "running"
        self.reject = self.fail_restart = self.fail_health = self.interrupt = False
        self.path = manager.quic_root(self.module) / "config.json"
        manager.atomic_json(self.path, config(module, manager.module_settings(self.module)["port"]))
        self.original = self.path.read_bytes()
        self.settings = manager.SETTINGS_ROOT / f"{self.module}.json"
        manager.atomic_json(self.settings, manager.module_settings(self.module))
        self.before = self.settings.read_bytes()
        self.events.clear()
        self.telemetry.reset_mock()

    def apply(self, **values):
        return manager.patch_module_settings(self.module, manager.ModuleSettingsPatch(values=values))

    def test_running_and_stopped_apply_preserve_unedited_config(self):
        for module, edit in (("hysteria2", {"up_mbps": 200}), ("tuic", {"heartbeat": "15s"})):
            for state in ("running", "stopped"):
                with self.subTest(module=module, state=state):
                    self.fixture(module); self.state = state
                    before = json.loads(self.original)
                    self.apply(**edit)
                    after = json.loads(self.path.read_bytes())
                    expected = deepcopy(before); expected["inbounds"][0].update(edit)
                    self.assertEqual(after, expected)
                    self.assertEqual(self.state, state)
                    self.assertEqual(self.events[0][0], str(manager.SINGBOX_BIN))
                    self.assertEqual(any(args[:2] == ("systemctl", "restart") for args in self.events), state == "running")
                    self.assertFalse(any(args[0] == "ufw" for args in self.events))
                    self.assertEqual(self.telemetry.call_count, int(state == "running"))
                    self.install.assert_not_called()
                    self.assertFalse(profile_recovery.marker(manager).exists())

    def test_no_effect_settings_do_not_restart_or_rewrite(self):
        self.fixture("tuic")
        self.apply(heartbeat="10s")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.events, [])

    def test_missing_ambiguous_or_mismatched_listener_prevents_settings_write(self):
        self.fixture("hysteria2")
        original = json.loads(self.original)
        for value in ([], {"inbounds": []}, {"inbounds": ["invalid"]},
                      {**original, "inbounds": original["inbounds"] * 2},
                      {**original, "inbounds": [{**original["inbounds"][0], "listen_port": 9999}]}):
            with self.subTest(config=value):
                self.path.write_text(json.dumps(value))
                with self.assertRaises(manager.HTTPException) as failed:
                    self.apply(up_mbps=200)
                self.assertEqual(failed.exception.status_code, 409)
                self.assertEqual(self.settings.read_bytes(), self.before)
        self.path.unlink()
        with self.assertRaises(manager.HTTPException):
            self.apply(up_mbps=200)

    def test_unknown_runtime_prevents_write(self):
        self.fixture("tuic"); self.state = "unknown"
        with self.assertRaises(manager.HTTPException) as failed:
            self.apply(heartbeat="15s")
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_core_restart_and_health_failure_restore_exact_snapshot(self):
        for module in ("hysteria2", "tuic"):
            for failure in ("reject", "fail_restart", "fail_health"):
                with self.subTest(module=module, failure=failure):
                    self.fixture(module); setattr(self, failure, True)
                    with self.assertRaises(manager.HTTPException) as failed:
                        self.apply(**({"up_mbps": 200} if module == "hysteria2" else {"heartbeat": "15s"}))
                    self.assertEqual(failed.exception.status_code, 500)
                    self.assertNotIn("private credential", str(failed.exception.detail))
                    self.assertEqual(self.path.read_bytes(), self.original)
                    self.assertEqual(self.settings.read_bytes(), self.before)
                    self.assertEqual(self.state, "running")
                    self.assertFalse(self.path.with_name("config.candidate.json").exists())
                    self.assertFalse(profile_recovery.marker(manager).exists())

    def test_interruption_keeps_explicit_recovery(self):
        self.fixture("tuic"); self.interrupt = True
        with self.assertRaises(KeyboardInterrupt):
            self.apply(heartbeat="15s")
        self.assertTrue(profile_recovery.marker(manager).exists())
        profile_recovery.recover(manager)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.state, "running")

    def test_state_or_config_change_during_core_check_rejects_before_replace(self):
        for changed in ("state", "config"):
            with self.subTest(changed=changed):
                self.fixture("tuic")
                prepared = quic_settings.prepare(manager, self.module, manager.module_settings(self.module), {**manager.module_settings(self.module), "heartbeat": "15s"})
                def run(*args):
                    if changed == "state": self.state = "stopped"
                    else: self.path.write_text("external write")
                    return subprocess.CompletedProcess(args, 0, "", "")
                with patch.object(manager, "run", side_effect=run), self.assertRaises(RuntimeError):
                    quic_settings.apply(manager, self.module, prepared)
                self.assertEqual(self.path.read_bytes(), self.original if changed == "state" else b"external write")
                self.assertFalse(self.path.with_name("config.candidate.json").exists())

    def test_enabling_empty_hysteria_obfs_creates_secret_inside_snapshot_and_rolls_back(self):
        self.fixture("hysteria2")
        runtime = json.loads(self.original); runtime["inbounds"][0].pop("obfs"); runtime["inbounds"][0]["users"] = []
        manager.atomic_json(self.path, runtime); self.original = self.path.read_bytes()
        manager.atomic_json(self.settings, {**manager.module_settings(self.module), "obfs": False}); self.before = self.settings.read_bytes()
        secret = manager.CONFIG_ROOT / "quic/hysteria2/obfs.secret"
        self.reject = True
        with self.assertRaises(manager.HTTPException): self.apply(obfs=True)
        self.assertFalse(secret.exists())
        self.reject = False
        self.apply(obfs=True)
        self.assertEqual(json.loads(self.path.read_bytes())["inbounds"][0]["obfs"]["password"], secret.read_text().strip())

    def test_another_listener_port_is_preserved_and_cannot_be_taken(self):
        self.fixture("tuic")
        original = json.loads(self.original)
        original["inbounds"].append({"type": "hysteria2", "tag": "other", "listen_port": 21000})
        with self.assertRaises(RuntimeError):
            quic_settings.render(original, "tuic", {**manager.module_settings(self.module), "port": 21000})
        rendered = quic_settings.render(original, "tuic", {**manager.module_settings(self.module), "heartbeat": "15s"})
        self.assertEqual(rendered["inbounds"][1], original["inbounds"][1])

    def test_runtime_users_protect_breaking_edits_without_credential_files(self):
        for module, edit in (("hysteria2", {"obfs": False}), ("hysteria2", {"port": 21000}), ("tuic", {"port": 21000})):
            with self.subTest(module=module, edit=edit):
                self.fixture(module)
                with self.assertRaises(manager.HTTPException) as failed:
                    self.apply(**edit)
                self.assertEqual(failed.exception.status_code, 409)
                self.assertEqual(self.settings.read_bytes(), self.before)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_obfs_drift_does_not_change_existing_connections_on_compatible_edit(self):
        self.fixture("hysteria2")
        runtime = json.loads(self.original); runtime["inbounds"][0].pop("obfs")
        manager.atomic_json(self.path, runtime)
        with self.assertRaises(manager.HTTPException) as failed:
            self.apply(up_mbps=200)
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual(json.loads(self.path.read_bytes()), runtime)

    def test_empty_stopped_listener_port_change_updates_config_without_commands(self):
        self.fixture("tuic"); self.state = "stopped"
        runtime = json.loads(self.original); runtime["inbounds"][0]["users"] = []
        manager.atomic_json(self.path, runtime)
        self.apply(port=21000)
        self.assertEqual(json.loads(self.path.read_bytes())["inbounds"][0]["listen_port"], 21000)
        self.assertEqual(self.state, "stopped")
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], str(manager.SINGBOX_BIN))

    def test_invalid_existing_obfs_never_rotates_client_secret(self):
        self.fixture("hysteria2")
        for obfs in ("unknown", {"type": "other", "password": "fixture"}, {"type": "salamander", "password": 123}, {"type": "salamander"}):
            with self.subTest(obfs=obfs):
                runtime = json.loads(self.original); runtime["inbounds"][0]["obfs"] = obfs
                manager.atomic_json(self.path, runtime)
                with patch.object(manager, "quic_obfs_secret") as secret, self.assertRaises(manager.HTTPException) as failed:
                    self.apply(up_mbps=200)
                self.assertEqual(failed.exception.status_code, 409)
                self.assertEqual(self.settings.read_bytes(), self.before)
                secret.assert_not_called()

    @unittest.skipUnless(os.getenv("PRIVACY_SINGBOX_BIN") and os.getenv("PRIVACY_OPENSSL_BIN"), "Requires real sing-box and OpenSSL")
    def test_rendered_configs_are_accepted_by_real_singbox(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        certificate, key = self.root / "server.crt", self.root / "server.key"
        subprocess.run([os.environ["PRIVACY_OPENSSL_BIN"], "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=fixture", "-keyout", str(key), "-out", str(certificate)], check=True, capture_output=True, timeout=20, creationflags=flags)
        for module in ("hysteria2", "tuic"):
            self.fixture(module)
            original = json.loads(self.original)
            original["inbounds"][0]["tls"].update(certificate_path=str(certificate), key_path=str(key))
            values = {**manager.module_settings(self.module), **({"up_mbps": 200} if module == "hysteria2" else {"heartbeat": "15s"})}
            rendered = quic_settings.render(original, module, values)
            path = self.root / "candidate.json"; path.write_text(json.dumps(rendered))
            result = subprocess.run([os.environ["PRIVACY_SINGBOX_BIN"], "check", "-c", str(path)], capture_output=True, text=True, timeout=15, creationflags=flags)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
