import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests.api.support import ROOT

BASH = shutil.which('bash') if os.name != 'nt' else next((str(p) for p in [Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')] if p.exists()), None)


@unittest.skipUnless(BASH, 'Requires Bash')
class KernelUpdateTests(unittest.TestCase):
    def invoke(self, rows, simulation='Inst new-kernel', fail_simulation=False):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        function = re.search(r'(?ms)^update_kernel\(\) \{.*?^\}', source).group()
        script = '''set -eu
REBOOT_AFTER_UPDATE=no
CURRENT_ACTION=kernel-update
info() { :; }
ok() { :; }
die() { echo "$*" >&2; exit 43; }
dpkg-query() { printf '%s\\n' "$TEST_PACKAGES"; }
apt-get() { if [[ "$1" == -s ]]; then [[ "$TEST_FAIL_SIMULATION" == no ]] || return 17; printf '%s\\n' "$TEST_SIMULATION"; else printf 'APT %s\\n' "$*"; fi; }
systemctl() { echo UNEXPECTED_REBOOT; exit 77; }
''' + function + '\nupdate_kernel\necho "REBOOT=$REBOOT_AFTER_UPDATE"\n'
        return subprocess.run([BASH, '-c', script], env={**os.environ, 'TEST_PACKAGES': rows, 'TEST_SIMULATION': simulation, 'TEST_FAIL_SIMULATION': 'yes' if fail_simulation else 'no'}, capture_output=True, text=True, encoding='utf-8')

    def test_debian_and_ubuntu_keep_installed_kernel_family(self):
        cases = [
            ('linux-image-amd64', 'linux-headers-amd64'),
            ('linux-image-cloud-amd64', 'linux-headers-cloud-amd64'),
            ('linux-image-arm64', 'linux-headers-arm64'),
            ('linux-generic-hwe-22.04', 'linux-headers-generic-hwe-22.04'),
            ('linux-virtual', 'linux-image-virtual'),
        ]
        for packages in cases:
            with self.subTest(packages=packages):
                rows = '\n'.join(p + ' ii ' for p in packages) + '\nlinux-image-6.12.95-amd64 ii \nlinux-libc-dev ii \nlinux-image-generic rc '
                result = self.invoke(rows)
                self.assertEqual(result.returncode, 0, result.stderr)
                install = next(line for line in result.stdout.splitlines() if 'install -y' in line)
                self.assertIn('--no-remove', install)
                for package in packages:
                    self.assertIn(package, install)
                self.assertNotIn('6.12.95', install)
                self.assertNotIn('linux-libc-dev', install)
                self.assertIn('REBOOT=yes', result.stdout)

    def test_absent_metapackage_does_not_install_or_choose_another_kernel(self):
        result = self.invoke('linux-image-6.12.95-amd64 ii ')
        self.assertEqual(result.returncode, 43)
        self.assertNotIn('APT ', result.stdout)

    def test_no_updates_does_not_require_reboot(self):
        result = self.invoke('linux-image-amd64 ii ', simulation='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('REBOOT=no', result.stdout)

    def test_failed_simulation_cancels_kernel_installation(self):
        result = self.invoke('linux-image-amd64 ii ', fail_simulation=True)
        self.assertEqual(result.returncode, 43)
        self.assertNotIn('install -y', result.stdout)
