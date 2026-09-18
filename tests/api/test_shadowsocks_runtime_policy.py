"""Exercise the migration with fake systemctl and real process command lines."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[2] / "protocol-images/mihomo/modules/transport-shadowsocks"


@unittest.skipUnless(sys.platform == "linux", "Requires Linux /proc and Bash")
class RuntimePolicyTests(unittest.TestCase):
    def test_changed_guard_keeps_protected_sessions_and_migrates_only_unprotected(self):
        for protected in (True, False):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source"
                source.mkdir()
                guard = root / "installed/guard.py"
                script = (MODULE / "protect-runtime.sh").read_text()
                script = script.replace("/run/lock/vps-control-mihomo-ss-protection.lock", str(root / "migration.lock"))
                script = script.replace("/etc/systemd/system/vps-control-mihomo-ss@.service.d", str(root / "unit"))
                script = script.replace("/usr/local/lib/vps-control-mihomo-ss/guard.py", str(guard))
                (source / "protect-runtime.sh").write_text(script)
                for name in ("guard.py", "empty_connections.py"):
                    (source / name).write_text((MODULE / name).read_text())
                command = [sys.executable, "-c", "import time; time.sleep(30)"]
                if protected:
                    command.append(str(guard))
                child = subprocess.Popen(command)
                try:
                    fakebin = root / "bin"
                    fakebin.mkdir()
                    log = root / "commands"
                    fake = fakebin / "systemctl"
                    fake.write_text(f'''#!/bin/sh
printf '%s\\n' "$*" >>{shlex.quote(str(log))}
case "$1" in
 list-units) echo 'vps-control-mihomo-ss@test.service loaded active running';;
 show) echo {child.pid};;
esac
''')
                    fake.chmod(0o755)
                    env = {**os.environ, "PATH": str(fakebin) + os.pathsep + os.environ["PATH"]}
                    subprocess.run(["bash", str(source / "protect-runtime.sh"), "--restart-active"], env=env, check=True, capture_output=True, timeout=10)
                    calls = log.read_text()
                    self.assertIn("daemon-reload", calls)
                    self.assertEqual("try-restart" in calls, not protected)
                    self.assertIsNone(child.poll())
                    unit = (root / "unit/resource-guard.conf").read_text()
                    self.assertIn("RestartPreventExitStatus=78", unit)
                    self.assertIn("StartLimitIntervalSec=300", unit)
                    log.write_text("")
                    subprocess.run(["bash", str(source / "protect-runtime.sh"), "--restart-active"], env=env, check=True, capture_output=True, timeout=10)
                    self.assertNotIn("daemon-reload", log.read_text())
                finally:
                    child.terminate()
                    child.wait(timeout=5)
