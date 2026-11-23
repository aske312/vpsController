"""Verify the installed Agent sandbox permits native network monitoring sockets."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux systemd')
class RelaySandboxTests(unittest.TestCase):
    def test_network_monitoring_is_allowed_without_network_administration(self):
        self.assertEqual(os.geteuid(), 0, 'Run on an isolated root CI runner')
        installer = (Path(__file__).resolve().parents[1] / 'protocol-images/relay-agent/install.sh').read_text()
        unit = installer.split("cat >/etc/systemd/system/vps-control-relay-agent.service <<'EOF'\n", 1)[1].split('\nEOF', 1)[0]
        properties = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line)
        families = properties['RestrictAddressFamilies'].split()
        probe = ('import socket; '
                 's=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,socket.NETLINK_ROUTE); '
                 's.bind((0,0)); s.close()')
        for allowed, expected in ((families, 0), ([family for family in families if family != 'AF_NETLINK'], 1)):
            name = 'relay-network-monitor-test-' + uuid.uuid4().hex
            try:
                result = subprocess.run([
                    'systemd-run', '--quiet', '--wait', '--pipe', '--collect', '--unit=' + name,
                    '-p', 'DynamicUser=yes', '-p', 'RestrictAddressFamilies=' + ' '.join(allowed),
                    '-p', 'CapabilityBoundingSet=' + properties['CapabilityBoundingSet'],
                    '-p', 'NoNewPrivileges=' + properties['NoNewPrivileges'],
                    sys.executable, '-c', probe,
                ], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected:
                    self.assertIn('Address family not supported by protocol', result.stderr)
            finally:
                subprocess.run(['systemctl', 'stop', name], capture_output=True)
                subprocess.run(['systemctl', 'reset-failed', name], capture_output=True)


if __name__ == '__main__':
    unittest.main()
