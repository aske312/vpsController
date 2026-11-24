"""Encryption at rest for assistant content, independent of panel credentials.

The API service can decrypt; a host administrator controlling that service can
also decrypt. This is deliberately not advertised as end-to-end encryption.
"""
import base64
import json
import os
from pathlib import Path
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

FORMAT = 'nodera-private-v1'
CONTENT = {'conversations', 'personalization', 'bot-history', 'requests', 'nodera-bot/commands'}


def scope(path):
    path = Path(path).absolute()
    for parent in path.parents:
        if parent.name == 'assistant':
            relative = path.relative_to(parent)
            if (relative.parts[0] in CONTENT or relative.parts[:2] == ('nodera-bot', 'commands')) and path.suffix == '.json':
                return parent, relative.as_posix().encode()
            return None
    return None


def key(root, *, create=False):
    file = root / '.privacy-key'
    if create and not file.exists():
        for existing in root.rglob('*.json'):
            if scope(existing) is None and existing.parent != root:
                continue
            value = json.loads(existing.read_text(encoding='utf-8'))
            if isinstance(value, dict) and 'private_storage' in value:
                raise ValueError('Private storage key is missing; restore it before writing')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = root / ('.privacy-key.' + uuid.uuid4().hex)
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(AESGCM.generate_key(bit_length=256))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, file)  # Publish a complete key without replacing another worker's key.
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if file.is_symlink():
        raise ValueError('Private storage key must not be a symlink')
    try:
        value = file.read_bytes()
    except FileNotFoundError:
        raise ValueError('Private storage key is missing; restore it before reading') from None
    if len(value) != 32:
        raise ValueError('Private storage key is invalid')
    return value


def seal(path, value):
    selected = scope(path)
    if selected is None:
        return value
    root, associated = selected
    return seal_record(root, associated, value)


def seal_record(root, associated, value):
    nonce = os.urandom(12)
    encrypted = AESGCM(key(root, create=True)).encrypt(
        nonce, json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), associated)
    return {'private_storage': FORMAT, 'ciphertext': base64.b64encode(nonce + encrypted).decode()}


def unseal(path, value):
    if 'private_storage' not in value:
        return value  # Legacy records are migrated at startup under the application lock.
    selected = scope(path)
    if selected is None or value.get('private_storage') != FORMAT:
        raise ValueError('Unsupported private storage format')
    root, associated = selected
    return unseal_record(root, associated, value)


def unseal_record(root, associated, value):
    if 'private_storage' not in value:
        return value
    if value.get('private_storage') != FORMAT:
        raise ValueError('Unsupported private storage format')
    encoded = base64.b64decode(value['ciphertext'], validate=True)
    try:
        decoded = AESGCM(key(root)).decrypt(encoded[:12], encoded[12:], associated)
    except InvalidTag:
        raise ValueError('Private record authentication failed') from None
    result = json.loads(decoded)
    if not isinstance(result, dict):
        raise ValueError('Invalid private record')
    return result


def migrate(data_dir):
    import application_operation as operations
    root = Path(data_dir) / 'assistant'
    with operations.locked(Path(data_dir)):
        for name in sorted(CONTENT):
            for file in (root / name).rglob('*.json'):
                value = json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('Invalid legacy private record')
                if 'private_storage' in value:
                    unseal(file, value)  # Fail closed on a lost key or damaged ciphertext.
                    continue
                stat = file.stat()
                operations.atomic_json(file, value)
                os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def panel_actor(telegram_id=None):
    """A panel login never proves ownership of a Telegram identity."""
    from fastapi import HTTPException
    if telegram_id is not None:
        raise HTTPException(403, 'Личная переписка доступна только её участнику через NoderaBot')
    return 'panel'
