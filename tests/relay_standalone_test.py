"""Behaviour checks for standalone Agent credentials and bootstrap arguments."""
import hashlib
import importlib.util
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'protocol-images/relay-agent'))
spec = importlib.util.spec_from_file_location('relay_credentials', ROOT / 'protocol-images/relay-agent/credentials.py')
credentials = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credentials)


class StandaloneTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').name == 'posix', 'Installer config uses Unix ownership')
    def test_installer_preserves_valid_limits_and_rejects_invalid_before_replacing_config(self):
        import grp
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        source=(ROOT/'protocol-images/relay-agent/install.sh').read_text().split('python3 - "${ROOT}" "${ENV_FILE:-/etc/vps-control.env}" "${SOURCE}"',1)[1]
        script=source.split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            limits={'udp_budget_mib':64,'max_sessions':256,'max_sessions_per_route':192,'statistics_reset_seconds':86400}
            (root/'config.json').write_text(json.dumps({'public_ip':'8.8.8.8','limits':limits}))
            for name in ('server.crt','server.key','token'): (root/name).write_text('synthetic')
            with patch.object(sys,'argv',['config',str(root),str(root/'missing-env'),str(ROOT/'protocol-images/relay-agent')]), \
                 patch.dict(os.environ,{'RELAY_PUBLIC_IP':'8.8.8.8','RELAY_API_PORT':'9443','RELAY_DOMAINS_JSON':'[]','RELAY_CDN_DOMAINS_JSON':'[]'}), \
                 patch.object(grp,'getgrnam',return_value=SimpleNamespace(gr_gid=0)),patch.object(os,'chown'), \
                 patch.object(subprocess,'run',return_value=SimpleNamespace(stdout='8.8.8.8')):
                exec(compile(script,'installer-config','exec'),{})
                saved=json.loads((root/'config.json').read_text())
                self.assertEqual(saved['limits'],limits)
                saved['limits']={'max_sessions':'invalid'}
                (root/'config.json').write_text(json.dumps(saved));before=(root/'config.json').read_bytes()
                with self.assertRaises(ValueError):exec(compile(script,'installer-config','exec'),{})
                self.assertEqual((root/'config.json').read_bytes(),before)
                self.assertEqual((root/'token').read_text(),'synthetic')

    def test_connection_export_matches_certificate_and_saved_identity(self):
        # A syntactically valid PEM with synthetic DER checks the hash contract.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config.json').write_text(json.dumps({'public_ip': '8.8.8.8'}))
            (root / 'token').write_text('synthetic-token\n')
            (root / 'server.crt').write_text(ssl.DER_cert_to_PEM_cert(b'synthetic-certificate'))
            result = credentials.connection(root)
            self.assertEqual(result, {'api_version': 1, 'agent_url': 'https://8.8.8.8:9443',
                'certificate_sha256': hashlib.sha256(b'synthetic-certificate').hexdigest(),
            'token': 'synthetic-token', 'addresses': {'ipv4': ['8.8.8.8'], 'ipv6': [], 'domains': [], 'cdn_domains': []}})
            self.assertEqual(credentials.connection(root), result)
            (root / 'config.json').write_text(json.dumps({'public_ip': '8.8.8.8', 'api_port': 9444}))
            self.assertEqual(credentials.connection(root)['agent_url'], 'https://8.8.8.8:9444')
            for port in (True, '9444', 80, 20000, 20999, 65536):
                (root / 'config.json').write_text(json.dumps({'public_ip': '8.8.8.8', 'api_port': port}))
                with self.subTest(port=port), self.assertRaises(ValueError): credentials.connection(root)

    @unittest.skipUnless(__import__('os').name == 'posix', 'Bash installer behaviour requires Linux')
    def test_invalid_arguments_exit_before_installing(self):
        for arguments in (['--unknown'], ['--public-ip'], ['--api-port'], ['--api-port=nope'], ['--api-port=20001'], ['--api-port=80']):
            result = subprocess.run(['bash', str(ROOT / 'scripts/install-agent.sh'), *arguments],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('token', result.stdout)


if __name__ == '__main__':
    unittest.main()
