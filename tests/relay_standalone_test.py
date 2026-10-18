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
