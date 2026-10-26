"""Opt-in runtime upgrade test with disposable systemd units only."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unittest
import uuid

from tests.api.support import ROOT


@unittest.skipUnless(sys.platform == 'linux' and os.getenv('PRIVACY_SYSTEMD_TESTS') == '1',
                     'Requires explicitly enabled Linux/systemd tests')
class SubscriptionRuntimeSystemdTests(unittest.TestCase):
    def test_runtime_code_refresh_migrates_dependency_without_restarting_protocol(self):
        self.check_refresh(running=True)

    def test_fresh_runtime_install_starts_the_service(self):
        self.check_refresh(running=False)

    def check_refresh(self, *, running):
        if os.geteuid() != 0 or not Path('/run/systemd/system').is_dir():
            self.skipTest('Requires root and systemd')
        name = 'vps-control-limits-test-' + uuid.uuid4().hex
        helper, consumer = name + '.service', name + '-consumer.service'
        directory = Path('/run/systemd/system')
        files = [directory / helper, directory / consumer]

        def command(*args, check=True):
            return subprocess.run(['systemctl', *args], capture_output=True, text=True, check=check, timeout=30)

        try:
            files[0].write_text('[Unit]\nDescription=Disposable accounting helper\n[Service]\nExecStart=/usr/bin/sleep infinity\nRestart=on-failure\nRestartSec=.1\n[Install]\nWantedBy=multi-user.target\n')
            files[1].write_text(f'[Unit]\nDescription=Disposable protocol process\nRequires={helper}\nAfter={helper}\n[Service]\nExecStart=/usr/bin/sleep infinity\n')
            command('daemon-reload')
            if running:
                command('start', consumer)
            previous = command('show', helper, '--property=MainPID', '--value').stdout.strip()
            protocol_pid = command('show', consumer, '--property=MainPID', '--value').stdout.strip()
            source = (ROOT / 'protocol-images/mihomo/install-subscription-limits.sh').read_text()
            self.assertIn('Wants=vps-control-subscription-limits.service', source)
            self.assertIn('ExecStartPre=+${PYTHON} ${ROOT}/subscription_limits.py refresh', source)
            # Simulate the installer's migration of a running older release.
            files[1].write_text(files[1].read_text().replace('Requires=', 'Wants='))
            command('daemon-reload')
            function = re.search(r'(?ms)^refresh_limits_service\(\) \{.*?^\}', source).group()
            # Scope enablement to /run; never leave persistent test symlinks.
            script = 'set -eu\nsystemctl() { if [[ "$1" == enable ]]; then command systemctl enable --runtime "$2"; else command systemctl "$@"; fi; }\n'
            result = subprocess.run(['bash', '-c', script + function + '\nrefresh_limits_service ' +
                                     shlex.quote(helper) + ' ' + shlex.quote(previous)], capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            current = command('show', helper, '--property=MainPID', '--value').stdout.strip()
            self.assertNotEqual(current, '0')
            self.assertNotEqual(current, previous)
            if running:
                self.assertEqual(command('is-active', consumer).stdout.strip(), 'active')
                self.assertEqual(command('show', consumer, '--property=MainPID', '--value').stdout.strip(), protocol_pid)
        finally:
            command('stop', consumer, helper, check=False)
            command('disable', '--runtime', helper, check=False)
            for path in files:
                path.unlink(missing_ok=True)
            command('daemon-reload', check=False)
            command('reset-failed', consumer, helper, check=False)
