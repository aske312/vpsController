import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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
