"""Public address inventory. Discovery reads local configuration, never probes DNS."""
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tempfile

LIMIT = 64


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if address.is_global and not address.is_multicast and not getattr(address, 'ipv4_mapped', None) and '%' not in str(value):
            return str(address)
    except ValueError:
        pass
    return None


def domain(value):
    value = str(value).strip().lower().rstrip('.')
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError:
        return None
    if (len(value) <= 253 and not value.endswith(('.localhost', '.local', '.internal', '.lan'))
            and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', value)):
        return value
    return None


def normalize(value, primary=None):
    if not isinstance(value, dict):
        raise ValueError('Invalid address inventory')
    result = {}
    for key, version in (('ipv4', 4), ('ipv6', 6), ('domains', None)):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > LIMIT or any(not isinstance(item, str) for item in items):
            raise ValueError('Invalid address list')
        result[key] = sorted({item for raw in items if (item := domain(raw) if version is None else public_ip(raw))
                              and (version is None or ipaddress.ip_address(item).version == version)})
    if primary and (address := public_ip(primary)):
        key = 'ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6'
        result[key] = [address, *[item for item in result[key] if item != address]][:LIMIT]
    return result


def read_inventory(root, config=None):
    root = Path(root)
    config = config or json.loads((root / 'config.json').read_text())
    try:
        value = json.loads((root / 'addresses.json').read_text())
        return normalize(value, config.get('public_ip'))
    except (OSError, ValueError, TypeError):
        return normalize({'ipv4': [config['public_ip']] if config.get('public_ip') else [], 'domains': config.get('domains', [])})


def discover(config, *, addresses=None, environment_paths=None, caddy_root=Path('/etc/caddy'), hostname_path=Path('/etc/hostname'), hosts_path=Path('/etc/hosts')):
    if addresses is None:
        addresses = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5, check=True).stdout.split()
    result = {'ipv4': [], 'ipv6': [], 'domains': list(config.get('domains', []))}
    for raw in addresses:
        if address := public_ip(raw):
            result['ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6'].append(address)
    for path in environment_paths or (Path('/etc/vps-control/environment'), Path('/etc/vps-control.env')):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, raw = line.split('=', 1)
            if key.strip() not in ('PUBLIC_DOMAIN', 'CDN_DOMAIN', 'CF_DOMAIN', 'TLS_DOMAIN', 'PUBLIC_ENDPOINT'):
                continue
            values = shlex.split(raw, comments=True)
            if values and (name := domain(values[0])):
                result['domains'].append(name)
    if hostname_path.exists() and (name := domain(hostname_path.read_text().strip())):
        result['domains'].append(name)
    own = set(result['ipv4'] + result['ipv6'] + [config.get('public_ip', '')])
    if hosts_path.exists():
        for line in hosts_path.read_text().splitlines():
            fields = line.split('#', 1)[0].split()
            if len(fields) > 1 and fields[0] in own | {'127.0.1.1'}:
                result['domains'].extend(name for raw in fields[1:] if (name := domain(raw)))
    files = ([caddy_root / 'Caddyfile'] if (caddy_root / 'Caddyfile').is_file() else []) + sorted(caddy_root.rglob('*.caddy'))[:100]
    for path in files:
        if path.stat().st_size > 1024 * 1024:
            continue
        depth = 0
        for raw in path.read_text().splitlines():
            line = raw.split('#', 1)[0].strip()
            candidates = []
            if depth == 0 and '{' in line:
                candidates = line.split('{', 1)[0].replace(',', ' ').split()
            elif re.match(r'^@\S+\s+host\s+', line):
                candidates = line.split()[2:]
            for token in candidates:
                token = re.sub(r':\d+$', '', re.sub(r'^https?://', '', token))
                if name := domain(token):
                    result['domains'].append(name)
            depth = max(0, depth + line.count('{') - line.count('}'))
    result = {key: sorted(set(values))[:LIMIT] for key, values in result.items()}
    return normalize(result, config.get('public_ip'))


def dual_stack_available():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(('::', 0))
        return True
    except OSError:
        return False


if __name__ == '__main__':
    import sys
    if os.geteuid() != 0:
        raise SystemExit('Address refresh requires root')
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '/etc/vps-control-relay-agent')
    value = discover(json.loads((root / 'config.json').read_text()))
    value['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fd, temporary = tempfile.mkstemp(prefix='.addresses-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        config = (root / 'config.json').stat()
        os.chown(temporary, 0, config.st_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, root / 'addresses.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
