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


def mihomo_plan(api, route, host, consumer_id, *, allow_disposable=False):
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
    if connection.get('component') in ('transport-awg', 'transport-wg') and not allow_disposable:
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
    transport = 'udp' if node.get('type') in ('tuic', 'hysteria2', 'wireguard') else 'tcp'
    allowed = api.relay_control().addresses(host)
    endpoints = set(allowed['ipv4'] + allowed['ipv6'] + allowed['domains'] + [host.get('client_address', '')])
    types = ('vless', 'vmess', 'trojan', 'tuic', 'hysteria2', 'wireguard') if allow_disposable else ('vless', 'vmess', 'trojan', 'tuic', 'hysteria2')
    if node.get('type') not in types or node.get('server') not in endpoints or node.get('port') != route['listen_port'] or transport != route['transport']:
        raise ProbeUnavailable('Экспорт не использует выбранный вход Relay; тест не будет подменять маршрут')
    material = [hashlib.sha256(raw).hexdigest(), route, host['certificate_sha256']]
    # Saved check results are not part of the tested configuration.
    material[1] = {key: route[key] for key in ('id', 'transport', 'listen_port', 'target_ip', 'target_port')}
    fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    return ProbePlan(route['id'], 1, consumer_id, node['server'], node['port'], '', route['target_port'], transport,
                     fingerprint, node, producer='mihomo_export', engine='mihomo', relay_host_id=host['id'], relay_route_id=route['id'])


from relay_test_runner import client_config, transfer, verify_content, stop_client, execute
