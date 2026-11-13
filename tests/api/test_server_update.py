"""Exercise real shell update functions with isolated files and fake host commands."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import json
import tempfile
import unittest

from tests.api.support import ROOT

BASH = shutil.which("bash") if os.name != "nt" else next((str(path) for path in (Path("D:/Git/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")) if path.exists()), None)


def bash_path(path):
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


@unittest.skipUnless(BASH, "Requires Bash")
class ServerUpdateTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="server-update-", dir=runtime)))
        self.source = (ROOT / "scripts/vps-control.sh").read_text(encoding="utf-8")
        self.events = self.root / "events"
        self.cache = self.root / "security-updates.json"
        self.cache.write_text('{"available": 12}')

    def function(self, name):
        return re.search(r"(?ms)^" + name + r"\(\) \{.*?^\}", self.source).group()

    def invoke(self, action="safe-update", failure="", real_backup=False, automatic=True):
        (self.root / 'automation.json').write_text(json.dumps({'update': {'enabled': automatic}}), encoding='utf-8')
        functions = "\n".join(self.function(name) for name in (
            "safe_update_server", "automatic_update_enabled", "auto_safe_update_server", "update_kernel", "finish_operation"))
        if real_backup:
            functions += "\n" + self.function("create_recovery_point")
        script = r'''set -Eeuo pipefail
DATA_DIR=$TEST_ROOT
AUTOMATION_FILE=$TEST_ROOT/automation.json
python3() { "$TEST_PYTHON" "$@"; }
INSTALL_DIR=$TEST_ROOT/app
CONFIG_DIR=$TEST_ROOT/config
INSTALL_CONFIG=$TEST_ROOT/install.conf
RECOVERY_DIR=$TEST_ROOT/recovery
SAFE_UPDATE_REPORT=$RECOVERY_DIR/report.txt
CURRENT_ACTION=$TEST_ACTION
ACTION_PROGRESS=40
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"
event() { printf '%s\n' "$*" >> "$TEST_ROOT/events"; }
# Permissions are not supported by all Windows filesystems; host commands are mocked.
install() { mkdir -p -- "${@: -1}"; }
chmod() { :; }
info() { :; }
ok() { :; }
warn() { :; }
die() { event BACKUP_FAILED; exit 1; }
write_action_status() { event "STATUS:$1"; }
create_recovery_point() { event BACKUP; [[ "$TEST_FAIL" != backup ]] || return 17; printf '%s\n' "$RECOVERY_DIR/fixture.enc"; }
configure_os_update_policy() { event POLICY; [[ "$TEST_FAIL" != policy ]]; }
restore_recovery_point() { event RESTORE; [[ "$TEST_FAIL" != restore ]]; }
apt-get() {
  event "APT:$*"
  if [[ "$*" == *full-upgrade* ]]; then
    [[ "$TEST_FAIL" != upgrade ]] || return 18
  else
    [[ "$TEST_FAIL" != index ]] || return 19
  fi
}
dpkg-query() { printf 'package\t1.0\n'; }
integrity_check() { event INTEGRITY; [[ "$TEST_FAIL" != integrity && "$TEST_FAIL" != restore ]]; }
network_check() { event NETWORK; [[ "$TEST_FAIL" != network ]]; }
systemctl() { event "SERVICE:$*"; [[ "$TEST_FAIL" != reboot ]]; }
# Backup-stage stubs never read host paths or run real cryptography.
tar() { event TAR; [[ "$TEST_FAIL" != tar ]] || return 20; cat >/dev/null; }
openssl() {
  event "OPENSSL:$1"
  if [[ "$1" == rand ]]; then printf 'fixture-key'; return; fi
  [[ "$TEST_FAIL" != encryption ]] || return 21
  local output=""
  while (($#)); do if [[ "$1" == -out ]]; then output=$2; shift; fi; shift; done
  if [[ -n "$output" ]]; then printf fixture > "$output"; else printf fixture; fi
}
sha256sum() { event CHECKSUM; [[ "$TEST_FAIL" != checksum ]] || return 22; }
'''
        script += functions
        script += r'''
on_exit() { local result=$?; trap - EXIT; finish_operation "$result" || result=1; exit "$result"; }
trap on_exit EXIT
case "$TEST_ACTION" in
  safe-update) safe_update_server ;;
  auto-safe-update) auto_safe_update_server ;;
  kernel-update) update_kernel ;;
esac
'''
        result = subprocess.run([BASH, "-s"], env={**os.environ,
            "TEST_ROOT": bash_path(self.root), "TEST_FAIL": failure, "TEST_ACTION": action, "TEST_PYTHON": bash_path(Path(sys.executable))},
            input=script, capture_output=True, text=True, encoding="utf-8", timeout=30)
        events = self.events.read_text().splitlines() if self.events.exists() else []
        return result, events

    def test_all_entry_points_backup_upgrade_check_and_reboot_in_order(self):
        for action in ("safe-update", "auto-safe-update", "kernel-update"):
            with self.subTest(action=action):
                self.events.unlink(missing_ok=True)
                self.cache.write_text('{"available": 12}')
                result, events = self.invoke(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ["BACKUP", "POLICY",
                    "APT:-o DPkg::Lock::Timeout=300 -o APT::Update::Error-Mode=any update",
                    "APT:-o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade --no-remove -y",
                    "INTEGRITY", "NETWORK", "STATUS:rebooting", "SERVICE:--no-block --no-wall reboot"])
                self.assertFalse(self.cache.exists())

    def test_failures_never_reboot_and_invalidate_package_observation(self):
        for failure in ("backup", "policy", "index", "upgrade", "integrity", "network", "restore"):
            with self.subTest(failure=failure):
                self.events.unlink(missing_ok=True)
                self.cache.write_text('{"available": 12}')
                result, events = self.invoke(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("STATUS:rebooting", events)
                self.assertFalse(any(line.startswith("SERVICE:") for line in events))
                self.assertEqual(events[-1], "STATUS:failed")
                self.assertEqual("RESTORE" in events, failure in ("integrity", "network", "restore"))
                if failure in ("backup", "policy"):
                    self.assertFalse(any(line.startswith("APT:") for line in events))
                self.assertFalse(self.cache.exists())

    def test_backup_stage_errors_cannot_be_masked_by_command_substitution(self):
        for failure in ("tar", "encryption", "checksum"):
            with self.subTest(failure=failure):
                self.events.unlink(missing_ok=True)
                result, events = self.invoke(failure=failure, real_backup=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(line.startswith(("APT:", "SERVICE:")) for line in events))
                self.assertEqual(events[-1], "STATUS:failed")

    def test_rejected_reboot_is_failed_not_success(self):
        result, events = self.invoke(failure="reboot")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-3:], ["STATUS:rebooting", "SERVICE:--no-block --no-wall reboot", "STATUS:failed"])

    def test_disabled_auto_update_never_runs_packages_or_reboot_but_manual_update_still_works(self):
        result, events = self.invoke('auto-safe-update', automatic=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['STATUS:succeeded'])
        self.events.unlink()
        result, events = self.invoke('safe-update', automatic=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('STATUS:rebooting', events)

    def test_invalid_automatic_policy_cannot_be_treated_as_enabled(self):
        result, events = self.invoke('auto-safe-update', automatic='yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(row.startswith(('APT:', 'SERVICE:')) for row in events))
        self.assertEqual(events[-1], 'STATUS:failed')

    def test_setup_disables_distro_timer_and_preserves_the_UI_schedule(self):
        script = 'set -eu\nCOMMAND_PATH=/usr/local/sbin/vps-control\n'
        script += 'AUTOMATION_FILE=$TEST_ROOT/automation.json\npython3() { "$TEST_PYTHON" "$@"; }\n'
        script += 'install() { mkdir -p -- "${@: -1}"; }\nchmod() { :; }\n'
        script += 'systemctl() { printf "%s\\n" "$*" >> "$TEST_ROOT/events"; }\n'
        script += '\n'.join(self.function(name) for name in ('automation_calendar', 'install_automation_timer', 'apply_update_schedule', 'disable_native_update_timer', 'configure_os_update_policy'))
        script += '\nconfigure_os_update_policy "$TEST_ROOT/units"\n'
        for enabled in (False, True, False):
            with self.subTest(enabled=enabled):
                (self.root / 'automation.json').write_text(json.dumps({'update': {'enabled': enabled, 'cadence': 'daily', 'hour': 17, 'minute': 13}}), encoding='utf-8')
                self.events.unlink(missing_ok=True)
                result = subprocess.run([BASH, "-c", script], env={**os.environ, "TEST_ROOT": bash_path(self.root), "TEST_PYTHON": bash_path(Path(sys.executable))},
                                        capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                events = self.events.read_text().splitlines()
                self.assertIn('disable --now apt-daily-upgrade.timer', events)
                self.assertNotIn('enable --now apt-daily-upgrade.timer', events)
                self.assertEqual('enable --now vps-control-auto-update.timer' in events, enabled)
                timer = (self.root / 'units/vps-control-auto-update.timer').read_text()
                self.assertIn('OnCalendar=*-*-* 17:13:00', timer)
        policy = (self.root / "units/apt-daily-upgrade.service.d/vps-control.conf").read_text()
        self.assertIn("ExecStart=\nExecStart=/usr/local/sbin/vps-control auto-safe-update\n", policy)
        self.assertIn("TimeoutStartSec=60min", policy)
