"""Explicit root-only console export of Relay Agent connection parameters."""
import hashlib
import json
import os
from pathlib import Path
import ssl


def connection(root):
    root = Path(root)
    config = json.loads((root / 'config.json').read_text())
    certificate = ssl.PEM_cert_to_DER_cert((root / 'server.crt').read_text())
    return {'api_version': 1, 'agent_url': f"https://{config['public_ip']}:9443",
            'certificate_sha256': hashlib.sha256(certificate).hexdigest(),
            'token': (root / 'token').read_text().strip()}


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Connection parameters may only be shown by root')
    print(json.dumps(connection('/etc/vps-control-relay-agent'), ensure_ascii=False, indent=2))
