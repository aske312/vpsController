"""Protocol-owned Relay sockets and client endpoint mapping; no credential changes."""
from collections import defaultdict
import copy
import json
import re
from urllib.parse import urlsplit, urlunsplit

import yaml
from relay_addresses import normalize_address, authority


UDP_COMPONENTS = {'transport-wg', 'transport-awg', 'transport-hysteria2', 'transport-tuic'}
COMPONENTS = UDP_COMPONENTS | {'transport-shadowsocks', 'transport-reality'}


def host_id(settings):
    value = settings.get('relay_host_id', '')
    if not isinstance(value, str) or value and not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Некорректный Relay узел подключения')
    if value and settings.get('relay_address'):normalize_address(settings['relay_address'])
    return value


def selected_endpoint(control, settings, transport, port, **kwargs):
    if settings.get('relay_address'):kwargs['address'] = normalize_address(settings['relay_address'])
    return control.endpoint(host_id(settings), transport, port, **kwargs)


def consumer_state(control, settings, fallback):
    """Overlay missing saved Relay bindings without modifying consumer settings."""
    selected = host_id(settings)
    if not selected:
        return fallback
    try:
        exists = any(host['id'] == selected for host in control.read()['hosts'])
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'unknown', 'reason': 'Реестр Relay недоступен', 'addresses': [], 'bindings': []}
    if not exists:
        return {'state': 'stale', 'reason': 'Relay недоступен: узел удалён', 'addresses': [], 'bindings': []}
    return fallback


def connection_groups(connection, direct_settings=None):
    component = connection['component']
    if component not in COMPONENTS:
        raise ValueError('Протокол не поддерживает Relay Agent API v1')
    credential = connection.get('credential', {})
    if component == 'transport-reality':
        mode = connection.get('settings', {}).get('route_mode') or credential.get('route_mode') or 'both'
        groups = []
        if mode in ('direct', 'both'):
            effective = credential if credential.get('direct_tag') else {**credential, **(direct_settings() if direct_settings else {})}
            groups.append([('tcp', int(effective['port']))])
        if mode in ('cdn', 'both') and credential.get('cdn_enabled') or mode == 'tls':
            groups.append([('tcp', 443)])
        return groups
    port = int(credential['port'])
    return [[('tcp', port), ('udp', port)]] if component == 'transport-shadowsocks' else [[('udp', port)]]


def prepare_mihomo(control, connections, identity, direct_settings=None, previous=()):
    by_host = defaultdict(list)
    saved = {item['id']: item for item in previous}
    for connection in connections:
        selected = host_id(connection.get('settings', {}))
        if selected:
            groups = connection_groups(connection, direct_settings)
            old = saved.get(connection.get('id'), {})
            if old.get('component') == connection['component'] and host_id(old.get('settings', {})) == selected and connection_groups(old, direct_settings) == groups:
                for group in groups:
                    for transport, port in group:
                        control.endpoint(selected, transport, port, paired=len(group) > 1)
                continue
            by_host[selected].extend(groups)
    for selected, groups in by_host.items():
        control.ensure(selected, groups, identity)


def redirect_nodes(control, connection, nodes, endpoints=()):
    selected = host_id(connection.get('settings', {}))
    if not selected:
        return
    component = connection['component']
    transport = 'udp' if component in UDP_COMPONENTS else 'tcp'
    paired = component == 'transport-shadowsocks'
    for node in nodes:
        if 'server' in node:
            key = 'server_port' if 'server_port' in node else 'port'
            original = node['server']
            address, port = selected_endpoint(control, connection.get('settings', {}), transport, int(node[key]), paired=paired)
            if isinstance(node.get('tls'), dict) and node['tls'].get('enabled'):
                node['tls'].setdefault('server_name', original)
            elif node.get('tls') is True:
                node.setdefault('servername', original)
            node['server'], node[key] = address, port
        elif node.get('protocol') in ('vless', 'shadowsocks', 'trojan'):
            entries = node.get('settings', {}).get('vnext' if node['protocol'] == 'vless' else 'servers', [])
            for entry in entries:
                entry['address'], entry['port'] = selected_endpoint(control, connection.get('settings', {}), transport, int(entry['port']), paired=paired)
    for node in endpoints:
        for peer in node.get('peers', []):
            peer['address'], peer['port'] = selected_endpoint(control, connection.get('settings', {}), 'udp', int(peer['port']))


