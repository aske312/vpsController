"""Bounded video transfer through one isolated protocol client, without fallback."""
import base64
import copy
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import yaml
from network_probe import ProbePlan, ProbeUnavailable

MEDIA_URL = 'https://media.w3.org/2010/05/sintel/trailer.mp4'
MEDIA_SIZE = 4372373
MEDIA_SHA256 = 'b670602fa00934ca27c4351bb0efe7ea7a07fae57284e44226025eeed7c51254'


def mihomo_plan(api, route, host, consumer_id):
    """Use the actual device export; never synthesize weaker TLS or a direct hop."""
    parts = consumer_id.split(':')
    if len(parts) != 3 or parts[0] != 'mihomo':
        raise ProbeUnavailable('Нужно выбрать существующее подключение Mihomo')
    profiles_path = api.DATA_DIR / 'mihomo' / 'profiles.json'
    raw = profiles_path.read_bytes()
    profile = next((item for item in json.loads(raw) if item.get('id') == parts[1]), None)
    connection = next((item for item in (profile or {}).get('connections', []) if item.get('id') == parts[2]), None)
    if not connection or connection.get('settings', {}).get('relay_host_id') != host['id']:
        raise ProbeUnavailable('Подключение не относится к выбранному Relay')
    if connection.get('component') in ('transport-awg', 'transport-wg'):
        raise ProbeUnavailable('Активный WireGuard/AmneziaWG peer не используется тестом: нужен отдельный тестовый peer')
    device = connection.get('device_id', 'profile-common')
    own = [item for item in profile['connections'] if item.get('device_id', 'profile-common') == device]
    query = urllib.parse.urlencode({'format': 'mihomo', 'device_id': device})
    token = base64.b64encode(f'{api.ADMIN_USER}:{api.ADMIN_PASSWORD}'.encode()).decode()
    request = urllib.request.Request('http://127.0.0.1:8791/api/mihomo/profiles/' + urllib.parse.quote(parts[1], safe='') + '/config?' + query,
                                     headers={'Authorization': 'Basic ' + token})
    # Loopback only; environment proxy settings must not receive admin credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ProbeUnavailable('Экспорт слишком большой')
    exported = yaml.safe_load(body)
    nodes = exported.get('proxies', []) if isinstance(exported, dict) else []
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes) or len(nodes) != len(own) or profiles_path.read_bytes() != raw:
        raise ProbeUnavailable('Экспорт неоднозначен или профиль изменился')
    node = nodes[next(index for index, item in enumerate(own) if item['id'] == connection['id'])]
    transport = 'udp' if node.get('type') in ('tuic', 'hysteria2') else 'tcp'
    allowed = api.relay_control().addresses(host)
    endpoints = set(allowed['ipv4'] + allowed['ipv6'] + allowed['domains'] + [host.get('client_address', '')])
    if node.get('type') not in ('vless', 'vmess', 'trojan', 'tuic', 'hysteria2') or node.get('server') not in endpoints or node.get('port') != route['listen_port'] or transport != route['transport']:
        raise ProbeUnavailable('Экспорт не использует выбранный вход Relay; тест не будет подменять маршрут')
    material = [hashlib.sha256(raw).hexdigest(), route, host['certificate_sha256']]
    # Saved check results are not part of the tested configuration.
    material[1] = {key: route[key] for key in ('id', 'transport', 'listen_port', 'target_ip', 'target_port')}
    fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    return ProbePlan(route['id'], 1, consumer_id, node['server'], node['port'], '', route['target_port'], transport,
                     fingerprint, node, producer='mihomo_export', engine='mihomo', relay_host_id=host['id'], relay_route_id=route['id'])


def client_config(plan, port):
    node = copy.deepcopy(plan.client)
    if plan.engine == 'mihomo':
        node['name'] = 'relay-media'
        return {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False, 'mode': 'rule', 'log-level': 'silent',
                'proxies': [node], 'rules': ['MATCH,relay-media'], 'dns': {'enable': True, 'nameserver': ['https://1.1.1.1/dns-query'], 'proxy-server-nameserver': ['https://1.1.1.1/dns-query']}}
    node['tag'] = 'relay-media'
    if plan.engine == 'singbox':
        return {'log': {'disabled': True}, 'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': port}],
                'outbounds': [node], 'route': {'final': 'relay-media'}}
    return {'log': {'loglevel': 'none'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'auth': 'noauth'}}],
            'outbounds': [node], 'routing': {'rules': [{'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'relay-media'}]}}


def transfer(port, path, *, byte_range=None):
    command = ['curl', '--silent', '--show-error', '--fail', '--noproxy', '', '--proto', '=https',
               '--socks5-hostname', f'127.0.0.1:{port}', '--connect-timeout', '8', '--max-time', '25',
               '--max-filesize', str(MEDIA_SIZE), '--limit-rate', '524288', '--output', str(path)]
    if byte_range is not None:
        command += ['--range', f'{byte_range[0]}-{byte_range[1]}']
    result = subprocess.run([*command, MEDIA_URL], capture_output=True, timeout=30)
    if result.returncode:
        raise TimeoutError() if result.returncode == 28 else OSError('Media transport failed')
    return path.read_bytes()


def verify_content(body):
    return len(body) == MEDIA_SIZE and hashlib.sha256(body).hexdigest() == MEDIA_SHA256


def stop_client(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def execute(plan, binary):
    started = time.monotonic()
    process = None
    try:
        if not Path(binary).is_file():
            return {'state': 'unknown', 'reason': 'client_unavailable'}
        with ExitStack() as stack:
            folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='relay-media-'))
            root = Path(folder)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            path = root / 'client.json'
            path.write_text(json.dumps(client_config(plan, port)), encoding='utf-8')
            os.chmod(path, 0o600)
            args = ['-f', str(path)] if plan.engine == 'mihomo' else ['run', '-c' if plan.engine == 'singbox' else '-config', str(path)]
            if plan.engine == 'mihomo': args += ['-d', str(root)]
            process = subprocess.Popen([binary, *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stack.callback(stop_client, process)
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.15): break
                except OSError: time.sleep(0.05)
            else:
                return {'state': 'unknown', 'reason': 'client_unavailable'}
            body = transfer(port, root / 'video.mp4')
            if not verify_content(body):
                return {'state': 'failed', 'reason': 'content_mismatch'}
            ranges = [(0, 65535), (MEDIA_SIZE // 2, MEDIA_SIZE // 2 + 131071), (MEDIA_SIZE - 131072, MEDIA_SIZE - 1)]
            def seek(index):
                first, last = ranges[index]
                return transfer(port, root / f'range-{index}', byte_range=(first, last)) == body[first:last + 1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                if not all(pool.map(seek, range(3))):
                    return {'state': 'failed', 'reason': 'range_mismatch'}
            return {'state': 'ready', 'reason': 'media_verified', 'verified_bytes': len(body), 'range_checks': 3,
                    'seconds': round(time.monotonic() - started, 2)}
    except (TimeoutError, subprocess.TimeoutExpired):
        return {'state': 'failed', 'reason': 'timeout'}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'failed' if process else 'unknown', 'reason': 'transport_error' if process else 'client_unavailable'}
