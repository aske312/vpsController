import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from tests.api.support import manager, ROOT
import application_operation as operations
import profile_recovery as recovery


class ProfileRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, path in {"DATA_ROOT": self.root / "mihomo", "PROFILE_FILE": self.root / "mihomo/profiles.json",
                           "CONFIG_ROOT": self.root / "config", "ROUTING_SETTINGS_FILE": self.root / "mihomo/routing.json"}.items():
            self.enterContext(patch.object(manager, name, path))
        manager.PROFILE_FILE.parent.mkdir()
        manager.CONFIG_ROOT.mkdir()
        manager.PROFILE_FILE.write_text('[{"id":"original"}]')
        (manager.CONFIG_ROOT / "peer.json").write_text('{"original":true}')
        self.state = "running"
        self.enterContext(patch.object(manager, "SERVICE_BY_MODULE", {"transport-wg": "fixture.service"}))
        self.enterContext(patch.object(manager, "WG_CONFIG_BY_MODULE", {}))
        self.observation = self.enterContext(patch.object(manager, "observe_service", side_effect=lambda _: {"unit_present": True, "runtime": {"state": self.state}}))
        def run(*args, **kwargs):
            if args[:2] == ("systemctl", "restart"):
                self.assertEqual(json.loads(manager.PROFILE_FILE.read_text()), [{"id": "original"}])
                self.state = "running"
        self.command = self.enterContext(patch.object(manager, "run", side_effect=run))

    def interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            with manager.profile_runtime_transaction({"transport-wg"}):
                manager.PROFILE_FILE.write_text('[]')
                (manager.CONFIG_ROOT / "created.json").write_text('{}')
                self.state = "error"
                raise KeyboardInterrupt()

    def test_interrupted_snapshot_survives_and_recovery_is_idempotent(self):
        self.interrupt()
        self.assertTrue(recovery.marker(manager).exists())
        with self.assertRaises(operations.OperationConflict):
            with operations.short_mutation(self.root):
                self.fail("Recovery must block unrelated mutations")
        manager.recover_profile_runtime()
        self.assertFalse(recovery.marker(manager).exists())
        self.assertFalse((manager.CONFIG_ROOT / "created.json").exists())
        self.assertEqual(self.state, "running")
        self.command.reset_mock()
        manager.recover_profile_runtime()
        self.command.assert_not_called()

    def test_reality_recovery_applies_firewall_without_nested_control_operation(self):
        real_is_file = Path.is_file
        descriptor = manager.CONFIG_ROOT / 'reality/caddy-routes/cdn.json'
        descriptor.parent.mkdir(parents=True)
        original = {'domain':'cdn.example','path':'/cdn','port':10087,'relay_origins':['85.209.155.229']}
        descriptor.write_text(json.dumps(original))

        def is_file(path):
            if str(path).replace("\\", "/") == "/opt/vps-control/api/cdn_security.py":
                return True
            return real_is_file(path)

        with patch.object(manager, "SERVICE_BY_MODULE", {"transport-reality": "fixture.service"}):
            with self.assertRaises(KeyboardInterrupt):
                with manager.profile_runtime_transaction({"transport-reality"}):
                    manager.PROFILE_FILE.write_text('[]')
                    descriptor.write_text(json.dumps({**original, 'relay_origins':['8.8.4.4']}))
                    (descriptor.parent/'new.json').write_text('{}')
                    self.state = "error"
                    raise KeyboardInterrupt()
            with (
                patch.object(recovery.Path, "is_file", autospec=True, side_effect=is_file),
                patch.object(manager, "rebuild_vless_cdn_snippet"),
            ):
                manager.recover_profile_runtime()
        self.assertIn(
            ("/usr/bin/python3", str(Path("/opt/vps-control/api/cdn_security.py")), "firewall"),
            [call.args for call in self.command.call_args_list],
        )
        self.assertFalse(recovery.marker(manager).exists())
        self.assertEqual(json.loads(descriptor.read_text()), original)
        self.assertFalse((descriptor.parent/'new.json').exists())

    def test_restore_error_retains_snapshot_and_other_files_restore(self):
        self.interrupt()
        original = recovery.restore_file
        def fail(path, saved):
            if path == manager.PROFILE_FILE:
                raise OSError("disk unavailable")
            return original(path, saved)
        (manager.CONFIG_ROOT / "peer.json").write_text('{"changed":true}')
        with patch.object(recovery, "restore_file", side_effect=fail), self.assertRaises(RuntimeError):
            manager.recover_profile_runtime()
        self.assertTrue(recovery.marker(manager).exists())
        self.assertEqual(json.loads((manager.CONFIG_ROOT / "peer.json").read_text()), {"original": True})
        self.command.assert_not_called()

    def test_unknown_preflight_and_corrupt_snapshot_never_mutate(self):
        self.state = "unknown"
        with self.assertRaises(RuntimeError):
            with manager.profile_runtime_transaction({"transport-wg"}):
                self.fail("Unknown runtime must prevent mutation")
        self.assertFalse(recovery.marker(manager).exists())
        self.state = "running"
        self.interrupt()
        path = recovery.marker(manager)
        value = json.loads(path.read_text())
        value["config"]["../outside"] = next(iter(value["config"].values()))
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            manager.recover_profile_runtime()
        self.assertTrue(path.exists())
        self.command.assert_not_called()

    def test_restore_file_reapplies_captured_owner(self):
        target = self.root / "owned.json"
        saved = {"content": b'{}', "mode": 0o640, "uid": 123, "gid": 456}
        with patch.object(recovery.os, "chown", create=True) as chown:
            recovery.restore_file(target, saved)
        self.assertEqual(target.read_bytes(), b'{}')
        chown.assert_called_once_with(ANY, 123, 456)


class PersonalRuleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, filename in {"PROFILE_FILE": "profiles.json", "ROUTING_SETTINGS_FILE": "routing.json",
                               "PERSONAL_RULES_FILE": "personal-rules.json", "CONFIG_ROOT": "config"}.items():
            self.enterContext(patch.object(manager, name, self.root / filename))
        manager.CONFIG_ROOT.mkdir()
        self.rule_id = "personal-aaaaaaaaaaaa"
        self.rules = [{"id": self.rule_id, "kind": "domain", "entries": ["example.test"]},
                      {"id": "personal-bbbbbbbbbbbb", "kind": "domain", "entries": ["other.test"]}]
        self.profiles = [{"id": "one", "devices": [{"id": "phone", "personal_rule_ids": [self.rule_id, self.rules[1]["id"]]},
                                                   {"id": "tablet", "personal_rule_ids": [self.rule_id]}]},
                         {"id": "unrelated", "devices": [{"id": "pc", "personal_rule_ids": [self.rules[1]["id"]]}]}]
        manager.save_personal_rules(self.rules)
        manager.save_profiles(self.profiles)
        self.before = {path: path.read_bytes() for path in (manager.PROFILE_FILE, manager.PERSONAL_RULES_FILE)}
        # Keep this test focused on mutation/recovery, independently of legacy normalization.
        self.enterContext(patch.object(manager, "profiles", side_effect=lambda: json.loads(manager.PROFILE_FILE.read_text())))
        self.command = self.enterContext(patch.object(manager, "run"))
        self.observation = self.enterContext(patch.object(manager, "observe_service"))
        self.enterContext(patch.object(manager, "get_action_payload", return_value={"id": "fixture"}))

    def assert_restored(self):
        for path, content in self.before.items():
            self.assertEqual(path.read_bytes(), content)
        self.command.assert_not_called()
        self.observation.assert_not_called()

    def test_second_write_failure_restores_rule_and_all_references(self):
        def fail(_):
            self.assertNotIn(self.rule_id, [rule["id"] for rule in manager.personal_rules()])
            raise OSError("profile disk failure")
        with patch.object(manager, "save_profiles", side_effect=fail), self.assertRaisesRegex(OSError, "profile disk failure"):
            manager.delete_personal_rule(self.rule_id)
        self.assert_restored()
        self.assertFalse(recovery.marker(manager).exists())

    def test_first_write_failure_keeps_profiles_and_rules(self):
        with patch.object(manager, "save_personal_rules", side_effect=OSError("rule disk failure")), self.assertRaisesRegex(OSError, "rule disk failure"):
            manager.delete_personal_rule(self.rule_id)
        self.assert_restored()
        self.assertFalse(recovery.marker(manager).exists())

    def test_rule_recovery_rejects_a_different_target_before_restoring_files(self):
        recovery.prepare(manager, set(), include_personal_rules=True)
        manager.PERSONAL_RULES_FILE.write_text("[]")
        value = json.loads(recovery.marker(manager).read_text())
        value["targets"]["personal-rules"] = str((self.root / "outside.json").resolve())
        recovery.marker(manager).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "does not match"):
            manager.recover_profile_runtime()
        self.assertEqual(manager.PERSONAL_RULES_FILE.read_text(), "[]")
        self.assertTrue(recovery.marker(manager).exists())
        self.assertFalse((self.root / "outside.json").exists())
        self.command.assert_not_called()

    def test_success_removes_only_selected_references_and_repeat_is_safe(self):
        self.assertEqual(manager.delete_personal_rule(self.rule_id), {"removed": self.rule_id})
        saved = json.loads(manager.PROFILE_FILE.read_text())
        self.assertEqual(saved[0]["devices"][0]["personal_rule_ids"], [self.rules[1]["id"]])
        self.assertEqual(saved[0]["devices"][1]["personal_rule_ids"], [])
        self.assertEqual(saved[1], self.profiles[1])
        self.assertEqual([rule["id"] for rule in manager.personal_rules()], [self.rules[1]["id"]])
        self.assertEqual(manager.delete_personal_rule(self.rule_id), {"removed": self.rule_id, "already_removed": True})
        self.assertFalse(recovery.marker(manager).exists())
        self.command.assert_not_called()
        self.observation.assert_not_called()

    def test_abrupt_process_exit_retains_both_files_for_explicit_recovery(self):
        program = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0] = [sys.argv[1] + '/api', sys.argv[1] + '/protocol-images/mihomo']
