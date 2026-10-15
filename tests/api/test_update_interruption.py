import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.api.support import ROOT

BASH = shutil.which("bash") if os.name != "nt" else next((str(path) for path in (Path("D:/Git/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")) if path.exists()), None)


def bash_path(path):
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


@unittest.skipUnless(BASH, "Requires Bash")
class UpdateInterruptionTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="update-interruption-", dir=runtime)))
        self.assertTrue(self.root.resolve().is_relative_to(runtime.resolve()))
        self.installed = self.root / "app"
        self.previous = self.root / "app.rollback.fixture"
        self.data = self.root / "data"
        self.failed = self.data / "tmp" / "interrupted-update.fixture"
        for path in (self.installed, self.previous, self.failed):
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
        self.installed.mkdir()
        self.previous.mkdir()
        (self.installed / "release").write_text("candidate")
        (self.previous / "release").write_text("previous")
        (self.installed / "venv").mkdir()
        (self.installed / "venv" / "runtime").write_text("retained runtime")

    def invoke(self, failure="", active="yes", previous=None):
        source = (ROOT / "scripts/vps-control.sh").read_text(encoding="utf-8")
        function = re.search(r"(?ms)^rollback_interrupted_update\(\) \{.*?^\}", source).group()
        script = f'''set -eu
INSTALL_DIR={shlex.quote(bash_path(self.installed))}
UPDATE_ROLLBACK_DIR={shlex.quote(bash_path(previous or self.previous))}
DATA_DIR={shlex.quote(bash_path(self.data))}
TEST_ROOT={shlex.quote(bash_path(self.root))}
TEST_FAIL={shlex.quote(failure)}
TEST_WINDOWS={"yes" if os.name == "nt" else "no"}
UPDATE_SWAP_ACTIVE={shlex.quote(active)}
APP_NAME=vps-control
warn() {{ printf 'WARN:%s\\n' "$*"; }}
date() {{ printf fixture; }}
systemctl() {{
  printf 'SERVICE:%s\\n' "$*"
  [[ "$TEST_FAIL" != "service-$1" ]]
}}
restore_update_gateway() {{ printf 'GATEWAY\\n'; [[ "$TEST_FAIL" != gateway ]]; }}
restart_mihomo_manager_if_present() {{ printf 'MANAGER\\n'; [[ "$TEST_FAIL" != manager ]]; }}
mv() {{
  local origin="$2" destination="$3"
  printf 'MOVE:%s -> %s\\n' "$origin" "$destination"
  [[ "$origin" == "$TEST_ROOT/"* && "$destination" == "$TEST_ROOT/"* ]] || return 91
  case "$TEST_FAIL:$origin" in
    runtime:*/venv|candidate:*/app|previous:*.rollback.fixture) return 17 ;;
  esac
  command mv "$@"
}}
install() {{
  [[ "$TEST_FAIL" != staging ]] || return 17
  # Git Bash cannot apply POSIX directory modes to this Windows workspace.
  if [[ "$TEST_WINDOWS" == yes ]]; then command mkdir -p -- "${{@: -1}}"; else command install "$@"; fi
}}
rm() {{
  printf 'REMOVE:%s\\n' "$*"
  [[ "$3" == "$TEST_ROOT/data/tmp/interrupted-update.fixture" ]] || return 92
  [[ "$TEST_FAIL" != cleanup ]] || return 17
  command rm "$@"
}}
{function}
# Production calls the function on the left of ||; errexit is suppressed there.
result=0
rollback_interrupted_update || result=$?
printf 'RESULT:%s\\nACTIVE:%s\\nBACKUP:%s\\n' "$result" "$UPDATE_SWAP_ACTIVE" "$UPDATE_ROLLBACK_DIR"
'''
        result = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout + result.stderr

    def test_success_preserves_runtime_and_clears_candidate_only_after_ready(self):
        output = self.invoke()
        self.assertIn("RESULT:0", output)
        self.assertIn("ACTIVE:no", output)
        self.assertEqual((self.installed / "release").read_text(), "previous")
        self.assertEqual((self.installed / "venv" / "runtime").read_text(), "retained runtime")
        self.assertFalse(self.failed.exists())
        self.assertFalse(self.previous.exists())
        self.assertLess(output.index("SERVICE:is-active"), output.index("REMOVE:"))

    def test_runtime_transfer_failure_never_moves_or_cleans_releases(self):
        output = self.invoke("runtime")
        self.assertIn("RESULT:1", output)
        self.assertIn("ACTIVE:yes", output)
        self.assertEqual((self.installed / "release").read_text(), "candidate")
        self.assertEqual((self.previous / "release").read_text(), "previous")
        self.assertNotIn("GATEWAY", output)
        self.assertNotIn("REMOVE:", output)

    def test_candidate_move_failure_cannot_nest_previous_release_or_claim_readiness(self):
        output = self.invoke("candidate")
        self.assertIn("RESULT:1", output)
        self.assertEqual((self.installed / "release").read_text(), "candidate")
        self.assertEqual((self.previous / "release").read_text(), "previous")
        self.assertFalse((self.installed / self.previous.name).exists())
        self.assertNotIn("GATEWAY", output)
        self.assertNotIn("REMOVE:", output)

    def test_previous_move_failure_retains_both_releases_for_recovery(self):
        output = self.invoke("previous")
        self.assertIn("RESULT:1", output)
        self.assertFalse(self.installed.exists())
        self.assertEqual((self.failed / "release").read_text(), "candidate")
        self.assertEqual((self.previous / "release").read_text(), "previous")
        self.assertNotIn("SERVICE:restart", output)
        self.assertNotIn("REMOVE:", output)

    def test_stop_failure_does_not_touch_application_files(self):
        output = self.invoke("service-stop")
        self.assertIn("RESULT:1", output)
        self.assertEqual((self.installed / "release").read_text(), "candidate")
        self.assertTrue((self.installed / "venv").exists())
        self.assertNotIn("MOVE:", output)

    def test_staging_failure_does_not_stop_services_or_move_runtime(self):
        output = self.invoke("staging")
        self.assertIn("RESULT:1", output)
        self.assertNotIn("SERVICE:", output)
        self.assertNotIn("MOVE:", output)
        self.assertTrue((self.installed / "venv").exists())

    def test_failed_gateway_runtime_or_cleanup_never_clears_pending_state(self):
        for failure in ("gateway", "service-restart", "service-reload-or-restart", "manager", "service-is-active", "cleanup"):
            with self.subTest(failure=failure):
                # Each invocation gets fresh isolated candidate/previous directories.
                if not self.previous.exists():
                    if self.installed.exists():
                        self.installed.rename(self.previous)
                    self.installed.mkdir()
                    (self.installed / "release").write_text("candidate")
                if self.failed.exists():
                    self.failed.rename(self.root / ("retained-" + failure))
                output = self.invoke(failure)
                self.assertIn("RESULT:1", output)
                self.assertIn("ACTIVE:yes", output)
                self.assertEqual((self.installed / "release").read_text(), "previous")
                self.assertTrue(self.failed.exists())

    def test_preflight_collision_missing_or_unexpected_backup_has_no_service_effect(self):
        self.failed.mkdir(parents=True)
        for previous in (self.previous, self.root / "outside", self.root / "app.rollback.missing"):
            with self.subTest(previous=previous.name):
                output = self.invoke(previous=previous)
                self.assertIn("RESULT:1", output)
                self.assertNotIn("SERVICE:", output)
                self.assertNotIn("MOVE:", output)
                self.assertEqual((self.installed / "release").read_text(), "candidate")

    def test_inactive_swap_is_an_idempotent_noop(self):
        output = self.invoke(active="no")
        self.assertIn("RESULT:0", output)
        self.assertNotIn("SERVICE:", output)
        self.assertNotIn("MOVE:", output)
        self.assertEqual((self.installed / "release").read_text(), "candidate")

    def test_redirected_staging_directory_is_rejected_before_service_or_file_effects(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.data.mkdir()
        try:
            (self.data / "tmp").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Native symlinks unavailable in this Windows session")
        output = self.invoke()
        self.assertIn("RESULT:1", output)
        self.assertNotIn("SERVICE:", output)
        self.assertNotIn("MOVE:", output)
        self.assertFalse((outside / "interrupted-update.fixture").exists())
