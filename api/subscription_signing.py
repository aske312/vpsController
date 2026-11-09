"""PRO subscription provenance. Trust anchors are provisioned out of band."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DOMAIN = b'vps-control/pro-subscription/v1\0'
TTL = 300


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
    """No automatic key generation: arbitrary installs must not become trusted."""
    if not filename:
        return None
    descriptor = os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
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
    return {'X-Subscription-Proof': encode(payload) + '.' + encode(key.sign(DOMAIN + payload))}


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
    parser = argparse.ArgumentParser(description='Create an offline PRO subscription signing key; print only its public trust record.')
    parser.add_argument('key_file', type=Path)
    args = parser.parse_args()
    key = Ed25519PrivateKey.generate()
    args.key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation prevents accidental rotation of an already pinned key.
    descriptor = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as target:
        target.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
        target.flush()
        os.fsync(target.fileno())
    print(json.dumps(public_record(key), sort_keys=True))


if __name__ == '__main__':
    main()
