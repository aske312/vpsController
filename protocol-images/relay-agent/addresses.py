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
DOMAIN_MAX_AGE = 15 * 60
# Same supported CDN networks as PRO; https://www.cloudflare.com/ips/
CDN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22', '2400:cb00::/32',
    '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32',
    '2a06:98c0::/29', '2c0f:f248::/32'))


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Inventory source too large')
    return json.loads(path.read_text())


def fresh_checks(value):
    if not isinstance(value, dict) or value.get('eligibility_schema') != 1:
        return []
    try:
        checked = datetime.datetime.fromisoformat(value['updated_at'])
        age = (datetime.datetime.now(datetime.timezone.utc) - checked).total_seconds()
    except (ValueError, KeyError, TypeError):
        return []
    if not -60 <= age <= DOMAIN_MAX_AGE:
        return []
    checks = value.get('domain_checks')
    if not isinstance(checks, list) or len(checks) > LIMIT:
        return []
    return [{'domain': item['domain'], 'state': item['state']} for item in checks if isinstance(item, dict) and isinstance(item.get('domain'), str)
            and domain(item['domain']) == item['domain'] and item.get('state') in ('verified', 'dns_only', 'unresolved', 'mismatch')]


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
    if 'cdn_domains' in value:
        items = value['cdn_domains']
        if not isinstance(items, list) or len(items) > LIMIT or any(not isinstance(item, str) for item in items):
            raise ValueError('Invalid CDN domain list')
        result['cdn_domains'] = sorted({name for raw in items if (name := domain(raw)) and name in result['domains']})
    if primary and (address := public_ip(primary)):
        key = 'ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6'
        result[key] = [address, *[item for item in result[key] if item != address]][:LIMIT]
    return result


def read_inventory(root, config=None):
    root = Path(root)
    config = config or json.loads((root / 'config.json').read_text())
    try:
        value = read_json(root / 'addresses.json')
        result = normalize(value, config.get('public_ip'))
        checks = {item['domain']: item['state'] for item in fresh_checks(value)}
        cdn = set(result.get('cdn_domains', []))
        result['domains'] = [name for name in result['domains'] if checks.get(name) == 'verified' or name in cdn and checks.get(name) == 'dns_only']
        result['cdn_domains'] = [name for name in result['domains'] if name in cdn and checks.get(name) == 'dns_only']
        return result
    except (OSError, ValueError, TypeError):
        # No DNS receipt means no usable domain, even when explicitly configured.
        return normalize({'ipv4': [], 'domains': [], 'cdn_domains': []}, config.get('public_ip'))


