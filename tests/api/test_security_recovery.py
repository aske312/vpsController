"""Hardening recovery preserves config presence and observed runtime policy."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from tests.api.support import ROOT, load_module

recovery = load_module('security_recovery', 'api/security_recovery.py')
OPENSSL = shutil.which('openssl') or next((str(p) for p in (Path('D:/Git/usr/bin/openssl.exe'),) if p.exists()), None)


class SecurityRecoveryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.runtime').mkdir(exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=ROOT / '.runtime', prefix='hardening-state-')))
        self.units = {unit: {'present': True, 'active': True, 'enabled': 'enabled'} for unit in recovery.UNITS}
        self.units['auditd.service'] = {'present': True, 'active': False, 'enabled': 'disabled'}
        self.units['apparmor.service'] = {'present': True, 'active': False, 'enabled': 'masked'}
        self.sysctls = dict.fromkeys(recovery.SYSCTLS, '0')
        self.firewall_active = False
        self.calls = []
        self.fail = None
        self.file = self.root / recovery.FILES[0].lstrip('/')
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'original policy\n')
        self.file.chmod(0o640)

    def fake_run(self, args, **options):
        self.calls.append(args)
        if self.fail == args:
            return SimpleNamespace(returncode=1, stdout='', stderr='injected failure')
        output = ''
        if args[0] == 'systemctl':
            action = args[1]
            unit = args[2] if action == 'show' else args[-1]
            if action == 'show':
                state = self.units[unit]
                output = f"LoadState={'loaded' if state['present'] else 'not-found'}\nActiveState={'active' if state['active'] else 'inactive'}\nUnitFileState={state['enabled'] or ''}\n"
            elif action in ('start', 'restart', 'stop'):
                self.units[unit]['active'] = action != 'stop'
            elif action in ('enable', 'disable', 'mask', 'unmask'):
                self.units[unit]['enabled'] = {'enable': 'enabled', 'disable': 'disabled', 'mask': 'masked', 'unmask': 'disabled'}[action]
                if '--runtime' in args and action in ('enable', 'mask'):
                    self.units[unit]['enabled'] += '-runtime'
        elif args[:2] == ['sysctl', '-n']:
            output = self.sysctls[args[-1]]
        elif args[:2] == ['sysctl', '-w']:
            name, value = args[-1].split('=', 1)
            self.sysctls[name] = value
        elif args[0] == 'ufw':
            if args[1] == 'status':
                output = 'Status: active' if self.firewall_active else 'Status: inactive'
            elif args[-1] in ('enable', 'disable'):
                self.firewall_active = args[-1] == 'enable'
        elif args[0] == 'openssl':
            return subprocess.run([OPENSSL, *args[1:]], **options)
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    def mutate(self):
        self.file.write_bytes(b'changed policy')
        added = self.root / recovery.FILES[1].lstrip('/')
        added.parent.mkdir(parents=True)
        added.write_text('new SSH policy')
        for state in self.units.values():
            state.update(active=True, enabled='enabled')
        self.sysctls = dict.fromkeys(recovery.SYSCTLS, '1')
        self.firewall_active = True
        return added

    def test_restore_returns_original_files_absence_sysctls_and_service_policy(self):
        state = recovery.snapshot(root=self.root, run=self.fake_run)
        original_units = copy.deepcopy(self.units)
        original_sysctls = self.sysctls.copy()
        added = self.mutate()
        recovery.restore(state, root=self.root, run=self.fake_run)
        self.assertEqual(self.file.read_bytes(), b'original policy\n')
        self.assertFalse(added.exists())
        self.assertEqual(self.units, original_units)
        self.assertEqual(self.sysctls, original_sysctls)
        self.assertFalse(self.firewall_active)
        if os.name != 'nt':
            self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)

    def test_update_timer_files_and_disabled_state_are_restored_and_old_snapshots_still_validate(self):
        self.units['vps-control-auto-update.timer'].update(active=False, enabled='disabled')
        state = recovery.snapshot(root=self.root, run=self.fake_run)
        path = self.root / 'etc/systemd/system/vps-control-auto-update.timer'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('new timer', encoding='utf-8')
        self.units['vps-control-auto-update.timer'].update(active=True, enabled='enabled')
        recovery.restore(state, root=self.root, run=self.fake_run)
        self.assertFalse(path.exists())
        self.assertFalse(self.units['vps-control-auto-update.timer']['active'])
        self.assertEqual(self.units['vps-control-auto-update.timer']['enabled'], 'disabled')
        legacy = copy.deepcopy(state)
        legacy['files'] = {key: value for key, value in legacy['files'].items() if key in recovery.LEGACY_FILES}
        legacy['services'] = {key: value for key, value in legacy['services'].items() if key in recovery.LEGACY_UNITS}
        recovery.validate(legacy)

    def test_entire_snapshot_is_validated_before_any_restore_effect(self):
        state = recovery.snapshot(root=self.root, run=self.fake_run)
        state['files']['/unowned/path'] = {'kind': 'absent'}
        self.calls.clear()
        before = self.file.read_bytes()
        with self.assertRaises(ValueError):
            recovery.restore(state, root=self.root, run=self.fake_run)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_failed_restart_does_not_hide_failure_or_skip_independent_recovery(self):
        state = recovery.snapshot(root=self.root, run=self.fake_run)
        self.mutate()
        self.fail = ['systemctl', 'restart', 'fail2ban.service']
        with self.assertRaisesRegex(RuntimeError, 'не подтверждено'):
            recovery.restore(state, root=self.root, run=self.fake_run)
        self.assertEqual(self.file.read_bytes(), b'original policy\n')
        self.assertIn(['systemctl', 'restart', 'vps-control-web.service'], self.calls)
        self.assertFalse(self.firewall_active)

    def test_unknown_original_service_prevents_snapshot_before_mutation(self):
        self.fail = ['systemctl', 'show', 'auditd.service', '--property=LoadState,ActiveState,UnitFileState']
        with self.assertRaises(ValueError):
            recovery.snapshot(root=self.root, run=self.fake_run)
        self.assertFalse(any(args[:2] == ['sysctl', '-w'] for args in self.calls))

    @unittest.skipUnless(OPENSSL, 'Requires OpenSSL')
    def test_failed_encryption_preserves_original_archive_manifest(self):
        archive = self.root / 'recovery-20261102T000000Z.tar.gz.enc'
        archive.write_bytes(b'archive fixture')
        manifest = Path(str(archive) + '.sha256')
        manifest.write_text('original verified manifest')
        (self.root / 'recovery.key').write_text('fixture')

        def failing_run(args, **options):
            if args[0] == 'openssl':
                return SimpleNamespace(returncode=1, stdout='', stderr='failure')
            return self.fake_run(args, **options)

        with self.assertRaises(RuntimeError):
            recovery.prepare(archive, root=self.root, run=failing_run)
        self.assertEqual(manifest.read_text(), 'original verified manifest')
        self.assertFalse(Path(str(archive) + '.hardening.enc').exists())
        self.assertEqual(list(self.root.glob('.hardening-*')), [])

    def test_unchanged_auditd_does_not_require_forbidden_manual_restart(self):
        self.units['auditd.service'].update(active=True, enabled='enabled')
        state = recovery.snapshot(root=self.root, run=self.fake_run)
        self.fail = ['systemctl', 'restart', 'auditd.service']
        recovery.restore(state, root=self.root, run=self.fake_run)
        self.assertNotIn(self.fail, self.calls)

    @unittest.skipUnless(OPENSSL, 'Requires OpenSSL')
    def test_encrypted_sidecar_round_trip_and_corruption_rejected_before_restore(self):
        archive = self.root / 'recovery-20261102T000000Z.tar.gz.enc'
        archive.write_bytes(b'archive fixture')
        (self.root / 'recovery.key').write_text('non-secret fixture passphrase')
        recovery.prepare(archive, root=self.root, run=self.fake_run)
        state = recovery.read(archive, run=self.fake_run)
        self.assertEqual(state['files'][recovery.FILES[0]]['kind'], 'file')
        sidecar = Path(str(archive) + '.hardening.enc')
        self.assertNotIn(b'original policy', sidecar.read_bytes())
        sidecar.write_bytes(sidecar.read_bytes() + b'corrupt')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'сумма'):
            recovery.read(archive, run=self.fake_run)
        self.assertEqual(self.calls, [])
