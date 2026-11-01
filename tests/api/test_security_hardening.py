"""Failure injection for the real hardening shell function without host changes."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.api.support import ROOT

BASH = shutil.which('bash') if os.name != 'nt' else next((str(p) for p in (Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')) if p.exists()), None)


@unittest.skipUnless(BASH, 'Requires Bash')
class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.runtime').mkdir(exist_ok=True)

    def test_manual_recovery_preserves_marker_until_verified_and_rejects_foreign_paths(self):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        function = re.search(r'(?ms)^recover_security\(\) \{.*?^\}', source).group()
        for mode in ('success', 'failed', 'foreign', 'application-marker'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=ROOT / '.runtime') as directory:
                root = Path(directory)
                archive = root / 'recovery-20261102T000000Z.tar.gz.enc'
                Path(str(archive) + '.hardening.enc').write_bytes(b'fixture')
                marker = root / 'security-recovery.txt'
                value = root.resolve().as_posix()
                if os.name == 'nt':
                    value = '/' + value[0].lower() + value[2:]
                text = value + '/' + archive.name
                if mode == 'foreign':
                    text = value + '/../' + archive.name
                if mode == 'application-marker':
                    text = '{"owner":"application-update"}'
                marker.write_text(text)
                script = '''set -Eeuo pipefail
RECOVERY_DIR=$TEST_ROOT
SECURITY_RECOVERY_MARKER=$TEST_ROOT/security-recovery.txt
die() { exit 1; }
ok() { :; }
restore_recovery_point() { printf called > "$TEST_ROOT/called"; [[ "$TEST_MODE" != failed ]]; }
''' + function + '\nrecover_security\n'
                result = subprocess.run([BASH, '-s'], input=script, capture_output=True, text=True,
                    env={**os.environ, 'TEST_ROOT': value, 'TEST_MODE': mode}, timeout=10)
                self.assertEqual(result.returncode == 0, mode == 'success', result.stderr)
                self.assertEqual(marker.exists(), mode != 'success')
                self.assertEqual((root / 'called').exists(), mode in ('success', 'failed'))

    def invoke(self, failure='', recovery_failure=False, real_recovery=False, hardening=False):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        function = re.search(r'(?ms)^secure_server\(\) \{.*?^\}', source).group()
        # Redirect the two literal host paths; every host command is stubbed.
        function = function.replace('/etc/sysctl.d', '${TEST_ROOT}/sysctl.d').replace('/etc/ssh/sshd_config.d', '${TEST_ROOT}/ssh.d')
        with tempfile.TemporaryDirectory(dir=ROOT / '.runtime', prefix='security-hardening-') as directory:
            root = Path(directory)
            if real_recovery:
                (root / 'recovery').mkdir()
                (root / 'recovery/recovery-fixture.tar.gz.enc').write_bytes(b'fixture')
                (root / 'recovery/recovery.key').write_bytes(b'fixture')
                if hardening:
                    (root / 'recovery/recovery-fixture.tar.gz.enc.hardening.enc').write_bytes(b'fixture')
            value = root.resolve().as_posix()
            if os.name == 'nt':
                value = '/' + value[0].lower() + value[2:]
            script = r'''set -Eeuo pipefail
RECOVERY_DIR=$TEST_ROOT/recovery
INSTALL_DIR=$TEST_ROOT/app
SECURITY_RECOVERY_MARKER=$RECOVERY_DIR/security-recovery.txt
ENV_FILE=$TEST_ROOT/environment
COMMAND_PATH=$TEST_ROOT/control
APP_NAME=vps-control
event() { printf '%s\n' "$*" >> "$TEST_ROOT/events"; }
step() { event "$1"; [[ "$TEST_FAIL" != "$1" ]]; }
info() { :; }
ok() { event SUCCESS; }
warn() { :; }
die() { event FAILED; exit 1; }
install() {
  while (($#)); do
    case "$1" in -d) shift ;; -m) shift 2 ;; *) mkdir -p -- "$1"; shift ;; esac
  done
}
chmod() { :; }
chown() { :; }
security_preflight() { step PREFLIGHT; }
create_recovery_point() { step BACKUP; printf '%s\n' "$RECOVERY_DIR/fixture.enc"; }
python3() {
  case "${2:-}" in
    check) step CHECK_STATE ;;
    restore) step RESTORE_STATE ;;
    *) step PREPARE_STATE ;;
  esac
}
prepare_package_manager() { step PREPARE; }
apt-get() { if [[ "$*" == *update ]]; then step INDEX; else step PACKAGES; fi; }
configure_fail2ban() { step FAIL2BAN; }
configure_os_update_policy() { step UPDATE_POLICY; }
sysctl() { step SYSCTL; }
sshd() { step SSH_VALIDATE; }
systemctl() {
  case "$*" in
    'reload ssh.service') step SSH_RELOAD ;;
    'restart vps-control-api.service') step API_RESTART ;;
    'stop vps-control-api.service') step API_STOP ;;
    'daemon-reload') step DAEMON ;;
    'restart vps-control-api.service vps-control-web.service caddy.service') step RESTART ;;
    *) step SERVICES ;;
  esac
}
configure_access() { step ACCESS; }
configure_firewall() { step FIREWALL; }
install_api() { step INSTALL_API; }
ensure_api_write_access() { step API_ACCESS; }
restore_recovery_point() { event RECOVER; [[ "$TEST_RECOVERY_FAIL" != yes ]]; }
sha256sum() { step CHECKSUM; }
openssl() { step DECRYPT; }
tar() { step EXTRACT; }
restart_mihomo_manager_if_present() { step MANAGER; }
'''
            if real_recovery:
                function = re.search(r'(?ms)^restore_recovery_point\(\) \{.*?^\}', source).group()
                script += function + '\nif restore_recovery_point "$RECOVERY_DIR/recovery-fixture.tar.gz.enc"; then event RESTORED; else event FAILED; exit 1; fi\n'
            else:
                script += function + '\nsecure_server\n'
            result = subprocess.run([BASH, '-s'], input=script, capture_output=True, text=True, encoding='utf-8',
                env={**os.environ, 'TEST_ROOT': value, 'TEST_FAIL': failure,
                     'TEST_RECOVERY_FAIL': 'yes' if recovery_failure else 'no'}, timeout=10)
            events = (root / 'events').read_text().splitlines()
            return result, events, (root / 'recovery/security-recovery.txt').exists()

    def test_intermediate_failure_cannot_be_reported_as_success(self):
        for failure in ('PREPARE', 'INDEX', 'PACKAGES', 'FAIL2BAN', 'UPDATE_POLICY', 'SERVICES',
                        'SYSCTL', 'SSH_VALIDATE', 'SSH_RELOAD', 'ACCESS', 'FIREWALL', 'INSTALL_API', 'API_ACCESS', 'API_RESTART'):
            with self.subTest(failure=failure):
                result, events, marker = self.invoke(failure)
                self.assertNotEqual(result.returncode, 0, events)
                self.assertIn('RECOVER', events)
                self.assertNotIn('SUCCESS', events)
                self.assertEqual(events[events.index(failure) + 1:], ['RECOVER', 'FAILED'])
                self.assertFalse(marker)

    def test_missing_hardening_snapshot_prevents_all_mutations(self):
        result, events, marker = self.invoke('PREPARE_STATE')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('PREPARE', events)
        self.assertNotIn('FIREWALL', events)
        self.assertNotIn('RECOVER', events)
        self.assertFalse(marker)

    def test_failed_recovery_keeps_marker_for_manual_repair(self):
        result, events, marker = self.invoke('FAIL2BAN', recovery_failure=True)
        self.assertNotEqual(result.returncode, 0, events)
        self.assertTrue(marker)
        self.assertNotIn('SUCCESS', events)

    def test_success_removes_recovery_marker_only_after_all_steps(self):
        result, events, marker = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-2:], ['API_RESTART', 'SUCCESS'])
        self.assertNotIn('RECOVER', events)
        self.assertFalse(marker)

    def test_recovery_helper_stops_on_failure_even_when_called_from_conditional(self):
        for failure in ('CHECKSUM', 'API_STOP', 'DECRYPT', 'EXTRACT', 'DAEMON', 'RESTART', 'MANAGER'):
            with self.subTest(failure=failure):
                result, events, _ = self.invoke(failure, real_recovery=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-1], 'FAILED')
                self.assertNotIn('RESTORED', events)
                if failure in ('CHECKSUM', 'DECRYPT', 'EXTRACT'):
                    self.assertNotIn('DAEMON', events)
                if failure in ('CHECKSUM', 'API_STOP'):
                    self.assertNotIn('EXTRACT', events)
                if failure == 'CHECKSUM':
                    self.assertNotIn('API_STOP', events)
                if failure == 'RESTART':
                    self.assertNotIn('MANAGER', events)

    def test_hardening_state_is_checked_before_extract_and_restore_errors_are_not_success(self):
        for failure in ('CHECK_STATE', 'RESTORE_STATE', ''):
            with self.subTest(failure=failure):
                result, events, _ = self.invoke(failure, real_recovery=True, hardening=True)
                self.assertEqual(result.returncode == 0, not failure)
                if failure == 'CHECK_STATE':
                    self.assertNotIn('EXTRACT', events)
                if failure:
                    self.assertNotIn('RESTORED', events)
                else:
                    self.assertLess(events.index('CHECK_STATE'), events.index('EXTRACT'))
                    self.assertLess(events.index('API_STOP'), events.index('EXTRACT'))
                    self.assertLess(events.index('EXTRACT'), events.index('RESTORE_STATE'))
                    self.assertIn('RESTORED', events)
