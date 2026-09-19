import json
import tempfile
import unittest
from pathlib import Path

from tests.api.support import api  # Linux compatibility shim on Windows.
from application_operation import atomic_json
from resource_cleanup import cleanup, register


class ResourceCleanupTests(unittest.TestCase):
    def test_only_registered_artifacts_of_finished_operations_are_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, state in enumerate(("succeeded", "failed", "running", "unknown"), 1):
                path = root / "tmp" / f"update.{index}"
                path.mkdir(parents=True)
                (path / "archive").write_text("data")
                register(root, path, f"{index:032x}")
                atomic_json(root / "operations" / f"{index:032x}.json", {"state": state})
            foreign = root / "tmp" / "update.foreign"
            foreign.mkdir()
            retained = root / "mihomo" / "profiles.json"
            retained.parent.mkdir()
            retained.write_text("private profiles")
            result = cleanup(root)
            self.assertEqual(result, {"removed": 2, "preserved": 3})
            self.assertTrue(foreign.exists())
            self.assertEqual(retained.read_text(), "private profiles")
            self.assertTrue((root / "tmp" / "update.3" / "archive").exists())

    def test_missing_operation_and_path_escape_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "tmp" / "update.orphan"
            target.mkdir(parents=True)
            register(root, target, "1" * 32)
            self.assertEqual(cleanup(root), {"removed": 0, "preserved": 1})
            with self.assertRaises(ValueError):
                register(root, root / "update.outside", "1" * 32)
