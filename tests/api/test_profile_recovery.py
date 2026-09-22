import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import manager
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
