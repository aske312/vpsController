"""Managed settings apply preserves VLESS credentials and observed runtime."""
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
import reality_settings


def values():
    return {"api_port": 10086, "port_start": 9443, "cdn_port_start": 10443, "tls_port_start": 11443,
            "dns": "1.1.1.1, 1.0.0.1", "loglevel": "warning"}


def config():
    return {"log": {"loglevel": "warning", "access": "none"}, "dns": {"servers": ["1.1.1.1", "1.0.0.1"], "queryStrategy": "UseIP"},
            "api": {"tag": "api", "services": ["StatsService"]}, "stats": {},
            "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True, "statsUserOnline": True}}},
            "inbounds": [{"tag": "mihomo-vless-example", "listen": "127.0.0.1", "port": 9443, "protocol": "vless",
                          "settings": {"decryption": "none", "clients": [{"id": "00000000-0000-4000-8000-000000000001"}]}},
                         {"tag": "api", "listen": "127.0.0.1", "port": 10086, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}],
            "routing": {"rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]},
            "outbounds": [{"protocol": "freedom", "tag": "direct"}]}


class RealitySettingsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, value in {"PROFILE_FILE": self.root / "profiles.json", "ROUTING_SETTINGS_FILE": self.root / "routing.json",
                            "CONFIG_ROOT": self.root / "config", "SETTINGS_ROOT": self.root / "settings",
                            "SUBMODULE_ROOT": ROOT / "protocol-images/mihomo/modules"}.items():
            self.enterContext(patch.object(manager, name, value))
        self.path = manager.CONFIG_ROOT / "reality/config.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(config()))
        self.settings = manager.SETTINGS_ROOT / "transport-reality.json"
        manager.SETTINGS_ROOT.mkdir()
        self.settings.write_text(json.dumps(values()))
        self.original = self.path.read_bytes()
        self.before_settings = self.settings.read_bytes()
        self.state = "running"
        self.events = []
        self.fail_start = False
        self.fail_health = False
        self.interrupt = False
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "module_is_ready", side_effect=lambda _: self.state == "running"))
        self.enterContext(patch.object(manager, "observe_service", side_effect=lambda _: {"unit_present": True, "unit_file_state": "disabled", "runtime": {"state": self.state}}))
        self.enterContext(patch.object(manager, "check_module_port"))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "get_action_payload", return_value={}))
        self.enterContext(patch.object(manager, "ensure_reality_config_permissions"))
        self.enterContext(patch.object(manager.shutil, "chown"))
        self.enterContext(patch.object(manager, "rebuild_vless_cdn_snippet"))
        self.install = self.enterContext(patch.object(manager, "call_module_script"))
        self.enterContext(patch.object(manager, "service_stably_active", side_effect=lambda _: not self.fail_health))
        def run(*args, **kwargs):
            self.events.append((args, self.path.read_bytes()))
            if args[:2] == ("systemctl", "restart"):
                if self.path.read_bytes() != self.original:
                    self.state = "error"
                    if self.interrupt:
                        raise KeyboardInterrupt()
                    if self.fail_start:
                        raise RuntimeError("candidate failed")
                self.state = "running"
            elif args[:2] == ("systemctl", "stop"):
                self.state = "stopped"
            return subprocess.CompletedProcess(args, 0, "", "")
        self.enterContext(patch.object(manager, "run", side_effect=run))

    def apply(self, **changes):
        return manager.patch_module_settings("transport-reality", manager.ModuleSettingsPatch(values=changes))

    def test_render_changes_only_global_dns_log_and_local_api(self):
        original = config()
        original["inbounds"][0]["streamSettings"] = {"realitySettings": {"privateKey": "fixture", "shortIds": ["fixture"]}}
        before = deepcopy(original)
        result = reality_settings.render(original, {**values(), "api_port": 10087, "dns": "9.9.9.9, 149.112.112.112", "loglevel": "error"})
        self.assertEqual(original, before)
        self.assertEqual(result["inbounds"][0], before["inbounds"][0])
        self.assertEqual(result["inbounds"][1]["listen"], "127.0.0.1")
        self.assertEqual(result["inbounds"][1]["port"], 10087)
        self.assertEqual(result["dns"], {"servers": ["9.9.9.9", "149.112.112.112"], "queryStrategy": "UseIP"})
        self.assertEqual(result["log"]["loglevel"], "error")
        for key in ("routing", "outbounds", "policy", "api", "stats"):
            self.assertEqual(result[key], before[key])

    def test_invalid_or_ambiguous_managed_config_prevents_any_write(self):
        cases = [[], {"inbounds": []}, {"inbounds": ["invalid"]}, {**config(), "log": "invalid"},
                 {**config(), "inbounds": [*config()["inbounds"], config()["inbounds"][1]]}]
        for value in cases:
            with self.subTest(config=value):
                self.path.write_text(json.dumps(value))
                with patch.object(manager, "atomic_json") as write, self.assertRaises(manager.HTTPException) as failed:
                    self.apply(loglevel="error")
                self.assertEqual(failed.exception.status_code, 409)
                write.assert_not_called()
        self.path.write_bytes(self.original)
        with self.assertRaises(manager.HTTPException) as failed:
            self.apply(api_port=9443)
        self.assertEqual(failed.exception.status_code, 409)
        self.assertEqual(self.settings.read_bytes(), self.before_settings)

    def test_running_apply_checks_candidate_and_retains_credentials_without_installer(self):
        result = self.apply(dns="9.9.9.9", loglevel="error", api_port=10087)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["inbounds"][0], config()["inbounds"][0])
        self.assertEqual(saved["dns"]["servers"], ["9.9.9.9"])
        self.assertEqual(result["values"]["api_port"], 10087)
        self.assertEqual(self.state, "running")
        commands = [event[0] for event in self.events]
        check = (str(manager.REALITY_XRAY_BIN), "run", "-test", "-config", str(self.path.with_name("config.candidate.json")))
        restart = ("systemctl", "restart", manager.SERVICE_BY_MODULE["transport-reality"])
        self.assertLess(commands.index(check), commands.index(restart))
        self.install.assert_not_called()
        self.assertFalse(profile_recovery.marker(manager).exists())

    def test_stopped_apply_updates_config_without_starting_or_enabling_service(self):
        self.state = "stopped"
        self.apply(loglevel="error")
        self.assertEqual(json.loads(self.path.read_text())["log"]["loglevel"], "error")
        self.assertEqual(self.state, "stopped")
        self.assertFalse(any(args[0] == "systemctl" for args, _ in self.events))
        self.install.assert_not_called()

    def test_allocation_only_change_does_not_restart_or_rewrite_existing_runtime(self):
        self.apply(port_start=19000)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.events, [])
        self.install.assert_not_called()

    def test_restart_and_health_failure_restore_exact_settings_config_and_runtime(self):
        for failure in ("fail_start", "fail_health"):
            with self.subTest(failure=failure):
                setattr(self, failure, True)
                with self.assertRaises(manager.HTTPException) as failed:
                    self.apply(loglevel="error")
                self.assertEqual(failed.exception.status_code, 500)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(self.settings.read_bytes(), self.before_settings)
                self.assertEqual(self.state, "running")
                self.assertFalse(profile_recovery.marker(manager).exists())
                setattr(self, failure, False)
        self.install.assert_not_called()

    def test_core_rejection_restores_settings_before_restarting_saved_config(self):
        def reject(*args, **kwargs):
            if args[0] == str(manager.REALITY_XRAY_BIN):
                return subprocess.CompletedProcess(args, 1, "", "candidate rejected")
            if args[:2] == ("systemctl", "restart"):
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(self.settings.read_bytes(), self.before_settings)
            return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(manager, "run", side_effect=reject), self.assertRaises(manager.HTTPException) as failed:
            self.apply(loglevel="error")
        self.assertEqual(failed.exception.status_code, 500)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.settings.read_bytes(), self.before_settings)
        self.assertFalse(self.path.with_name("config.candidate.json").exists())
        self.assertFalse(profile_recovery.marker(manager).exists())
        self.install.assert_not_called()

    def test_failed_worker_restores_the_previous_settings_receipt(self):
        with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": "a" * 32}):
            self.apply(loglevel="error")
        self.original = self.path.read_bytes()
        self.before_settings = self.settings.read_bytes()
        self.fail_health = True
        with patch.dict(os.environ, {"VPS_CONTROL_MIHOMO_OPERATION_ID": "b" * 32}), self.assertRaises(manager.HTTPException):
            self.apply(loglevel="debug")
        self.assertEqual(self.settings.read_bytes(), self.before_settings)
        self.assertEqual(manager.get_module_settings("transport-reality")["last_operation_id"], "a" * 32)

    def test_interrupted_apply_retains_snapshot_until_explicit_recovery(self):
        self.interrupt = True
        with self.assertRaises(KeyboardInterrupt):
            self.apply(loglevel="error")
        self.assertTrue(profile_recovery.marker(manager).exists())
        manager.recover_profile_runtime()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.settings.read_bytes(), self.before_settings)
        self.assertEqual(self.state, "running")
        self.assertFalse(profile_recovery.marker(manager).exists())
        self.install.assert_not_called()

    def test_unknown_state_and_changed_config_fail_before_apply(self):
        self.state = "unknown"
        with self.assertRaises(manager.HTTPException):
            self.apply(loglevel="error")
        self.assertEqual(self.settings.read_bytes(), self.before_settings)
        self.state = "running"
        prepared = reality_settings.prepare(manager, {**values(), "loglevel": "error"})
        self.path.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, "изменились"):
            reality_settings.apply(manager, prepared)
        self.assertEqual(self.events, [])

    def test_shadowsocks_allocation_setting_keeps_existing_instances_untouched(self):
        with patch.object(manager.ss_runtime, "snapshot", return_value={}):
            result = manager.patch_module_settings("transport-shadowsocks", manager.ModuleSettingsPatch(values={"port_start": 32000}))
        self.assertEqual(result["values"]["port_start"], 32000)
        self.assertEqual(self.events, [])
        self.install.assert_not_called()

    @unittest.skipUnless(os.getenv("PRIVACY_XRAY_BIN"), "Requires a real local Xray binary")
    def test_rendered_config_is_accepted_by_real_xray(self):
        candidate = reality_settings.render(config(), {**values(), "api_port": 10087, "dns": "9.9.9.9", "loglevel": "error"})
        path = self.root / "xray.json"
        path.write_text(json.dumps(candidate))
        result = subprocess.run([os.environ["PRIVACY_XRAY_BIN"], "run", "-test", "-config", str(path)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
