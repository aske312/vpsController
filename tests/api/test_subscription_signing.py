import json
import os
from pathlib import Path
import subprocess
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.api.support import manager, ROOT

signing = manager.subscription_signing


class SubscriptionSigningTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.trusted = signing.public_record(self.key)
        self.body = 'proxies: []\n# Подписка\n'.encode('utf-8')
        self.url = 'https://pro.example/s/private-token?format=yaml'
        self.nonce = signing.encode(os.urandom(32))
        self.proof = signing.proof_headers(self.key, self.body, self.url, self.nonce, now=1000)['X-Subscription-Proof']

    def verify(self, **changes):
        values = dict(proof=self.proof, body=self.body, url=self.url, nonce=self.nonce,
                      trusted_keys=self.trusted, now=1001)
        values.update(changes)
        return signing.verify(**values)

    def test_valid_proof_and_independent_signature_verification(self):
        self.assertEqual(self.verify()['source'], 'pro')
        payload, signature = self.proof.split('.')
        self.key.public_key().verify(signing.decode(signature), signing.DOMAIN + signing.decode(payload))
        self.assertNotIn('private-token', signing.decode(payload).decode())

    def test_modified_content_url_nonce_and_unknown_key_are_rejected(self):
        for changes in ({'body': self.body + b' '}, {'url': self.url.replace('pro.example', 'fake.example')},
                        {'url': self.url + '2'}, {'nonce': signing.encode(os.urandom(32))},
                        {'trusted_keys': {}}, {'trusted_keys': signing.public_record(Ed25519PrivateKey.generate())}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                self.verify(**changes)

    def test_expired_and_future_proof_rejected(self):
        for now in (900, 1300, 2000):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.verify(now=now)

    def test_modified_claims_and_malformed_proofs_rejected(self):
        payload, signature = self.proof.split('.')
        claims = json.loads(signing.decode(payload))
        claims['exp'] += 3600
        tampered = signing.encode(json.dumps(claims).encode()) + '.' + signature
        for proof in ('', '.', 'a.b.c', '!.!', 'a.a', 'a' * 4097, tampered):
            with self.subTest(proof=proof[:10]), self.assertRaises(ValueError):
                self.verify(proof=proof)

    def test_unsigned_server_does_not_generate_keys_or_proof(self):
        self.assertIsNone(signing.load_signer(None))
        self.assertEqual(signing.proof_headers(None, self.body, self.url), {})
        with self.assertRaises(OSError):
            signing.load_signer(str(ROOT / 'missing-subscription-signing-key.pem'))

    def test_automatic_provisioning_keeps_one_identity_and_default_loader_uses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'server.pem'
            with ThreadPoolExecutor(max_workers=4) as pool:
                records = list(pool.map(lambda _: signing.public_record(signing.create_signer(filename, ensure=True)), range(8)))
            self.assertTrue(all(record == records[0] for record in records))
            original = filename.read_bytes()
            with patch.object(signing, 'DEFAULT_KEY_FILE', filename), patch.dict(os.environ, {'PRO_SUBSCRIPTION_SIGNING_KEY': ''}):
                self.assertEqual(signing.public_record(signing.server_signer()), records[0])
            command = [sys.executable, str(ROOT / 'api/subscription_signing.py'), '--ensure', str(filename)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), records[0])
            self.assertEqual(filename.read_bytes(), original)
            self.assertEqual(list(filename.parent.iterdir()), [filename])

    def test_provisioning_does_not_replace_invalid_key_or_publish_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'server.pem'
            with patch.object(signing.os, 'link', side_effect=OSError('publish failed')):
                with self.assertRaises(OSError):
                    signing.create_signer(filename, ensure=True)
            self.assertEqual(list(filename.parent.iterdir()), [])
            filename.write_bytes(b'not a private key')
            with self.assertRaises(ValueError):
                signing.create_signer(filename, ensure=True)
            self.assertEqual(filename.read_bytes(), b'not a private key')

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership and symlinks')
    def test_provisioning_rejects_symlink_and_insecure_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory) / 'real.pem'
            signing.create_signer(real, ensure=True)
            link = Path(directory) / 'link.pem'
            link.symlink_to(real)
            with self.assertRaises(ValueError):
                signing.create_signer(link, ensure=True)
            real.chmod(0o644)
            with self.assertRaises(ValueError):
                signing.create_signer(real, ensure=True)

    def test_key_generation_is_exclusive_and_only_prints_public_record(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'private.pem'
            command = [sys.executable, str(ROOT / 'api/subscription_signing.py'), str(filename)]
            created = subprocess.run(command, capture_output=True, text=True, check=True)
            key = signing.load_signer(filename)
            self.assertEqual(json.loads(created.stdout), signing.public_record(key))
            self.assertNotIn('PRIVATE KEY', created.stdout)
            before = filename.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(filename.read_bytes(), before)
            if os.name == 'posix':
                self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
                filename.chmod(0o644)
                with self.assertRaises(ValueError):
                    signing.load_signer(filename)


class SignedSubscriptionEndpointTests(unittest.TestCase):
    from tests.api.test_subscription_formats import SubscriptionFormatTests
    setUp = SubscriptionFormatTests.setUp

    def test_http_proof_covers_exact_body_and_request(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'signer.pem'
            key = Ed25519PrivateKey.generate()
            filename.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            filename.chmod(0o600)
            nonce = signing.encode(os.urandom(32))
            with patch.dict(os.environ, {'PRO_SUBSCRIPTION_SIGNING_KEY': str(filename)}):
                client = TestClient(manager.app)
                for prefix in ('/s/', '/api/mihomo/subscriptions/'):
                    response = client.get(prefix + 'test-token', headers={'X-Subscription-Nonce': nonce})
                    self.assertEqual(response.status_code, 200, response.text)
                    claims = signing.verify(response.headers['x-subscription-proof'], response.content,
                                            str(response.url), nonce, signing.public_record(key))
                    self.assertEqual(claims['source'], 'pro')
                    self.assertEqual(response.headers['x-subscription-public-key'], next(iter(signing.public_record(key).values())))

    def test_configured_missing_key_fails_closed_without_exposing_path(self):
        from fastapi.testclient import TestClient
        before = list(self.store)
        with patch.dict(os.environ, {'PRO_SUBSCRIPTION_SIGNING_KEY': '/private/missing/signing.pem'}):
            response = TestClient(manager.app).get('/s/test-token')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('/private/', response.text)
        self.assertNotIn('x-subscription-proof', response.headers)
        self.assertEqual(self.store, before)

    def test_invalid_nonce_rejected_before_registration(self):
        from fastapi.testclient import TestClient
        response = TestClient(manager.app).get('/s/test-token', headers={'X-Subscription-Nonce': 'short'})
        self.assertEqual(response.status_code, 400)
        self.provision.assert_not_called()


class SigningUpdateHookTests(unittest.TestCase):
    def test_update_provisions_only_managed_mihomo_and_propagates_failure(self):
        bash = str(Path('D:/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Requires Bash')
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        function = re.search(r'(?ms)^ensure_mihomo_profile_runtimes\(\) \{.*?^\}', source).group()
        for managed, has_signer, signing_exit, expected in ((False, True, 0, 0), (True, True, 19, 19), (True, True, 0, 23), (True, False, 0, 23)):
            with self.subTest(managed=managed, has_signer=has_signer, signing_exit=signing_exit), tempfile.TemporaryDirectory(dir=ROOT / '.runtime') as directory:
                root = Path(directory)
                python = root / 'venv/bin/python'
                python.parent.mkdir(parents=True)
                if has_signer:
                    helper = root / 'api/subscription_signing.py'
                    helper.parent.mkdir()
                    helper.touch()
                python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TEST_EVENTS"\nexit '+str(signing_exit)+'\n', encoding='utf-8', newline='\n')
                python.chmod(0o755)
                posix_root = root.resolve().as_posix()
                if os.name == 'nt':
                    posix_root = '/' + posix_root[0].lower() + posix_root[2:]
                script = ('set -eu\nINSTALL_DIR='+shlex.quote(posix_root)+'\nDATA_DIR="$INSTALL_DIR"\n'
                          'export TEST_EVENTS="$INSTALL_DIR/events"\n'
                          'python3() { return '+('0' if managed else '1')+'; }\n'
                          'env_value() { printf "%s" "/private/custom server.pem"; }\n'
                          'bash() { echo limits >> "$TEST_EVENTS"; return 23; }\n'
                          +function+'\nensure_mihomo_profile_runtimes\n')
                result = subprocess.run([bash, '-c', script], capture_output=True, text=True, env={k:v for k,v in os.environ.items() if k != 'PRO_SUBSCRIPTION_SIGNING_KEY'})
                self.assertEqual(result.returncode, expected, result.stderr)
                events = (root / 'events').read_text() if (root / 'events').exists() else ''
                if managed and has_signer:
                    self.assertIn('--ensure /private/custom server.pem', events)
                    self.assertEqual('limits' in events, signing_exit == 0)
                elif managed:
                    self.assertEqual(events, 'limits\n')
                else:
                    self.assertEqual(events, '')
