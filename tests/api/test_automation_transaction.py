import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.api.support import api
from automation_transaction import configure, recover, KINDS


class AutomationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.units = self.root / "units"
        self.units.mkdir()
        self.settings_file = self.root / "automation.json"
        self.before = api.default_automation()
        self.settings_file.write_text(json.dumps(self.before), encoding="utf-8")
        self.settings = {key: {**value, "enabled": True} for key, value in self.before.items()}
        self.active = {key: False for key in KINDS}
        self.enabled = dict(self.active)
        self.fail_apply = False
        self.calls = []
        self.legacy_active = self.legacy_enabled = True
        for kind in KINDS:
            for suffix in ("timer", "service"):
                (self.units / f"vps-control-auto-{kind}.{suffix}").write_text("original", encoding="utf-8")

    def fake_command(self, args, **kwargs):
        self.calls.append(args)
        output = ""
        code = 0
        kind = next((key for key in KINDS if f"vps-control-auto-{key}.timer" in args), None)
        if args[0] == "control":
            self.assertEqual(json.loads(self.settings_file.read_text()), self.before)
            self.assertEqual(json.loads(Path(args[-1]).read_text()), self.settings)
            self.assertEqual(kwargs["env"]["VPS_CONTROL_AUTOMATION_CHILD"], "1")
            for key in KINDS:
                self.active[key] = self.enabled[key] = True
                (self.units / f"vps-control-auto-{key}.timer").write_text("changed")
            self.legacy_active = self.legacy_enabled = False
            code = 1 if self.fail_apply else 0
        elif args[1] == "show":
            active = self.active[kind] if kind else self.legacy_active
            enabled = self.enabled[kind] if kind else self.legacy_enabled
            output = f"LoadState=loaded\nActiveState={'active' if active else 'inactive'}\nUnitFileState={'enabled' if enabled else 'disabled'}\n"
        elif args[1] == "disable":
            if kind: self.active[kind] = self.enabled[kind] = False
            else: self.legacy_active = self.legacy_enabled = False
        elif args[1] == "enable":
            if kind: self.enabled[kind] = True
            else: self.legacy_enabled = True
        elif args[1] == "start":
            if kind: self.active[kind] = True
            else: self.legacy_active = True
        return SimpleNamespace(returncode=code, stdout=output)

    def test_only_confirmed_timer_application_commits_settings(self):
        configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=self.fake_command)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.settings)
        self.assertTrue(all(self.active.values()))
        self.assertFalse(self.legacy_active)
        self.assertFalse(self.legacy_enabled)

    def test_failed_application_restores_timer_files_and_runtime(self):
        self.fail_apply = True
        self.active["cleanup"] = self.enabled["cleanup"] = True
        with self.assertRaisesRegex(RuntimeError, "восстановлены"):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=self.fake_command)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.before)
        self.assertEqual(self.active, {"reboot": False, "cleanup": True, "update": False})
        self.assertTrue(self.legacy_active)
        self.assertTrue(self.legacy_enabled)
        for path in self.units.iterdir():
            self.assertEqual(path.read_text(), "original")

    def test_unknown_timer_state_prevents_changes(self):
        def unavailable(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="")
        with self.assertRaises(RuntimeError):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=unavailable)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.before)

    def test_interruption_preserves_recovery_and_blocks_blind_reapply(self):
        def killed(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[0] == "control":
                raise KeyboardInterrupt()
            return result
        with self.assertRaises(KeyboardInterrupt):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=killed)
        recovery = self.settings_file.with_name("automation-recovery.json")
        snapshot = recovery.read_bytes()
        self.assertTrue(json.loads(snapshot)["files"])
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "прервана"):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=self.fake_command)
        self.assertEqual(recovery.read_bytes(), snapshot)
        self.assertEqual(self.calls, [])

    def test_successful_systemctl_exit_does_not_prove_rollback(self):
        self.fail_apply = True
        def ineffective_stop(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[:2] == ["systemctl", "disable"]:
                kind = next((key for key in KINDS if f"vps-control-auto-{key}.timer" in args), None)
                if kind: self.active[kind] = True
            return result
        with self.assertRaisesRegex(RuntimeError, "требуется проверка"):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=ineffective_stop)
        self.assertTrue(self.settings_file.with_name("automation-recovery.json").exists())


    def test_explicit_recovery_after_interruption_restores_original_and_is_idempotent(self):
        def killed(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[0] == "control":
                raise KeyboardInterrupt()
            return result
        with self.assertRaises(KeyboardInterrupt):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=killed)
        recover(self.settings_file, unit_dir=self.units, run=self.fake_command)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.before)
        self.assertFalse(any(self.active.values()))
        self.assertFalse(self.settings_file.with_name("automation-recovery.json").exists())
        self.calls.clear()
        recover(self.settings_file, unit_dir=self.units, run=self.fake_command)
        self.assertEqual(self.calls, [])

    def test_corrupt_snapshot_cannot_redirect_recovery_to_arbitrary_files(self):
        def killed(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[0] == "control":
                raise KeyboardInterrupt()
            return result
        with self.assertRaises(KeyboardInterrupt):
            configure(self.settings, self.settings_file, ["control"], unit_dir=self.units, run=killed)
        path = self.settings_file.with_name("automation-recovery.json")
        snapshot = json.loads(path.read_text())
        snapshot["files"][str(self.root / "foreign")] = None
        path.write_text(json.dumps(snapshot))
        self.calls.clear()
        with self.assertRaises(ValueError):
            recover(self.settings_file, unit_dir=self.units, run=self.fake_command)
        self.assertTrue(path.exists())
        self.assertEqual(self.calls, [])

    def test_legacy_timer_must_actually_be_disabled_before_committing(self):
        def legacy_still_active(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[0] == 'control': self.legacy_active = self.legacy_enabled = True
            return result
        with self.assertRaisesRegex(RuntimeError, 'восстановлены'):
            configure(self.settings, self.settings_file, ['control'], unit_dir=self.units, run=legacy_still_active)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.before)

    def test_previous_recovery_schema_remains_supported(self):
        def killed(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[0] == 'control': raise KeyboardInterrupt()
            return result
        with self.assertRaises(KeyboardInterrupt):
            configure(self.settings, self.settings_file, ['control'], unit_dir=self.units, run=killed)
        path = self.settings_file.with_name('automation-recovery.json')
        snapshot = json.loads(path.read_text())
        snapshot['states'].pop('apt-daily-upgrade.timer')
        path.write_text(json.dumps(snapshot), encoding='utf-8')
        recover(self.settings_file, unit_dir=self.units, run=self.fake_command)
        self.assertEqual(json.loads(self.settings_file.read_text()), self.before)
