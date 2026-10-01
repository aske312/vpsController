import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.api.support import manager


class RetainedRuntimeTests(unittest.TestCase):
    def test_snapshot_is_scoped_to_instances_and_excludes_retention_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "one.json").write_bytes(b"private configuration")
            (root / ".retained-services.json").write_text("{}")
            for prefix in ("vps-control-shadowsocks@", "vps-control-mihomo-ss@"):
                calls = []
                def run(*args, **kwargs):
                    calls.append(args)
                    if args[1] in {"list-units", "list-unit-files"}:
                        return SimpleNamespace(stdout="foreign@one.service loaded active\n")
                    self.assertEqual(args[2], prefix + "one.service")
                    return SimpleNamespace(stdout="ActiveState=inactive\nUnitFileState=masked-runtime\n")
                result = manager.ss_runtime.snapshot(run, root, prefix=prefix, directory=root)
                self.assertEqual(result, {prefix + "one.service": (False, "masked-runtime", b"private configuration")})
                self.assertEqual(len(calls), 3)
            with patch.object(manager.ss_runtime, "PREFIX", "test-prefix@"):
                self.assertEqual(list(manager.ss_runtime.snapshot(lambda *a, **kw: SimpleNamespace(stdout="ActiveState=inactive\nUnitFileState=disabled\n" if a[1] == "show" else ""), root, directory=root)), ["test-prefix@one.service"])

    def test_restore_keeps_an_intentionally_stopped_instance_stopped(self):
        unit = "vps-control-shadowsocks@one.service"
        commands = []
        def run(*args, **kwargs):
            commands.append(args)
            return SimpleNamespace(stdout="active" if args[1] == "is-active" else "ActiveState=inactive\nUnitFileState=enabled\n" if args[1] == "show" else "")
        before = {unit: (False, "enabled", b"same")}
        manager.ss_runtime.restore(run, before, {unit: (True, "enabled", b"same")})
        self.assertIn(("systemctl", "stop", unit), commands)
        self.assertFalse(any(command[1] in {"restart", "enable", "unmask"} for command in commands))