def discover(config, *, addresses=None, environment_paths=None, endpoint_paths=None, caddy_root=Path('/etc/caddy'), hostname_path=Path('/etc/hostname'), hosts_path=Path('/etc/hosts')):
    if addresses is None:
        addresses = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5, check=True).stdout.split()
    # Only explicit Agent domains and transport settings are candidates. Hostname,
    # hosts aliases and arbitrary web vhosts do not establish transport intent.
    result = {'ipv4': [], 'ipv6': [], 'domains': [], 'cdn_domains': []}
    def add(raw, cdn=False):
        if isinstance(raw, str) and (name := domain(raw)):
            result['domains'].append(name)
            if cdn: result['cdn_domains'].append(name)
    for key in ('domains', 'cdn_domains'):
        values = config.get(key, [])
        if isinstance(values, list):
            for raw in values[:LIMIT]: add(raw, key == 'cdn_domains')
    for raw in addresses:
        if address := public_ip(raw):
            result['ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6'].append(address)
    data_dirs = {Path('/var/lib/vps-control')}
    retired, active = set(), set()
    for path in environment_paths if environment_paths is not None else (Path('/etc/vps-control/environment'), Path('/etc/vps-control/controller.env'), Path('/etc/vps-control.env')):
        path = Path(path)
        try:
            if path.stat().st_size > 1024 * 1024: continue
            lines = path.read_text().splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, raw = line.split('=', 1)
            key = key.strip()
            if key not in ('VLESS_CDN_DOMAIN', 'CDN_DOMAIN', 'CF_DOMAIN', 'TLS_DOMAIN', 'PUBLIC_ENDPOINT', 'DATA_DIR'):
                continue
            try: values = shlex.split(raw, comments=True)
            except ValueError: continue
            if not values: continue
            if key == 'DATA_DIR':
                if Path(values[0]).is_absolute(): data_dirs.add(Path(values[0]))
            else: add(values[0], key in ('VLESS_CDN_DOMAIN', 'CDN_DOMAIN', 'CF_DOMAIN'))
    paths = endpoint_paths if endpoint_paths is not None else [path / 'network-endpoints.json' for path in sorted(data_dirs)]
    for path in paths:
        try: settings = read_json(path)
        except (OSError, ValueError, TypeError): continue
        if not isinstance(settings, dict): continue
        if 'route_registry' in settings:
            registry = settings['route_registry']
            if not isinstance(registry, dict) or registry.get('schema') != 1 or not isinstance(registry.get('bindings'), list): continue
            for binding in registry['bindings']:
                if not isinstance(binding, dict) or binding.get('kind') not in ('cdn', 'tls_relay', 'udp_relay') or binding.get('relay_host_id'): continue
                name = domain(binding.get('address', ''))
                if not name: continue
                if binding.get('state') == 'retired': retired.add((binding['kind'], name))
                elif binding.get('state') == 'active':
                    active.add((binding['kind'], name))
                    add(name, binding['kind'] == 'cdn')
            continue
        for field in ('cdn_domain', 'tls_relay_domain', 'udp_relay_domain'):
            add(settings.get(field), field == 'cdn_domain')
            values = settings.get(field + 's', [])
            if isinstance(values, list):
                for raw in values[:LIMIT]: add(raw, field == 'cdn_domain')
    result = {key: sorted(set(values))[:LIMIT] for key, values in result.items()}
    blocked = {name for _, name in retired} - {name for _, name in active}
    result['domains'] = [name for name in result['domains'] if name not in blocked]
    result['cdn_domains'] = [name for name in result['cdn_domains'] if ('cdn', name) not in retired - active]
    return normalize(result, config.get('public_ip'))


def dual_stack_available():
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.bind(('::', 0))
        return True
    except OSError:
        return False


def verify_domains(value, resolver=None):
    """Keep local hostname discovery separate from public DNS eligibility."""
    import concurrent.futures
    def resolve(name):
        if resolver:
            return resolver(name)
        import urllib.parse
        import urllib.request
        # Public DNS, bypassing /etc/hosts and environment HTTP proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        found = []
        for kind in (1, 28):
            query = urllib.parse.urlencode({'name': name, 'type': kind})
            request = urllib.request.Request('https://1.1.1.1/dns-query?' + query, headers={'Accept': 'application/dns-json'})
            with opener.open(request, timeout=2) as response:
                data = response.read(8193)
            if len(data) > 8192: raise ValueError('DNS response too large')
            answer = json.loads(data)
            if not isinstance(answer, dict) or answer.get('Status') not in (0, 3) or not isinstance(answer.get('Answer', []), list): raise ValueError('DNS unavailable')
            found.extend(item['data'] for item in answer.get('Answer', []) if isinstance(item, dict) and item.get('type') == kind and isinstance(item.get('data'), str))
        return found
    own = set(value['ipv4'] + value['ipv6'])
    def check(name):
        try:
            found = {str(ipaddress.ip_address(item)) for item in resolve(name)}
            direct = bool(found) and found <= own
            proxy = bool(found) and any(ipaddress.ip_address(item).version == 4 for item in found) and all(
                any(ipaddress.ip_address(item) in network for network in CDN_NETWORKS if network.version == ipaddress.ip_address(item).version) for item in found)
            state = 'verified' if direct else 'dns_only' if name in value.get('cdn_domains', []) and proxy else 'mismatch' if found else 'unresolved'
        except (OSError, ValueError, subprocess.SubprocessError):
            state = 'unresolved'
        return {'domain': name, 'state': state}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        checks = list(pool.map(check, value['domains']))
    domains = [item['domain'] for item in checks if item['state'] in ('verified', 'dns_only')]
    return {**value, 'eligibility_schema': 1, 'domains': domains,
            'cdn_domains': [item['domain'] for item in checks if item['state'] == 'dns_only'], 'domain_checks': checks}


if __name__ == '__main__':
    import sys
    if os.geteuid() != 0:
        raise SystemExit('Address refresh requires root')
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '/etc/vps-control-relay-agent')
    value = verify_domains(discover(json.loads((root / 'config.json').read_text())))
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
