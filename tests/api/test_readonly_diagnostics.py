"""Diagnostics resolve the saved panel address without reconfiguring access."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from tests.api.support import ROOT
from tests.api.test_security_hardening import BASH


@unittest.skipUnless(BASH, 'Requires Bash')
class ReadonlyDiagnosticTests(unittest.TestCase):
    def invoke(self, action, mode='external', domain='', endpoint='192.0.2.1', panel_host='192.168.1.2'):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        functions = '\n'.join(re.search(r'(?ms)^' + name + r'\(\) \{.*?^\}', source).group()
                              for name in ('panel_check_url', 'verify_app', 'integrity_check'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = root.resolve().as_posix()
            if os.name == 'nt':
                value = '/' + value[0].lower() + value[2:]
            script = r'''set -Eeuo pipefail
DATA_DIR=$TEST_ROOT
APP_NAME=vps-control
HTTP_PORT=8000
LOCAL_ADDRESS=192.168.1.3
env_value() {
  case "$1" in
    PUBLIC_DOMAIN) printf '%s' "$TEST_DOMAIN" ;;
    PUBLIC_ENDPOINT) printf '%s' "$TEST_ENDPOINT" ;;
    PUBLIC_IP) printf '192.0.2.2' ;;
    PANEL_HOST) printf '%s' "$TEST_PANEL_HOST" ;;
    INTERNAL_PANEL_HOST) printf 'admin.312.net' ;;
  esac
}
configure_access() { printf mutation > "$TEST_ROOT/mutated"; }
die() { exit 1; }
info() { :; }
ok() { :; }
systemctl() { :; }
curl() { printf '%s\n' "$*" >> "$TEST_ROOT/requests"; }
''' + functions + '\n' + action + '\n'
            result = subprocess.run([BASH, '-s'], input=script, text=True, capture_output=True,
                env={**os.environ, 'TEST_ROOT': value, 'ACCESS_MODE': mode, 'TEST_DOMAIN': domain,
                     'TEST_ENDPOINT': endpoint, 'TEST_PANEL_HOST': panel_host}, timeout=5)
            return result, (root / 'mutated').exists(), (root / 'requests').read_text() if (root / 'requests').exists() else ''

    def test_saved_addresses_resolve_without_mutations(self):
        for options, expected in (({}, 'http://192.0.2.1:8000'), ({'domain': 'panel.example'}, 'https://panel.example'),
                                  ({'mode': 'vpn'}, 'http://admin.312.net'), ({'mode': 'local'}, 'http://192.168.1.2:8000'),
                                  ({'mode': 'local', 'panel_host': '0.0.0.0'}, 'http://192.168.1.3:8000'),
                                  ({'endpoint': ''}, 'http://192.0.2.2:8000')):
            with self.subTest(options=options):
                result, mutated, _ = self.invoke('panel_check_url', **options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertFalse(mutated)

    def test_verify_checks_services_and_saved_url_without_writes(self):
        result, mutated, requests = self.invoke('verify_app')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(mutated)
        self.assertIn('http://127.0.0.1:8000/api/health', requests)
        self.assertIn('http://192.0.2.1:8000/', requests)

    def test_integrity_rejection_does_not_reconfigure_access_first(self):
        result, mutated, _ = self.invoke('integrity_check')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(mutated)
