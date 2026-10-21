"""Disposable live peer, with a durable expiry scheduled before any mutation."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import hashlib
from contextlib import contextmanager
from dataclasses import replace

from network_probe import ProbeUnavailable
from network_probe import ProbePlan
import relay_media
from relay_test_cleanup import atomic, cleanup


def schedule_cleanup(api, directory, ttl=1200):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = directory/'cleanup.json'
    if not journal.exists(): atomic(journal, {'expires_at': time.time() + ttl})
    durable = api.DATA_DIR/'recovery'/'relay-tests'
    durable.mkdir(parents=True, exist_ok=True, mode=0o700)
    script = durable/'cleanup.py'
    # Atomic replacement lets existing timers survive application updates/rollbacks.
    temporary = durable/'cleanup.py.new'
    temporary.write_bytes(Path(__file__).with_name('relay_test_cleanup.py').read_bytes())
    os.chmod(temporary, 0o700); temporary.replace(script)
    subprocess.run(['systemd-run', '--quiet', '--collect', '--unit=vps-relay-test-reap-' + directory.name,
                    '--on-active=' + str(ttl) + 's', sys.executable, str(script), str(journal)], check=True, capture_output=True, timeout=10)
    return journal


def reserve_peer(api, plan, connection, directory):
    module = connection['component']
    if module not in ('transport-wg', 'transport-awg'): raise ProbeUnavailable('Unsupported test peer')
    tool = 'wg' if module == 'transport-wg' else 'awg'
    interface = connection.get('_interface') or ('mh-wg0' if tool == 'wg' else 'mh-awg0')
    def command(*args, input=None):
        return subprocess.run([tool, *args], input=input, capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    port = int(command('show', interface, 'listen-port'))
    server_public = command('show', interface, 'public-key')
    if server_public != plan.client.get('public-key'):
        raise ProbeUnavailable('Live WireGuard identity differs from export')
    if connection.get('_interface'):
        if plan.listener_port != port: raise ProbeUnavailable('WireGuard listener changed')
    else:
        settings = json.loads((api.DATA_DIR/'mihomo'/'settings'/(module + '.json')).read_text())
        if settings.get('port') != port: raise ProbeUnavailable('WireGuard listener changed')
    peer_rows = command('show', interface, 'allowed-ips').splitlines()
    existing_public = {row.split()[0] for row in peer_rows if row.split()}
    networks = [ipaddress.ip_network(item, strict=False) for row in peer_rows for item in row.split()[1:] for item in item.split(',') if item != '(none)']
    addresses = json.loads(subprocess.run(['ip', '-j', 'address', 'show', 'dev', interface], capture_output=True, text=True, check=True, timeout=5).stdout)[0]['addr_info']
    local = next(item for item in addresses if item['family'] == 'inet')
    network = ipaddress.ip_network(f"{local['local']}/{local['prefixlen']}", strict=False)
    if network.num_addresses > 65536: raise ProbeUnavailable('Diagnostic peer pool too large')
    # Persistent credentials/retiring peers remain reserved even if a service temporarily lost its runtime peers.
    profiles_path = api.DATA_DIR/'mihomo'/'profiles.json'
    for profile in json.loads(profiles_path.read_text()) if profiles_path.exists() else []:
        for item in [*profile.get('connections', []), *profile.get('retiring_connections', [])]:
            if item.get('component') == module and item.get('credential', {}).get('ip'):
                networks.append(ipaddress.ip_network(str(ipaddress.ip_interface(item['credential']['ip']).ip) + '/32'))
    if connection.get('_interface'):
        for client in api.read_clients():
            if client.get('protocol') == tool and client.get('address'):
                networks.append(ipaddress.ip_network(str(ipaddress.ip_interface(client['address']).ip) + '/32'))
    available = next((ip for ip in reversed(list(network.hosts())) if str(ip) != local['local'] and not any(ip in prefix for prefix in networks)), None)
    if available is None: raise ProbeUnavailable('No free diagnostic peer address')
    private = command('genkey'); public = command('pubkey', input=private + '\n')
    if public in existing_public or len(base64.b64decode(private, validate=True)) != 32 or len(base64.b64decode(public, validate=True)) != 32:
        raise ProbeUnavailable('Invalid generated peer')
    journal = directory/'cleanup.json'
    record = json.loads(journal.read_text())
    if record.get('peer'):
        raise ProbeUnavailable('Diagnostic lease already exists; use a new test identity')
    record['peer'] = {'tool': tool, 'interface': interface, 'public_key': public, 'allowed_ip': str(available) + '/32'}
    atomic(journal, record)
    # Journal and expiry already exist. The original peer/config/profile are untouched.
    command('set', interface, 'peer', public, 'allowed-ips', str(available) + '/32')
    node = dict(plan.client)
    node.update({'private-key': private, 'ip': str(available), 'public-key': server_public})
    node.pop('pre-shared-key', None)
    node.pop('ipv6', None)
    return replace(plan, client=node, server_behavior={**plan.server_behavior, 'diagnostic_peer': True})


def base_plan(api, route, host, consumer_id):
    if consumer_id.startswith('mihomo:'):
        return relay_media.mihomo_plan(api, route, host, consumer_id, allow_disposable=True)
    client = next((item for item in api.read_clients() if 'direct:' + item.get('id', '') == consumer_id), None)
    if not client or client.get('protocol') not in ('wg', 'awg'):
        return api.prepare_relay_probe(route, host, consumer_id)
    tool = client['protocol']; interface = api.WG_INTERFACE if tool == 'wg' else api.AWG_INTERFACE
    config = api.WG_CONFIG if tool == 'wg' else api.AWG_CONFIG
    public = subprocess.run([tool, 'show', interface, 'public-key'], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    port = int(subprocess.run([tool, 'show', interface, 'listen-port'], capture_output=True, text=True, check=True, timeout=5).stdout)
    if route['transport'] != 'udp' or route['target_ip'] != api.PUBLIC_IPV4 or route['target_port'] != port:
        raise ProbeUnavailable('Direct WireGuard listener differs from Relay')
    node = {'name': 'diagnostic', 'type': 'wireguard', 'server': host.get('client_address') or api.relay_control().client(host).host,
            'port': route['listen_port'], 'ip': str(ipaddress.ip_interface(client['address']).ip), 'public-key': public,
            'private-key': '', 'allowed-ips': ['0.0.0.0/0'], 'udp': True, 'mtu': 1280}
    raw = config.read_text()
    if tool == 'awg':
        values = {}
        for line in raw.split('[Peer]', 1)[0].splitlines():
            if '=' in line:
                key, value = line.split('=', 1); values[key.strip().lower()] = value.strip()
        node['amnezia-wg-option'] = {'version': 2, **{key: int(values[key]) for key in ('jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4')}}
    material = [raw, client, route['id'], node['server'], route['listen_port'], host['certificate_sha256']]
    fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    return ProbePlan(route['id'], 1, consumer_id, node['server'], route['listen_port'], interface, port, 'udp', fingerprint,
                     node, producer='direct_disposable_' + tool, engine='mihomo', relay_host_id=host['id'], relay_route_id=route['id'])


@contextmanager
def disposable_plan(api, route, host, consumer_id, directory, *, scheduled=False):
    plan = base_plan(api, route, host, consumer_id)
    connection = None
    if plan.client.get('type') == 'wireguard':
        if consumer_id.startswith('mihomo:'):
            parts = consumer_id.split(':')
            profiles = json.loads((api.DATA_DIR/'mihomo'/'profiles.json').read_text())
            profile = next(item for item in profiles if item['id'] == parts[1])
            connection = next(item for item in profile['connections'] if item['id'] == parts[2])
        else:
            tool = plan.producer.removeprefix('direct_disposable_')
            connection = {'component': 'transport-' + tool, '_interface': plan.listener_tag}
    if connection and not scheduled: schedule_cleanup(api, directory)
    try:
        yield reserve_peer(api, plan, connection, directory) if connection else plan
    finally:
        if connection: cleanup(directory/'cleanup.json')
