"""Local administrator bridge; never shares panel credentials with the agent."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import urllib.error
import urllib.request

CONFIG_DIR = Path('/etc/vps-control-relay-agent')
TOKEN_LOCK = threading.Lock()


def connection(config_dir=CONFIG_DIR, reveal=False):
    root = Path(config_dir)
    config = json.loads((root / 'config.json').read_text())
    der = ssl.PEM_cert_to_DER_cert((root / 'server.crt').read_text())
    result = {'api_version': 1, 'agent_url': f"https://{config['public_ip']}:9443",
              'certificate_sha256': hashlib.sha256(der).hexdigest()}
    if reveal: result['token'] = (root / 'token').read_text().strip()
    return result


def local_request(path, config_dir=CONFIG_DIR):
    root = Path(config_dir)
    context = ssl.create_default_context(cafile=str(root / 'server.crt'))
    token = (root / 'token').read_text().strip()
    request = urllib.request.Request('https://127.0.0.1:9443'+path, headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(request, context=context, timeout=4) as response:
        return json.load(response)


def write_private(path, value, mode, gid):
    fd, temporary = tempfile.mkstemp(prefix='.relay-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, gid)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def rotate_token(config_dir=CONFIG_DIR):
    root = Path(config_dir)
    with TOKEN_LOCK:
        path = root / 'config.json'
        previous_config, previous_token = path.read_text(), (root / 'token').read_text()
        config = json.loads(previous_config)
        gid = path.stat().st_gid
        token = secrets.token_urlsafe(48)
        config['token_sha256'] = hashlib.sha256(token.encode()).hexdigest()
        try:
            write_private(path, json.dumps(config), 0o640, gid)
            write_private(root / 'token', token, 0o600, 0)
        except OSError:
            write_private(path, previous_config, 0o640, gid)
            write_private(root / 'token', previous_token, 0o600, 0)
            raise
    # The agent reads only the hash for each request; listeners keep running.
    return connection(root, reveal=True)