def redirect_mihomo_lines(control, component, settings, lines):
    if not host_id(settings):
        return lines
    # Only top-level socket fields in this one producer's rendered proxy.
    port = next((int(line.split(':', 1)[1]) for line in lines if line.startswith('    port: ')), None)
    if port is None:
        raise ValueError('Порт подключения не определён')
    address, external = selected_endpoint(control, settings, 'udp' if component in UDP_COMPONENTS else 'tcp', port, paired=component == 'transport-shadowsocks')
    return [f'    server: {json.dumps(address)}' if line.startswith('    server: ') else f'    port: {external}' if line.startswith('    port: ') else line for line in lines]


def direct_groups(protocol, settings, result):
    if protocol == 'ikev2':
        raise ValueError('IKEv2 требует стандартные UDP 500/4500; диапазон Relay Agent v1 несовместим')
    texts = [item['config'] for item in result.get('profiles', [])] or [result['config']]
    groups = []
    for text in texts:
        if protocol in ('wg', 'awg'):
            match = re.search(r'(?m)^Endpoint\s*=\s*(?:\[[^\]]+\]|[^\s:]+):(\d+)', text)
            if not match:
                raise ValueError('Не найден endpoint WG/AWG')
            groups.append([('udp', int(match[1]))])
        elif protocol == 'openvpn':
            match = re.search(r'(?m)^remote\s+\S+\s+(\d+)', text)
            if not match:
                raise ValueError('Не найден endpoint OpenVPN')
            groups.append([('tcp' if re.search(r'(?m)^proto\s+tcp', text) else 'udp', int(match[1]))])
        elif protocol in ('vless-reality-xhttp', 'shadowsocks'):
            for line in text.strip().splitlines():
                port = urlsplit(line).port
                if port is None:
                    raise ValueError('Не найден порт URI подключения')
                groups.append([('tcp', port), ('udp', port)] if protocol == 'shadowsocks' and settings.get('shadowsocks_mode', 'tcp_and_udp') == 'tcp_and_udp' else [('tcp', port)])
        else:
            value = yaml.safe_load(text)
            nodes = value.get('proxies', value.get('outbounds', []))
            groups.extend([('udp' if protocol in ('hysteria2', 'tuic') else 'tcp', int(node.get('server_port', node.get('port'))))] for node in nodes if 'server' in node)
    if not groups:
        raise ValueError('Не удалось определить listener протокола')
    return groups


def direct_result(control, protocol, settings, result):
    selected = host_id(settings)
    if not selected:
        return result
    result = copy.deepcopy(result)
    def rewrite(text):
        if protocol in ('wg', 'awg'):
            def peer(match):
                address, port = selected_endpoint(control, settings, 'udp', int(match[2]))
                return match[1]+authority(address, port)
            return re.sub(r'(?m)^(Endpoint\s*=\s*)[^\s:]+:(\d+)', peer, text)
        if protocol == 'openvpn':
            transport = 'tcp' if re.search(r'(?m)^proto\s+tcp', text) else 'udp'
            def remote(match):
                address, port = selected_endpoint(control, settings, transport, int(match[1]))
                return f'remote {address} {port}'
            return re.sub(r'(?m)^remote\s+\S+\s+(\d+)', remote, text)
        if protocol in ('vless-reality-xhttp', 'shadowsocks'):
            lines = []
            for line in text.strip().splitlines():
                uri = urlsplit(line)
                address, port = selected_endpoint(control, settings, 'tcp', uri.port, paired=protocol == 'shadowsocks' and settings.get('shadowsocks_mode', 'tcp_and_udp') == 'tcp_and_udp')
                user = uri.netloc.rsplit('@', 1)[0]+'@' if '@' in uri.netloc else ''
                lines.append(urlunsplit((uri.scheme, user+authority(address, port), uri.path, uri.query, uri.fragment)))
            return '\n'.join(lines)
        value = yaml.safe_load(text)
        component = {'hysteria2': 'transport-hysteria2', 'tuic': 'transport-tuic', 'trojan': 'transport-reality'}[protocol]
        redirect_nodes(control, {'component': component, 'settings': settings}, [node for node in value.get('proxies', value.get('outbounds', [])) if 'server' in node])
        try:
            json.loads(text)
            return json.dumps(value, ensure_ascii=False, indent=2)
        except ValueError:
            return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    if result.get('profiles'):
        for profile in result['profiles']:
            profile['config'] = rewrite(profile['config'])
        result['config'] = '\n'.join(profile['config'] for profile in result['profiles'])
    else:
        result['config'] = rewrite(result['config'])
    return result
