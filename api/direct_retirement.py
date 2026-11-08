"""Preflight an explicit PRO cleanup; never infer ownership from installed files."""
from pathlib import Path
import argparse
import json
import os

from component_registry import ComponentRegistry, RegistryError, inventory, paths_from_manifest

DIRECT_IMAGES = {
    'wg': 'wireguard', 'awg': 'amneziawg', 'shadowsocks': 'shadowsocks',
    'vless-reality-xhttp': 'vless-reality-xhttp', 'hysteria2': 'hysteria2',
    'tuic': 'tuic', 'trojan': 'trojan', 'openvpn': 'openvpn', 'ikev2': 'ikev2',
}


def cleanup_plan(install_dir: Path, data_dir: Path, environment=None):
    environment = os.environ if environment is None else environment
    metadata = dict(line.split('=', 1) for line in (install_dir / '.prebuilt-release').read_text().splitlines() if '=' in line)
    if metadata.get('edition') != 'pro':
        raise RegistryError('Direct cleanup is available only in PRO')
    if environment.get('ACCESS_MODE', 'external') != 'external':
        raise RegistryError('Open external panel access before removing Direct')
    registry = ComponentRegistry(data_dir)
    clients_path = data_dir / 'clients.json'
    clients = json.loads(clients_path.read_text()) if clients_path.exists() else []
    if not isinstance(clients, list) or any(not isinstance(row, dict) for row in clients):
        raise RegistryError('Invalid Direct clients')
    result = []
    for identity, directory in DIRECT_IMAGES.items():
        receipt = registry.receipt(identity)
        if not receipt:
            if any(row.get('protocol') == identity for row in clients):
                raise RegistryError('Direct clients have no ownership receipt: ' + identity)
            continue
        manifest = json.loads((install_dir / 'protocol-images' / directory / 'manifest.json').read_text())
        if manifest.get('id') != identity:
            raise RegistryError('Invalid Direct manifest: ' + identity)
        interface = environment.get(manifest.get('interface_env', ''), {'wg': 'wg0', 'awg': 'awg0'}.get(identity, ''))
        if interface.startswith('mh-') or '/' in interface or '..' in interface:
            raise RegistryError('Mihomo interfaces cannot be removed')
        # Explicitly bind deletion to the standalone component's namespace.
        allowed = [Path('/etc/vps-control') / directory]
        if identity == 'wg':
            allowed = [Path('/etc/wireguard') / (interface + '.conf')]
        elif identity == 'awg':
            allowed = [Path('/etc/amnezia/amneziawg') / (interface + '.conf'), Path('/etc/amnezia') / (interface + '.conf')]
        elif identity == 'ikev2':
            allowed += [Path('/etc/swanctl') / name for name in (
                'swanctl.conf', 'users.conf', 'private/caKey.pem', 'private/serverKey.pem',
                'x509ca/caCert.pem', 'x509/serverCert.pem')]
        # paths_from_manifest uses the actual process environment, as does purge.
        paths = paths_from_manifest(manifest, interface, install_dir, data_dir, kind='data_paths')
        if not paths or any(not path.is_absolute() or ".." in path.parts or not any(path == root or path.is_relative_to(root) for root in allowed) for path in paths):
            raise RegistryError('Direct data overlaps an unexpected namespace: ' + identity)
        inventory(paths)
        result.append((identity, bool(receipt.get('retained'))))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        for identity, retained in cleanup_plan(args.install_dir, args.data_dir):
            print(identity + ':' + ('retained' if retained else 'installed'))
    except (RegistryError, OSError, ValueError, TypeError, KeyError) as error:
        raise SystemExit(str(error)) from None