if sys.platform == 'win32':
    import types
    sys.modules['fcntl'] = types.ModuleType('fcntl')
import profile_recovery
root = Path(sys.argv[2])
api = SimpleNamespace(PROFILE_FILE=root/'profiles.json', ROUTING_SETTINGS_FILE=root/'routing.json',
    PERSONAL_RULES_FILE=root/'personal-rules.json', CONFIG_ROOT=root/'config',
    TRANSPORTS=set(), WG_CONFIG_BY_MODULE={}, SERVICE_BY_MODULE={}, get_action_payload=lambda: {})
with profile_recovery.transaction(api, set(), include_personal_rules=True):
    api.PERSONAL_RULES_FILE.write_text('[]')
    os._exit(77)
'''
        result = subprocess.run([sys.executable, "-c", program, str(ROOT), str(self.root)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertTrue(recovery.marker(manager).exists())
        with self.assertRaisesRegex(RuntimeError, "Сначала восстановите"):
            with manager.profile_runtime_transaction(set(), include_personal_rules=True):
                self.fail("A pending recovery must prevent another transaction")
        manager.recover_profile_runtime()
        self.assert_restored()
        manager.recover_profile_runtime()
        self.assertFalse(recovery.marker(manager).exists())

    def test_failed_rollback_retains_marker_until_recovery_succeeds(self):
        original = recovery.restore_file
        def fail(path, saved):
            if path == manager.PERSONAL_RULES_FILE:
                raise OSError("rule disk unavailable")
            return original(path, saved)
        with (patch.object(manager, "save_profiles", side_effect=OSError("profile disk failure")),
              patch.object(recovery, "restore_file", side_effect=fail),
              self.assertRaisesRegex(RuntimeError, "rollback incomplete.*rule disk unavailable")):
            manager.delete_personal_rule(self.rule_id)
        self.assertTrue(recovery.marker(manager).exists())
        with self.assertRaisesRegex(RuntimeError, "Сначала восстановите"):
            manager.delete_personal_rule(self.rules[1]["id"])
        manager.recover_profile_runtime()
        self.assert_restored()
        self.assertFalse(recovery.marker(manager).exists())
