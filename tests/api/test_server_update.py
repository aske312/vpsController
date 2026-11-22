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
        self.driver = self.root / 'guard.py'
        self.driver.write_text('''import os, sys
sys.path.insert(0, os.environ['TEST_PROJECT_ROOT'])
from tests.api.support import api
from automation_schedule import admit
from datetime import datetime
from pathlib import Path
kind = sys.argv[2]
origin = os.environ.get('TEST_ORIGIN') or ('vps-control-auto-update.service' if kind == 'update' else 'vps-control-action-fixture.service')
print(admit(Path(sys.argv[1]), kind, origin=origin,
    now=datetime.fromisoformat(os.environ['TEST_NOW']), manual='--manual' in sys.argv))
''', encoding='utf-8')

    def function(self, name):
        return re.search(r"(?ms)^" + name + r"\(\) \{.*?^\}", self.source).group()

    def invoke(self, action="safe-update", failure="", real_backup=False, automatic=True,
               origin='', now='2026-11-22T02:31:00+02:00', settings=None):
        policy = self.root / 'automation.json'
        if automatic == 'missing':
            policy.unlink(missing_ok=True)
        else:
            policy.write_text('{' if automatic == 'malformed' else json.dumps(settings or {'update': {'enabled': automatic}}), encoding='utf-8')
        functions = "\n".join(self.function(name) for name in (
            "safe_update_server", "scheduled_action_check", "auto_safe_update_server", "update_kernel", "reboot_server", "finish_operation"))
        if real_backup:
            functions += "\n" + self.function("create_recovery_point")
        script = r'''set -Eeuo pipefail
DATA_DIR=$TEST_ROOT
AUTOMATION_FILE=$TEST_ROOT/automation.json
python3() {
  if [[ "$1" == "$INSTALL_DIR/api/automation_schedule.py" && "$2" == check ]]; then
    shift 2
    "$TEST_PYTHON" "$TEST_DRIVER" "$@"
  else
    "$TEST_PYTHON" "$@"
  fi
}
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
write_action_status() {
  event "STATUS:$1"
  [[ "$TEST_FAIL:$1" != reboot-marker:rebooting ]]
}
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
systemctl() {
  event "SERVICE:$*"
  if [[ "$TEST_FAIL" == reboot-term ]]; then kill -TERM "$$"; fi
  [[ "$TEST_FAIL" != reboot ]]
}
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
  reboot) reboot_server ;;
esac
'''
        result = subprocess.run([BASH, "-s"], env={**os.environ,
            "TEST_ROOT": bash_path(self.root), "TEST_FAIL": failure, "TEST_ACTION": action, "TEST_PYTHON": bash_path(Path(sys.executable)),
            'TEST_PROJECT_ROOT': str(ROOT), 'TEST_DRIVER': bash_path(self.driver), 'TEST_ORIGIN': origin, 'TEST_NOW': now},
            input=script, capture_output=True, text=True, encoding="utf-8", timeout=30)
        events = self.events.read_text().splitlines() if self.events.exists() else []
        return result, events

    def test_all_update_entry_points_backup_upgrade_check_without_reboot(self):
        for action in ("safe-update", "auto-safe-update", "kernel-update"):
            with self.subTest(action=action):
                self.events.unlink(missing_ok=True)
                self.cache.write_text('{"available": 12}')
                result, events = self.invoke(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ["BACKUP", "POLICY",
                    "APT:-o DPkg::Lock::Timeout=300 -o APT::Update::Error-Mode=any update",
                    "APT:-o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade --no-remove -y",
                    "INTEGRITY", "NETWORK", "STATUS:succeeded"])
                report = (self.root / 'recovery/report.txt').read_text(encoding='utf-8')
                self.assertIn('Reboot: manual only; not requested by this update', report)
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

    def test_reboot_is_only_requested_by_the_separate_explicit_action(self):
        result, events = self.invoke(action="reboot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["STATUS:rebooting", "SERVICE:--no-block --no-wall reboot", "STATUS:rebooting"])

    def test_shutdown_stopping_the_reboot_worker_keeps_the_pending_marker(self):
        result, events = self.invoke(action="reboot", failure="reboot-term")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ["STATUS:rebooting", "SERVICE:--no-block --no-wall reboot", "STATUS:rebooting"])

    def test_rejected_reboot_records_failure_instead_of_waiting_for_a_new_boot(self):
        result, events = self.invoke(action="reboot", failure="reboot")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["STATUS:rebooting", "SERVICE:--no-block --no-wall reboot", "STATUS:failed"])

    def test_reboot_is_not_requested_if_its_pending_marker_cannot_be_saved(self):
        result, events = self.invoke(action="reboot", failure="reboot-marker")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, ["STATUS:rebooting", "STATUS:failed"])

    def test_disabled_auto_update_never_runs_packages_or_reboot_but_manual_update_still_works(self):
        for automatic in (False, 'missing'):
            with self.subTest(automatic=automatic):
                self.events.unlink(missing_ok=True)
                result, events = self.invoke('auto-safe-update', automatic=automatic)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['STATUS:succeeded'])
        self.events.unlink()
        result, events = self.invoke('safe-update', automatic=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1], 'STATUS:succeeded')
        self.assertFalse(any(row.startswith('SERVICE:') for row in events))

    def test_invalid_automatic_policy_cannot_be_treated_as_enabled(self):
        for automatic in ('yes', None, 'malformed'):
            with self.subTest(automatic=automatic):
                self.events.unlink(missing_ok=True)
                result, events = self.invoke('auto-safe-update', automatic=automatic)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(row.startswith(('APT:', 'SERVICE:')) for row in events))
                self.assertEqual(events[-1], 'STATUS:failed')

    def test_old_unit_commands_cannot_bypass_saved_day_off_or_origin(self):
        cases = [
            ('reboot', 'cron.service', None),
            ('auto-safe-update', 'apt-daily-upgrade.service', None),
            ('auto-safe-update', 'vps-control-auto-update.service', {'update': {'enabled': True, 'weekday': 'Wed'}}),
            ('reboot', 'vps-control-auto-reboot.service', {'reboot': {'enabled': False}}),
            ('reboot', 'vps-control-auto-reboot.service', {'reboot': {'enabled': True, 'weekday': 'Wed'}}),
        ]
        for action, origin, settings in cases:
            with self.subTest(action=action, origin=origin, settings=settings):
                self.events.unlink(missing_ok=True)
                result, events = self.invoke(action, origin=origin, settings=settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['STATUS:succeeded'])

    def test_service_mode_restore_ignores_saved_maintenance_and_distro_timers(self):
        script = 'set -eu\n'
        script += 'systemctl() { printf "%s\\n" "$*"; }\n'
        script += 'apply_automation() { printf "CURRENT_SETTINGS\\n"; }\n'
        script += self.function('restore_maintenance_timers')
        script += '\nrestore_maintenance_timers vpn-monitor.timer vps-control-auto-reboot.timer vps-control-auto-update.timer apt-daily-upgrade.timer apt-daily.timer\n'
        result = subprocess.run([BASH, '-c', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['start vpn-monitor.timer', 'start apt-daily.timer', 'CURRENT_SETTINGS'])

    def test_enabled_scheduled_reboot_runs_once_at_the_selected_day_and_time(self):
        settings = {'reboot': {'enabled': True, 'weekday': 'Sat', 'hour': 4, 'minute': 12}}
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                self.events.unlink(missing_ok=True)
                result, events = self.invoke('reboot', origin='vps-control-auto-reboot.service',
                                             now='2026-11-21T04:13:00+02:00', settings=settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['STATUS:succeeded'] if attempt else
                                 ['STATUS:rebooting', 'SERVICE:--no-block --no-wall reboot', 'STATUS:rebooting'])

    def test_rollback_to_old_payload_preserves_disabled_schedules_and_custom_days(self):
        settings = {'update': {'enabled': False, 'weekday': 'Thu', 'hour': 5, 'minute': 17},
                    'reboot': {'enabled': False, 'weekday': 'Sat', 'hour': 6, 'minute': 23}}
        (self.root / 'automation.json').write_text(json.dumps(settings), encoding='utf-8')
        script = '''set -eu
AUTOMATION_RENDERER_SOURCE="$(cat "$TEST_INSTALL_DIR/api/automation_schedule.py")"
INSTALL_DIR=$TEST_ROOT/old-release
AUTOMATION_FILE=$TEST_ROOT/automation.json
COMMAND_PATH=/usr/local/sbin/vps-control
python3() { "$TEST_PYTHON" "$@"; }
info() { :; }
ok() { :; }
install() { mkdir -p -- "${@: -1}"; }
chmod() { :; }
systemctl() { printf '%s\\n' "$*" >> "$TEST_ROOT/events"; }
'''
        script += '\n'.join(self.function(name) for name in (
            'automation_calendar', 'render_automation_settings', 'install_automation_timer',
            'apply_automation', 'disable_native_update_timer'))
        script += '\napply_automation "$AUTOMATION_FILE" "$TEST_ROOT/units"\n'
        result = subprocess.run([BASH, '-c', script], env={**os.environ,
            'TEST_ROOT': bash_path(self.root), 'TEST_INSTALL_DIR': bash_path(ROOT),
            'TEST_PYTHON': bash_path(Path(sys.executable))}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events.read_text().splitlines()
        self.assertFalse(any(event.startswith('enable --now') for event in events))
        self.assertIn('disable --now apt-daily-upgrade.timer', events)
        for kind, calendar in [('update', 'Thu *-*-* 05:17:00'), ('reboot', 'Sat *-*-* 06:23:00')]:
            self.assertIn('OnCalendar=' + calendar, (self.root / f'units/vps-control-auto-{kind}.timer').read_text())
        self.assertEqual(json.loads((self.root / 'automation.json').read_text()), settings)

    def test_setup_disables_distro_timer_and_preserves_the_UI_schedule(self):
        script = 'set -eu\nCOMMAND_PATH=/usr/local/sbin/vps-control\n'
        script += 'AUTOMATION_FILE=$TEST_ROOT/automation.json\nINSTALL_DIR=$TEST_INSTALL_DIR\npython3() { "$TEST_PYTHON" "$@"; }\n'
        script += 'info() { :; }\nok() { :; }\n'
        script += 'install() { mkdir -p -- "${@: -1}"; }\nchmod() { :; }\n'
        script += 'systemctl() { printf "%s\\n" "$*" >> "$TEST_ROOT/events"; }\n'
        script += '\n'.join(self.function(name) for name in ('automation_calendar', 'render_automation_settings', 'install_automation_timer', 'apply_update_schedule', 'apply_automation', 'disable_native_update_timer', 'configure_os_update_policy'))
        script += '\nconfigure_os_update_policy "$TEST_ROOT/units"\napply_automation "$AUTOMATION_FILE" "$TEST_ROOT/units"\n'
        for enabled in (False, True, False):
            with self.subTest(enabled=enabled):
                settings = {'update': {'enabled': enabled, 'cadence': 'daily', 'hour': 17, 'minute': 13},
                            'reboot': {'enabled': enabled, 'cadence': 'weekly', 'weekday': 'Wed', 'hour': 5, 'minute': 21}}
                (self.root / 'automation.json').write_text(json.dumps(settings), encoding='utf-8')
                self.events.unlink(missing_ok=True)
                result = subprocess.run([BASH, "-c", script], env={**os.environ, "TEST_ROOT": bash_path(self.root), "TEST_PYTHON": bash_path(Path(sys.executable)), 'TEST_INSTALL_DIR': bash_path(ROOT)},
                                        capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                events = self.events.read_text().splitlines()
                self.assertIn('disable --now apt-daily-upgrade.timer', events)
                self.assertNotIn('enable --now apt-daily-upgrade.timer', events)
                self.assertEqual('enable --now vps-control-auto-update.timer' in events, enabled)
                timer = (self.root / 'units/vps-control-auto-update.timer').read_text()
                self.assertIn('OnCalendar=*-*-* 17:13:00', timer)
                self.assertIn('Persistent=false', timer)
                reboot = (self.root / 'units/vps-control-auto-reboot.timer').read_text()
                self.assertIn('OnCalendar=Wed *-*-* 05:21:00', reboot)
                self.assertIn('Persistent=false', reboot)
                self.assertEqual(json.loads((self.root / 'automation.json').read_text()), settings)
        policy = (self.root / "units/apt-daily-upgrade.service.d/vps-control.conf").read_text()
        self.assertIn("ExecStart=\nExecStart=/usr/local/sbin/vps-control auto-safe-update\n", policy)
        self.assertIn("TimeoutStartSec=60min", policy)
