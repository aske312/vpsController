"""Private Relay transport configuration; no flow identities or payload logging.

The outer QUIC links are independent of the inner VPN credentials. A protected
proxy has one fixed dialer and never substitutes a direct connection on failure.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
from pathlib import Path
import re
import ssl


PREFIX = '__312_transport_'
MAX_TARGETS = 128
RUNTIME_VERSION = '1.14.4'
RUNTIME_ASSETS = {
    'amd64': '699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968',
    'arm64': '436441f76ee2f7fe21b5b2d426d3cb261a480455a704865789ad533e4273ec26',
}


def public_ip(value):
    if not isinstance(value, str):
        raise ValueError('Relay transport requires a public IP')
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.version != 4:
        raise ValueError('Relay transport requires a public IPv4')
    return str(address)


def port(value, *, ingress=False, destination=False):
    if type(value) is not int or not (20000 <= value <= 20999 if ingress else (1 if destination else 1024) <= value <= 65535):
        raise ValueError('Invalid protected transport port')
    return value


def password(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', value):
        raise ValueError('Invalid protected transport key')
    return value


def targets(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TARGETS:
        raise ValueError('Protected transport requires explicit destinations')
    result = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {'ip', 'port', 'network'} or row['network'] not in ('tcp', 'udp'):
            raise ValueError('Invalid protected destination')
        endpoint = {'ip': public_ip(row['ip']), 'port': port(row['port'], destination=True), 'network': row['network']}
        if endpoint not in result:
            result.append(endpoint)
    return result


def fingerprint(certificate):
    if not isinstance(certificate, str) or len(certificate) > 16384:
        raise ValueError('Invalid transport certificate')
    try:
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
    except (ValueError, ssl.SSLError):
        raise ValueError('Invalid transport certificate') from None


def validate(profile):
    if not isinstance(profile, dict) or type(profile.get('schema')) is not int or profile.get('schema') != 1 or profile.get('transport') != 'hysteria2':
        raise ValueError('Unsupported protected transport profile')
    required = {'schema', 'transport', 'generation', 'relay_ip', 'listen_port', 'ingress_password',
                'ingress_certificate', 'ingress_private_key', 'origin_ip', 'origin_port',
                'origin_password', 'origin_certificate', 'targets', 'bandwidth_mbps'}
    if set(profile) != required or not isinstance(profile['generation'], str) or not re.fullmatch(r'[0-9a-f]{32}', profile['generation']):
        raise ValueError('Invalid protected transport profile')
    result = copy.deepcopy(profile)
    for name in ('relay_ip', 'origin_ip'):
        result[name] = public_ip(profile[name])
    if result['relay_ip'] == result['origin_ip']:
        raise ValueError('Relay and origin must be distinct')
    result['listen_port'] = port(profile['listen_port'], ingress=True)
    result['origin_port'] = port(profile['origin_port'])
    for name in ('ingress_password', 'origin_password'):
        password(profile[name])
    for name in ('ingress_certificate', 'origin_certificate'):
        fingerprint(profile[name])
    key = profile['ingress_private_key']
    if not isinstance(key, str) or not 64 <= len(key) <= 16384 or 'PRIVATE KEY-----' not in key:
        raise ValueError('Invalid transport private key')
    if type(profile['bandwidth_mbps']) is not int or not 1 <= profile['bandwidth_mbps'] <= 10000:
        raise ValueError('Invalid transport bandwidth')
    result['targets'] = targets(profile['targets'])
    if any(row['ip'] == result['origin_ip'] and row['port'] == result['origin_port'] for row in result['targets']):
        raise ValueError('Protected transport cannot dial its own listener')
    return result


def acl(allowed, inbound, outbound, *, sources=()):
    # Keep each IP/port/network tuple together; grouping them separately would
    # accidentally authorise their Cartesian product.
    rules = []
    for row in targets(allowed):
        rule = {'inbound': [inbound], 'ip_cidr': [row['ip'] + '/32'], 'port': [row['port']],
                'network': row['network'], 'action': 'route', 'outbound': outbound}
        if sources:
            rule['source_ip_cidr'] = [public_ip(value) + '/32' for value in sources]
        rules.append(rule)
    return rules + [{'action': 'reject', 'method': 'drop'}]


def render_relay(profile, directory):
    value = validate(profile)
    root = Path(directory)
    incoming = {'type': 'hysteria2', 'tag': 'edge', 'listen': value['relay_ip'],
                'listen_port': value['listen_port'], 'users': [{'password': value['ingress_password']}],
                'up_mbps': value['bandwidth_mbps'], 'down_mbps': value['bandwidth_mbps'],
                'tls': {'enabled': True, 'alpn': ['h3'], 'certificate_path': str(root/'ingress.crt'),
                        'key_path': str(root/'ingress.key')}}
    outgoing = {'type': 'hysteria2', 'tag': 'backhaul', 'server': value['origin_ip'],
                'inet4_bind_address': value['relay_ip'],
                'server_port': value['origin_port'], 'password': value['origin_password'],
                'up_mbps': value['bandwidth_mbps'], 'down_mbps': value['bandwidth_mbps'],
                'tls': {'enabled': True, 'server_name': value['origin_ip'],
                        'certificate_path': str(root/'origin.crt')}}
    return {'log': {'disabled': True}, 'inbounds': [incoming], 'outbounds': [outgoing],
            'route': {'rules': acl(value['targets'], 'edge', 'backhaul')}}


def render_origin(profile, directory):
    value = validate(profile)
    root = Path(directory)
    incoming = {'type': 'hysteria2', 'tag': 'backhaul', 'listen': value['origin_ip'],
                'listen_port': value['origin_port'], 'users': [{'password': value['origin_password']}],
                'up_mbps': value['bandwidth_mbps'], 'down_mbps': value['bandwidth_mbps'],
                'tls': {'enabled': True, 'alpn': ['h3'], 'certificate_path': str(root/'origin.crt'),
                        'key_path': str(root/'origin.key')}}
    return {'log': {'disabled': True}, 'inbounds': [incoming],
            'outbounds': [{'type': 'direct', 'tag': 'direct'}],
            'route': {'rules': acl(value['targets'], 'backhaul', 'direct', sources=[value['relay_ip']])}}


def mihomo_transport(profile):
    value = validate(profile)
    return {'name': PREFIX + value['generation'], 'type': 'hysteria2',
            'server': value['relay_ip'], 'port': value['listen_port'],
            'password': value['ingress_password'], 'fingerprint': fingerprint(value['ingress_certificate']),
            'up': str(value['bandwidth_mbps']), 'down': str(value['bandwidth_mbps']),
            'x-nodera-route': {'role': 'transport', 'hidden': True}}


def protect_node(node, profile, destination):
    value = validate(profile)
    endpoint = targets([destination])[0]
    if endpoint not in value['targets']:
        raise ValueError('Destination is outside the protected transport policy')
    if not isinstance(node, dict) or node.get('type') not in {'vless', 'vmess', 'trojan', 'ss', 'hysteria2', 'tuic', 'wireguard'}:
        raise ValueError('Unsupported protected client protocol')
    if node.get('dialer-proxy'):
        raise ValueError('Existing client dialer must be reviewed before protection')
    expected = 'udp' if node['type'] in {'wireguard', 'hysteria2', 'tuic'} else 'tcp'
    if endpoint['network'] != expected:
        raise ValueError('Protected destination transport does not match the client')
    if node['type'] == 'ss' and node.get('udp') and {**endpoint, 'network': 'udp'} not in value['targets']:
        raise ValueError('Shadowsocks UDP destination is not protected')
    result = copy.deepcopy(node)
    result['server'], result['port'] = endpoint['ip'], endpoint['port']
    result['dialer-proxy'] = PREFIX + value['generation']
    if result['type'] == 'wireguard':
        mtu = result.get('mtu', 1280)
        if type(mtu) is not int or not 576 <= mtu <= 9000:
            raise ValueError('Invalid protected WireGuard MTU')
        result['mtu'] = min(mtu, 1280)
    if result['type'] == 'tuic':
        result['disable-mtu-discovery'] = True
    return result


def protect_subscription(control, text, selections):
    """Wrap only explicitly selected Relay nodes; helpers never enter user groups."""
    import yaml
    from relay_private_transport import active
    from relay_connections import host_id
    from relay_cdn import after
    profiles = {}
    for connection in selections.values():
        selected = host_id(connection.get('settings', {}))
        if selected and selected not in profiles:
            profiles[selected] = active(control, selected)
    if not any(profiles.values()): return text
    registry = control.read()
    config = yaml.safe_load(text)
    helpers = {}
    for index, node in enumerate(config.get('proxies', [])):
        connection = selections.get(node['name'], {})
        selected = host_id(connection.get('settings', {}))
        profile = profiles.get(selected)
        if not profile: continue
        if after(connection.get('settings', {})):
            raise ValueError('Защищённый Relay требует клиентский вход на Relay перед CDN')
        network = 'udp' if node['type'] in {'wireguard', 'hysteria2', 'tuic'} else 'tcp'
        route = next((row for row in registry['routes'] if row['host_id'] == selected
                      and row['transport'] == network and row['listen_port'] == node['port']), None)
        if not route: raise ValueError('Не найден защищённый маршрут подключения')
        destination = {'ip': route['target_ip'], 'port': route['target_port'], 'network': network}
        config['proxies'][index] = protect_node(node, profile, destination)
        helper = mihomo_transport(profile);helpers[helper['name']] = helper
    if any(node['name'] in helpers for node in config['proxies'] + config.get('proxy-groups', [])):
        raise ValueError('Имя транспортного узла занято')
    config['proxies'].extend(helpers.values())
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
