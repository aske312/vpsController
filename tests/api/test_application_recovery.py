import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import ROOT, load_module
import application_operation as operations

recovery = load_module("application_recovery", "api/application_recovery.py")


class ApplicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.root / "data"
        self.installed = self.root / "app"
        self.previous = self.root / "app.rollback.fixture"
        self.gateway = self.root / "Caddyfile"
        self.unit = self.root / "api.service"
        self.snippets = self.root / "snippets"
        self.installed.mkdir()
        self.snippets.mkdir()
        (self.installed / "release").write_text("previous")
        (self.installed / "venv").mkdir()
        (self.installed / "venv/runtime").write_text("runtime")
        self.gateway.write_text("previous gateway")
        self.unit.write_text("previous unit")
        (self.snippets / "old.caddy").write_text("previous route")
        self.commands = []
        self.failure = None
        self.enterContext(patch.object(recovery, "run", side_effect=self.command))

    def command(self, *command):
        self.commands.append(command)
        if command[:2] == self.failure:
            raise subprocess.CalledProcessError(17, command)
        return "manager.service enabled" if command[1] == "list-unit-files" else ""

    def prepare(self):
        return recovery.prepare(self.data, self.installed, self.previous, [self.gateway, self.unit], self.snippets, "1" * 32)

    def swap(self):
        value = self.prepare()
        self.installed.rename(self.previous)
        self.installed.mkdir()
        (self.installed / "release").write_text("candidate")
        shutil.move(str(self.previous / "venv"), str(self.installed / "venv"))
        self.gateway.write_text("candidate gateway")
        self.unit.write_text("candidate unit")
        (self.snippets / "old.caddy").unlink()
        (self.snippets / "new.caddy").write_text("candidate route")
        return value

    def assert_blocked(self):
        with self.assertRaisesRegex(operations.OperationConflict, "приложения"):
            operations.require_recovery_clear(self.data)
        self.assertTrue(recovery.marker(self.data).exists())
        self.assertTrue((self.data / recovery.DIRECTORY / "recover.py").is_file())

    def assert_restored(self):
        self.assertEqual((self.installed / "release").read_text(), "previous")
        self.assertEqual((self.installed / "venv/runtime").read_text(), "runtime")
        self.assertEqual(self.gateway.read_text(), "previous gateway")
        self.assertEqual(self.unit.read_text(), "previous unit")
        self.assertEqual((self.snippets / "old.caddy").read_text(), "previous route")
        self.assertFalse((self.snippets / "new.caddy").exists())
        self.assertFalse(recovery.marker(self.data).exists())
        self.assertFalse((self.data / recovery.DIRECTORY).exists())
        operations.require_recovery_clear(self.data)

    def test_rollback_retains_candidate_and_restores_exact_configuration(self):
        value = self.swap()
        recovery.rollback(self.data)
        self.assert_restored()
        self.assertEqual((Path(value["failed"]) / "release").read_text(), "candidate")
        self.assertTrue(any(command[:2] == ("curl", "--fail") for command in self.commands))

    def test_restart_gateway_manager_and_health_failure_survive_process_exit(self):
        for failure in [("systemctl", "stop"), ("systemctl", "restart"), ("systemctl", "reload-or-restart"),
                        ("systemctl", "is-active"), ("curl", "--fail")]:
            with self.subTest(failure=failure):
                # Each attempt is a new isolated transaction.
                self.failure = failure
                value = self.swap()
                with self.assertRaises(subprocess.CalledProcessError):
                    recovery.rollback(self.data)
                self.assert_blocked()
                self.failure = None
                # A fresh import of the retained standalone file has no API or
                # application-tree dependencies, including on an older release.
                spec = importlib.util.spec_from_file_location("retained_recovery", self.data / recovery.DIRECTORY / "recover.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with patch.object(module, "run", side_effect=self.command):
                    module.rollback(self.data)
                self.assert_restored()
                shutil.rmtree(Path(value["failed"]))
                self.commands.clear()

    def test_interruption_before_between_and_after_directory_renames_is_resumable(self):
        for stage in ("before", "old-moved", "candidate-moved", "restored"):
            with self.subTest(stage=stage):
                value = self.prepare()
                if stage != "before":
                    self.installed.rename(self.previous)
                if stage in {"candidate-moved", "restored"}:
                    failed = Path(value["failed"])
                    failed.mkdir(parents=True)
                    (failed / "release").write_text("candidate")
                if stage == "restored":
                    self.previous.rename(self.installed)
                recovery.rollback(self.data)
                self.assert_restored()

    def test_unrelated_security_marker_is_never_replaced_or_removed(self):
        recovery.marker(self.data).parent.mkdir(parents=True)
        recovery.marker(self.data).write_text("unrelated security recovery")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(recovery.marker(self.data).read_text(), "unrelated security recovery")

    def test_replaced_owner_corrupt_manifest_and_missing_previous_fail_closed(self):
        value = self.swap()
        recovery.marker(self.data).write_text(json.dumps({"kind": "security", "token": "other"}))
        with self.assertRaises(ValueError):
            recovery.rollback(self.data)
        self.assertEqual((self.installed / "release").read_text(), "candidate")
        recovery.atomic(recovery.marker(self.data), {"kind": value["kind"], "token": value["token"]})
        manifest = self.data / recovery.DIRECTORY / "manifest.json"
        manifest.write_text("broken")
        with self.assertRaises(ValueError):
            recovery.rollback(self.data)
        recovery.atomic(manifest, value)
        shutil.rmtree(self.previous)
        with self.assertRaises(OSError):
            recovery.rollback(self.data)
        self.assert_blocked()

    def test_recovery_does_not_allow_other_recovery_mutations_or_prune_owner_history(self):
        self.prepare()
        for action in ("update", "ssh-access-disable", "dns-recover", "automation-recover", "mihomo-module-recover:profiles"):
            with self.assertRaises(operations.OperationConflict):
                operations.require_recovery_clear(self.data, action)
        for action in ("network-check", "integrity-check", "doctor"):
            operations.require_recovery_clear(self.data, action)
        history = self.data / "operations" / ("1" * 32 + ".json")
        operations.atomic_json(history, {"id": "1" * 32, "state": "failed"})
        operations.prune(self.data, None, byte_budget=0)
        self.assertTrue(history.exists())

    def test_compatibility_barrier_blocks_the_prechange_admission_code(self):
        self.prepare()
        source = (ROOT / "tests/fixtures/legacy-application-recovery-guard.py").read_text(encoding="utf-8")
        namespace = {"__name__": "legacy_application_operation"}
        exec(compile(source, "legacy_operation.py", "exec"), namespace)
        with self.assertRaises(namespace["OperationConflict"]):
            namespace["require_recovery_clear"](self.data)

    def test_commit_requires_health_and_preserves_old_tree_on_failure(self):
        self.swap()
        self.failure = ("curl", "--fail")
        with self.assertRaises(subprocess.CalledProcessError):
            recovery.commit(self.data)
        self.assert_blocked()
        self.assertTrue(self.previous.is_dir())
        self.failure = None
        recovery.commit(self.data)
        self.assertFalse(recovery.marker(self.data).exists())
        self.assertEqual((self.installed / "release").read_text(), "candidate")
        self.assertTrue(self.previous.is_dir())

    def test_preparation_failure_is_recoverable_without_stopping_services(self):
        with patch.object(recovery.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.prepare()
        # Before persisting the helper no swap or barrier is possible.
        self.assertEqual((self.installed / "release").read_text(), "previous")
        self.assertEqual(self.commands, [])

    def test_snapshot_failure_leaves_a_retained_helper_and_original_release(self):
        original = recovery.shutil.copy2
        def copy(source, target, *args, **kwargs):
            if Path(source) == self.unit:
                raise OSError("snapshot full")
            return original(source, target, *args, **kwargs)
        with patch.object(recovery.shutil, "copy2", side_effect=copy):
            with self.assertRaises(OSError):
                self.prepare()
        self.assert_blocked()
        recovery.rollback(self.data)
        self.assertEqual(self.commands, [])
        self.assertEqual((self.installed / "release").read_text(), "previous")
        operations.require_recovery_clear(self.data)

    def test_failed_commit_cleanup_retains_helper_and_cannot_revert_accepted_release(self):
        self.swap()
        original = recovery.Path.rename
        def rename(path, target):
            if path == self.data / recovery.DIRECTORY:
                raise OSError("archive unavailable")
            return original(path, target)
        with patch.object(recovery.Path, "rename", rename), self.assertRaises(OSError):
            recovery.commit(self.data)
        self.assertEqual(recovery.status(self.data), {"application_required": True, "application_command": "commit"})
        with self.assertRaises(ValueError):
            recovery.rollback(self.data)
        self.assertTrue((self.data / recovery.DIRECTORY / "recover.py").is_file())
        recovery.commit(self.data)
        operations.require_recovery_clear(self.data)
        self.assertEqual((self.installed / "release").read_text(), "candidate")

    def test_guard_is_rechecked_after_acquiring_the_worker_lock(self):
        from contextlib import contextmanager
        @contextmanager
        def acquire(data):
            self.prepare()
            yield
        with patch.object(operations, "mutation_lock", acquire):
            with self.assertRaises(operations.OperationConflict):
                with operations.short_mutation(self.data):
                    self.fail("Mutation entered after recovery began")

    def test_failed_test_rollback_restores_test_tree_and_keeps_production_backup(self):
        backup = self.data / "test-app-backup"
        backup.mkdir(parents=True)
        (backup / "release").write_text("production")
        (self.installed / "release").write_text("test")
        value = recovery.prepare(self.data, self.installed, self.previous, [self.gateway, self.unit],
                                 self.snippets, "1" * 32, candidate_backup=backup)
        self.installed.rename(self.previous)
        backup.rename(self.installed)
        shutil.move(str(self.previous / "venv"), str(self.installed / "venv"))
        self.failure = ("systemctl", "restart")
        with self.assertRaises(subprocess.CalledProcessError):
            recovery.rollback(self.data)
        self.assert_blocked()
        self.failure = None
        recovery.rollback(self.data)
        self.assertEqual((self.installed / "release").read_text(), "test")
        self.assertEqual((backup / "release").read_text(), "production")
        self.assertEqual((self.installed / "venv/runtime").read_text(), "runtime")
        self.assertEqual(value["failed"], str(backup))

    def test_interrupted_test_rollback_before_production_move_keeps_both_trees(self):
        backup = self.data / "test-app-backup"
        backup.mkdir(parents=True)
        (backup / "release").write_text("production")
        recovery.prepare(self.data, self.installed, self.previous, [self.gateway, self.unit], self.snippets,
                         "1" * 32, candidate_backup=backup)
        self.installed.rename(self.previous)
        recovery.rollback(self.data)
        self.assert_restored()
        self.assertEqual((backup / "release").read_text(), "production")

    def test_candidate_collision_is_rejected_before_stopping_services(self):
        value = self.swap()
        failed = Path(value["failed"])
        failed.mkdir(parents=True)
        (failed / "foreign").write_text("must survive")
        with self.assertRaises(ValueError):
            recovery.rollback(self.data)
        self.assertEqual(self.commands, [])
        self.assertEqual((failed / "foreign").read_text(), "must survive")
        self.assert_blocked()

    @unittest.skipUnless(os.name == "posix", "Requires real Linux flock")
    def test_standalone_manual_recovery_cannot_race_a_live_worker(self):
        import fcntl
        self.prepare()
        with (self.data / "application-worker.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(self.data / recovery.DIRECTORY / "recover.py"),
                                     "rollback", "--data-dir", str(self.data)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assert_blocked()
        self.assertEqual(self.commands, [])
