"""Per-installation PRO subscription signatures; private keys never leave the host."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DOMAIN = b'vps-control/pro-subscription/v1\0'
TTL = 300
DEFAULT_KEY_FILE = Path('/etc/vps-control/subscription-signing.pem')


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid proof encoding')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def validate_nonce(value):
    if value and not re.fullmatch(r'[A-Za-z0-9_-]{22,128}', value):
        raise ValueError('Invalid subscription nonce')
    return value


def load_signer(filename):
    """Read an existing private signer without changing or rotating it."""
    if not filename:
        return None
    if Path(filename).is_symlink():
        raise ValueError('Signing key must not be a symlink')
    descriptor = os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError('Invalid signing key file')
        if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid not in {0, os.geteuid()}):
            raise ValueError('Signing key file must be private')
        key = serialization.load_pem_private_key(source.read(4097), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Expected an Ed25519 signing key')
    return key


def server_signer():
    configured = os.getenv('PRO_SUBSCRIPTION_SIGNING_KEY')
    if configured:
        return load_signer(configured)
    # Migration is performed by installation/update, never by a public request.
    if DEFAULT_KEY_FILE.exists() or DEFAULT_KEY_FILE.is_symlink():
        return load_signer(DEFAULT_KEY_FILE)
    return None


def create_signer(filename, *, ensure=False):
    """Publish a complete 0600 key atomically. Concurrent updates keep one identity."""
    filename = Path(filename)
    filename.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ensure and (filename.exists() or filename.is_symlink()):
        return load_signer(filename)
    descriptor, temporary = tempfile.mkstemp(prefix='.subscription-signing-', dir=filename.parent)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            key = Ed25519PrivateKey.generate()
            target.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, filename)
            if os.name == 'posix':
                parent = os.open(filename.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        except FileExistsError:
            if not ensure:
                raise
        return load_signer(filename)
    finally:
        os.unlink(temporary)


def public_record(key):
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {digest(public): encode(public)}


def proof_headers(key, body, url, nonce='', *, now=None):
    if key is None:
        return {}
    issued = int(time.time() if now is None else now)
    claims = {'v': 1, 'source': 'pro', 'alg': 'Ed25519', 'kid': next(iter(public_record(key))),
              'body_sha256': digest(body), 'url_sha256': digest(url.encode('utf-8')),
              'nonce': validate_nonce(nonce), 'iat': issued, 'exp': issued + TTL}
    payload = json.dumps(claims, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'X-Subscription-Proof': encode(payload) + '.' + encode(key.sign(DOMAIN + payload)),
            'X-Subscription-Public-Key': next(iter(public_record(key).values()))}


def verify(proof, body, url, nonce, trusted_keys, *, now=None):
    """Reference client verifier. trusted_keys MUST NOT come from the response."""
    if not isinstance(proof, str) or len(proof) > 4096:
        raise ValueError('Invalid subscription proof')
    try:
        encoded, signature = proof.split('.')
        payload = decode(encoded)
        claims = json.loads(payload)
        if not isinstance(claims, dict) or set(claims) != {
            'v', 'source', 'alg', 'kid', 'body_sha256', 'url_sha256', 'nonce', 'iat', 'exp'
        }:
            raise ValueError('Invalid proof claims')
        public = decode(trusted_keys[claims['kid']])
        if digest(public) != claims['kid']:
            raise ValueError('Invalid trusted key identity')
        Ed25519PublicKey.from_public_bytes(public).verify(decode(signature), DOMAIN + payload)
        current = int(time.time() if now is None else now)
        if (type(claims['v']) is not int or claims['v'] != 1 or claims['source'] != 'pro'
                or claims['alg'] != 'Ed25519' or type(claims['iat']) is not int
                or type(claims['exp']) is not int or claims['exp'] - claims['iat'] != TTL
                or claims['iat'] > current + 30 or claims['exp'] <= current
                or claims['body_sha256'] != digest(body)
                or claims['url_sha256'] != digest(url.encode('utf-8'))
                or claims['nonce'] != validate_nonce(nonce)):
            raise ValueError('Subscription proof does not match this response')
    except (InvalidSignature, KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError('Invalid subscription proof') from exc
    return claims


def main():
    parser = argparse.ArgumentParser(description='Create a PRO subscription signing key; print only its public record.')
    parser.add_argument('key_file', type=Path)
    parser.add_argument('--ensure', action='store_true', help='Keep and validate an existing key instead of rotating it')
    args = parser.parse_args()
    key = create_signer(args.key_file, ensure=args.ensure)
    print(json.dumps(public_record(key), sort_keys=True))


if __name__ == '__main__':
    main()
