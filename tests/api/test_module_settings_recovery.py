"""Durable settings/runtime recovery; no installed files or real services."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import manager, ROOT
import profile_recovery as recovery


class ModuleSettingsRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, path in {"PROFILE_FILE": self.root / "profiles.json", "CONFIG_ROOT": self.root / "config",
                           "ROUTING_SETTINGS_FILE": self.root / "routing.json", "SETTINGS_ROOT": self.root / "settings",
                           "WG_CONFIG_BY_MODULE": {"transport-wg": self.root / "wg.conf"},
                           "SERVICE_BY_MODULE": {"transport-wg": "fixture.service"}}.items():
            self.enterContext(patch.object(manager, name, path))
        manager.SETTINGS_ROOT.mkdir()
        manager.CONFIG_ROOT.mkdir()
        self.settings = manager.SETTINGS_ROOT / "transport-wg.json"
        self.settings.write_text('{"port":18000,"mtu":1280}\n')
        self.config = self.root / "wg.conf"
        self.config.write_text("original peers")
        self.before = self.settings.read_bytes()
        self.state = "running"
        self.enabled = "disabled"
        self.enterContext(patch.object(manager, "module_settings", side_effect=lambda _: json.loads(self.settings.read_text()) if self.settings.exists() else {"port": 18000, "mtu": 1280}))
        self.enterContext(patch.object(manager, "validate_settings", return_value={"port": 18000, "mtu": 1400}))
        self.enterContext(patch.object(manager, "module_is_ready", side_effect=lambda _: self.state == "running"))
        self.enterContext(patch.object(manager, "get_action_payload", return_value={}))
        self.enterContext(patch.object(manager, "get_module_settings", side_effect=lambda _: {"values": json.loads(self.settings.read_text())}))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "manifest", return_value={"name": "Fixture"}))
        self.enterContext(patch("tunnel_settings.prepare", return_value=(b"original peers", b"candidate peers")))
        self.enterContext(patch.object(manager, "observe_service", side_effect=lambda _: {"unit_present": True, "unit_file_state": self.enabled, "runtime": {"state": self.state}}))
        def run(*args, **kwargs):
            if args[:2] == ("systemctl", "stop"):
                self.state = "stopped"
            if args[:2] == ("systemctl", "disable"):
                self.enabled = "disabled"
            elif args[:2] == ("systemctl", "enable"):
                self.enabled = "enabled-runtime" if "--runtime" in args else "enabled"
            if args[:2] == ("systemctl", "restart"):
                self.assertEqual(self.config.read_text(), "original peers")
                self.assertEqual(self.settings.read_bytes(), self.before)
                self.state = "running"
        self.command = self.enterContext(patch.object(manager, "run", side_effect=run))

    def apply(self):
        return manager.patch_module_settings("transport-wg", manager.ModuleSettingsPatch(values={"mtu": 1400}))

    def test_interruption_preserves_snapshot_and_restores_settings_before_runtime(self):
        def interrupt(*_):
            self.assertTrue(recovery.marker(manager).exists())
            self.config.write_text("candidate peers")
            self.state = "error"
            raise KeyboardInterrupt()
        with patch("tunnel_settings.apply", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.apply()
        self.assertEqual(json.loads(self.settings.read_text())["mtu"], 1400)
        with self.assertRaisesRegex(RuntimeError, "Сначала восстановите"):
            self.apply()
        manager.recover_profile_runtime()
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual(self.state, "running")
        self.assertFalse(recovery.marker(manager).exists())
        self.command.reset_mock()
        manager.recover_profile_runtime()
        self.command.assert_not_called()

    def test_runtime_failure_rolls_back_without_replaying_install(self):
        def fail(*_):
            self.config.write_text("candidate peers")
            raise RuntimeError("apply failed")
        with patch("tunnel_settings.apply", side_effect=fail) as install, self.assertRaises(manager.HTTPException) as failed:
            self.apply()
        self.assertEqual(failed.exception.status_code, 500)
        install.assert_called_once()
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertFalse(recovery.marker(manager).exists())

    def test_unknown_runtime_prevents_settings_write(self):
        self.state = "unknown"
        with patch.object(manager, "call_module_script") as install, self.assertRaisesRegex(RuntimeError, "Неизвестно состояние"):
            self.apply()
        install.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_unknown_enablement_prevents_settings_write(self):
        self.enabled = "unknown"
        with patch.object(manager, "call_module_script") as install, self.assertRaisesRegex(RuntimeError, "автозапуска"):
            self.apply()
        install.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_recovery_restores_original_enablement(self):
        for saved, candidate in (("disabled", "enabled-runtime"), ("enabled-runtime", "enabled"), ("enabled", "disabled")):
            with self.subTest(saved=saved, candidate=candidate):
                self.enabled = saved
                recovery.prepare(manager, {"transport-wg"}, settings_modules={"transport-wg"})
                self.enabled = candidate
                manager.recover_profile_runtime()
                self.assertEqual(self.enabled, saved)
                self.assertFalse(recovery.marker(manager).exists())

    def test_old_settings_snapshot_without_enablement_remains_compatible(self):
        recovery.prepare(manager, {"transport-wg"}, settings_modules={"transport-wg"})
        value = json.loads(recovery.marker(manager).read_text())
        del value["enablement"]
        recovery.marker(manager).write_text(json.dumps(value))
        manager.recover_profile_runtime()
        self.assertFalse(recovery.marker(manager).exists())

    def test_stopped_module_saves_settings_without_starting_it(self):
        self.state = "stopped"
        with patch.object(manager, "call_module_script") as install:
            self.assertEqual(self.apply()["values"]["mtu"], 1400)
        install.assert_not_called()
        self.command.assert_not_called()
        self.assertFalse(recovery.marker(manager).exists())

    def test_interrupted_creation_restores_absence_of_settings_file(self):
        self.settings.unlink()
        self.state = "stopped"
        with patch.object(manager, "get_module_settings", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.apply()
        self.assertTrue(self.settings.exists())
        manager.recover_profile_runtime()
        self.assertFalse(self.settings.exists())
        self.command.assert_not_called()

    def test_quic_failure_restores_exact_runtime_and_settings_without_reapply(self):
        for module in ("transport-hysteria2", "transport-tuic"):
            with self.subTest(module=module):
                self.settings = manager.SETTINGS_ROOT / f"{module}.json"
                self.settings.write_bytes(self.before)
                self.config = manager.CONFIG_ROOT / f"{module}.json"
                self.config.write_text("original peers")
                self.state = "running"
                def fail(_):
                    self.config.write_text("candidate peers")
                    self.state = "error"
                    raise RuntimeError("quic apply failed")
                with (patch.object(manager, "SERVICE_BY_MODULE", {module: "fixture.service"}),
                      patch.object(manager, "write_quic_runtime", side_effect=fail) as writer,
                      patch.object(manager, "call_module_script") as install,
                      self.assertRaises(manager.HTTPException)):
                    manager.patch_module_settings(module, manager.ModuleSettingsPatch(values={"mtu": 1400}))
                writer.assert_called_once_with(module)
                install.assert_not_called()
                self.assertEqual(self.settings.read_bytes(), self.before)
                self.assertEqual(self.config.read_text(), "original peers")
                self.assertEqual(self.state, "running")

    def test_failed_settings_restore_retains_snapshot_and_does_not_restart(self):
        original = recovery.restore_file
        def restore(path, saved):
            if path == self.settings:
                raise OSError("settings unavailable")
            return original(path, saved)
        with (patch("tunnel_settings.apply", side_effect=RuntimeError("apply failed")),
              patch.object(recovery, "restore_file", side_effect=restore),
              self.assertRaisesRegex(RuntimeError, "rollback incomplete.*settings unavailable")):
            self.apply()
        self.assertTrue(recovery.marker(manager).exists())
        self.assertFalse(any(call.args[:2] == ("systemctl", "restart") for call in self.command.call_args_list))
        manager.recover_profile_runtime()
        self.assertEqual(self.settings.read_bytes(), self.before)

    def test_abrupt_worker_exit_keeps_settings_snapshot(self):
        program = '''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from tests.api.support import manager
root = Path(sys.argv[2])
manager.PROFILE_FILE = root/'profiles.json'
manager.CONFIG_ROOT = root/'config'
manager.ROUTING_SETTINGS_FILE = root/'routing.json'
manager.SETTINGS_ROOT = root/'settings'
manager.WG_CONFIG_BY_MODULE = {'transport-wg': root/'wg.conf'}
manager.SERVICE_BY_MODULE = {'transport-wg': 'fixture.service'}
def interrupt(*args):
    (root/'wg.conf').write_text('candidate peers')
    os._exit(77)
with patch.object(manager, 'module_settings', return_value={'port':18000,'mtu':1280}), patch.object(manager, 'validate_settings', return_value={'port':18000,'mtu':1400}), patch.object(manager, 'module_is_ready', return_value=True), patch.object(manager, 'observe_service', return_value={'unit_present':True,'unit_file_state':'disabled','runtime':{'state':'running'}}), patch.object(manager, 'get_action_payload', return_value={}), patch.object(manager, 'write_action'), patch.object(manager, 'manifest', return_value={'name':'Fixture'}), patch('tunnel_settings.prepare', return_value=(b'original peers',b'candidate peers')), patch('tunnel_settings.apply', side_effect=interrupt):
    manager.patch_module_settings('transport-wg', manager.ModuleSettingsPatch(values={'mtu':1400}))
'''
        result = subprocess.run([sys.executable, "-c", program, str(ROOT), str(self.root)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertTrue(recovery.marker(manager).exists())
        self.assertEqual(json.loads(self.settings.read_text())["mtu"], 1400)
        manager.recover_profile_runtime()
        self.assertEqual(self.settings.read_bytes(), self.before)
        self.assertEqual(self.config.read_text(), "original peers")

    def test_unknown_settings_module_in_manifest_is_rejected(self):
        recovery.prepare(manager, {"transport-wg"}, settings_modules={"transport-wg"})
        value = json.loads(recovery.marker(manager).read_text())
        value["settings_modules"] = ["../../outside"]
        recovery.marker(manager).write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            manager.recover_profile_runtime()
        self.command.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), self.before)
