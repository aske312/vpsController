"""Opt-in lifecycle checks using disposable systemd workers and private data."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from tests.api.support import ROOT
import application_operation as operations
from service_state import observe_service


@unittest.skipUnless(sys.platform == "linux" and os.getenv("PRIVACY_SYSTEMD_TESTS") == "1", "Requires opt-in Linux/systemd environment")
class ApplicationOperationSystemdTests(unittest.TestCase):
    def test_real_worker_lock_completion_replay_and_interruption(self):
        if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
            self.skipTest("Requires root and systemd")
        with tempfile.TemporaryDirectory(prefix="core-operation-test-") as folder:
            root = Path(folder)
            current = root / "application-action.json"
            worker = root / "worker.py"
            worker.write_text(f"import sys\nsys.path.insert(0, {str(ROOT / 'api')!r})\n" + '''
import os, time
from pathlib import Path
import application_operation as operations
root = Path(sys.argv[1])
with operations.mutation_lock(root):
    identity = os.environ['VPS_CONTROL_OPERATION_ID']
    saved = operations.read(root / 'operations' / (identity + '.json'))
    def status(state):
        operations.write_status(root, root / 'application-action.json', saved['action'], state,
                                100 if state == 'succeeded' else 10, 'Fixture', saved['started_at'], identity, saved['unit'])
    status('running')
    (root / 'ready').touch()
    while not (root / 'finish').exists():
        if (root / 'commit').exists():
            status('succeeded')
        time.sleep(.05)
    if sys.argv[2] == 'crash':
        os._exit(3)
    status('succeeded')
''', encoding="utf-8")

            def wait_for(predicate):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(.1)
                self.fail("Disposable worker did not reach the expected state")

            units = []
            try:
                for mode in ("success", "crash"):
                    with self.subTest(mode=mode):
                        for marker in ("ready", "commit", "finish"):
                            (root / marker).unlink(missing_ok=True)
                        identity = uuid.uuid4().hex
                        command = [sys.executable, str(worker), str(root), mode]
                        properties = ("--property=RuntimeMaxSec=30", "--property=ProtectSystem=strict",
                                      f"--property=ReadWritePaths={root}", "--setenv=PYTHONDONTWRITEBYTECODE=1")
                        started = operations.start(root, current, "fixture", command, subprocess.run,
                                                   request_id=identity, properties=properties, observe=observe_service)
                        units.append(started["unit"])
                        wait_for(lambda: (root / "ready").exists())
                        launch = Mock(side_effect=AssertionError("A second worker must not launch"))
                        with self.assertRaises(operations.OperationConflict):
                            operations.start(root, current, "other", command, launch)
                        with self.assertRaises(operations.OperationConflict):
                            with operations.mutation_lock(root):
                                self.fail("Cross-process worker lock was not held")
                        if mode == "success":
                            (root / "commit").touch()
                            wait_for(lambda: operations.read(current).get("state") == "succeeded")
                            with operations.locked(root):
                                self.assertEqual(operations.reconcile(root, current, operations.read(current), observe_service)["state"], "running")
                        (root / "finish").touch()
                        wait_for(lambda: observe_service(started["unit"])["state"] in {"inactive", "failed"})
                        with operations.locked(root):
                            saved = operations.read(current)
                            saved["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
                            final = operations.reconcile(root, current, saved, observe_service)
                        self.assertEqual(final["state"], "succeeded" if mode == "success" else "failed")
                        replay = operations.start(root, current, "fixture", command, launch,
                                                  request_id=identity, properties=properties, observe=observe_service)
                        self.assertEqual(replay["state"], final["state"])
                        launch.assert_not_called()
            finally:
                for unit in units:
                    subprocess.run(["systemctl", "stop", unit], capture_output=True, timeout=15, check=False)
                    subprocess.run(["systemctl", "reset-failed", unit], capture_output=True, timeout=15, check=False)
