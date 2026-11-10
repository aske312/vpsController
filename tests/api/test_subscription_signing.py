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
    def test_old_updater_runs_new_payload_migration_and_preserves_key(self):
        bash = str(Path('D:/Git/bin/bash.exe')) if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Requires Bash')

        def posix(path):
            value = Path(path).resolve().as_posix()
            return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value

        source = (ROOT / 'protocol-images/mihomo/install-subscription-limits.sh').read_text(encoding='utf-8')
        # Execute the real migration prelude, stopping before any systemd writes.
        prelude = source.split('\nprevious_pid=', 1)[0]
        prelude = re.sub(r'(?m)^ROOT=.*$', 'ROOT=' + shlex.quote(posix(ROOT / 'protocol-images/mihomo')), prelude)
        prelude = re.sub(r'(?m)^PYTHON=.*$', 'PYTHON=' + shlex.quote(posix(sys.executable)), prelude)
        # This is the pre-signing updater contract. It knows only the existing
        # limits installer; it cannot call a function added in the next release.
        legacy = ('ensure_mihomo_profile_runtimes() {\n'
                  '  python3 check-managed || return 0\n'
                  '  bash "$INSTALL_DIR/protocol-images/mihomo/install-subscription-limits.sh" || return $?\n'
                  '}\n')
        for mode in ('unmanaged', 'new', 'environment', 'corrupt', 'override'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=ROOT / '.runtime') as directory:
                root = Path(directory)
                key = root / 'private key.pem'
                config = root / 'environment'
                config.write_text('PRO_SUBSCRIPTION_SIGNING_KEY="' + posix(key) + '"\n', encoding='utf-8')
                hook = root / 'protocol-images/mihomo/install-subscription-limits.sh'
                hook.parent.mkdir(parents=True)
                hook.write_text(prelude + 'echo limits >> "$TEST_EVENTS"\n', encoding='utf-8', newline='\n')
                if mode == 'corrupt':
                    key.write_bytes(b'corrupt existing key')
                environment = {k:v for k,v in os.environ.items() if k not in {'PRO_SUBSCRIPTION_SIGNING_KEY', 'ENV_FILE'}}
                environment.update(INSTALL_DIR=posix(root), ENV_FILE=posix(config), TEST_EVENTS=posix(root / 'events'))
                if mode != 'environment':
                    environment['PRO_SUBSCRIPTION_SIGNING_KEY'] = posix(key)
                if mode == 'override':
                    config.write_text('PRO_SUBSCRIPTION_SIGNING_KEY="' + posix(root / 'unused.pem') + '"\n')
                script = 'set -eu\npython3() { return ' + ('1' if mode == 'unmanaged' else '0') + '; }\n' + legacy + 'ensure_mihomo_profile_runtimes\n'

                def run():
                    return subprocess.run([bash, '-c', script], capture_output=True, text=True, env=environment)

                result = run()
                if mode == 'corrupt':
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(key.read_bytes(), b'corrupt existing key')
                    self.assertFalse((root / 'events').exists())
                elif mode == 'unmanaged':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(key.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    before = key.read_bytes()
                    signer = signing.load_signer(key)
                    response = signing.proof_headers(signer, b'proxies: []', 'https://example.test/s/token', 'n' * 32)
                    signing.verify(response['X-Subscription-Proof'], b'proxies: []', 'https://example.test/s/token', 'n' * 32, signing.public_record(signer))
                    self.assertEqual(run().returncode, 0)
                    self.assertEqual(key.read_bytes(), before)
                    self.assertEqual((root / 'events').read_text().splitlines(), ['limits', 'limits'])
                    self.assertFalse((root / 'unused.pem').exists())
                    if os.name != 'nt':
                        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
