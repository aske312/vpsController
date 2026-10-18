"""Make an explicit relay copy of an existing native export without changing users."""
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import yaml
from relay_addresses import authority


def rewrite(text, route, address, origin_hosts):
    if len(text.encode('utf-8')) > 262144:
        raise ValueError('Файл слишком большой')
    hosts = {str(host).lower() for host in origin_hosts}
    changed = 0
    port = route['listen_port']

    def matches(host, number):
        return str(host).lower() in hosts and type(number) is int and number == route['target_port']

    # WireGuard/AmneziaWG. No private keys are generated, stored or replaced.
    if re.search(r'(?m)^\s*\[Interface\]\s*$', text):
        if route['transport'] != 'udp':
            raise ValueError('WireGuard/AmneziaWG требует UDP relay')
        def endpoint(match):
            nonlocal changed
            if not matches(match[2], int(match[3])):
                return match[0]
            changed += 1
            return match[1]+authority(address, port)
        result = re.sub(r'(?m)^(\s*Endpoint\s*=\s*)([^\s:]+):(\d+)\s*$', endpoint, text)
    elif re.match(r'^(vless|trojan|ss|hysteria2|hy2|tuic)://', text.strip()):
        lines = []
        for line in text.strip().splitlines():
            value = urlsplit(line.strip())
            protocol = value.scheme
            transport = 'udp' if protocol in ('hysteria2', 'hy2', 'tuic') else 'tcp'
            # SS often needs TCP and UDP together; require an explicit pair in UI.
            if protocol == 'ss':
                raise ValueError('Для Shadowsocks используйте нативный JSON с явно выбранным транспортом')
            if protocol not in ('vless', 'trojan', 'hysteria2', 'hy2', 'tuic') or route['transport'] != transport or not matches(value.hostname, value.port):
                raise ValueError('Адрес, порт или транспорт экспорта не соответствует маршруту')
            query = dict(parse_qsl(value.query, keep_blank_values=True))
            if protocol in ('trojan', 'hysteria2', 'hy2', 'tuic') or query.get('security') == 'tls':
                query.setdefault('sni', value.hostname)
            userinfo = value.netloc.rsplit('@', 1)[0]+'@' if '@' in value.netloc else ''
            lines.append(urlunsplit((value.scheme, userinfo+authority(address, port), value.path, urlencode(query), value.fragment)))
            changed += 1
        result = '\n'.join(lines)+'\n'
    else:
        try:
            value = json.loads(text)
            is_json = True
        except ValueError:
            value = yaml.safe_load(text)
            is_json = False
        if not isinstance(value, dict):
            raise ValueError('Поддерживаются WG/AWG, URI, Mihomo YAML и sing-box/Xray JSON')
        # Only native endpoint fields, never a recursive search through arbitrary data.
        nodes = value.get('proxies') if 'proxies' in value else value.get('outbounds')
        if not isinstance(nodes, list) or len(nodes) > 256:
            raise ValueError('В файле не найден список подключений')
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError('Некорректное подключение')
            kind = node.get('type') or node.get('protocol')
            if kind in ('direct', 'block', 'dns', 'selector', 'urltest', 'freedom', 'blackhole'):
                continue
            udp = kind in ('wireguard', 'hysteria2', 'tuic')
            if kind not in ('wireguard', 'hysteria2', 'tuic', 'vless', 'trojan', 'ss', 'shadowsocks'):
                raise ValueError('В экспорте есть неподдержанный протокол')
            if route['transport'] != ('udp' if udp else 'tcp'):
                raise ValueError('Экспорт содержит подключение с другим транспортом')
            if kind in ('ss', 'shadowsocks') or kind == 'vless' and node.get('packet-encoding'):
                raise ValueError('Для этого экспорта требуется отдельная проверка TCP/UDP совместимости')
            if 'server' in node:
                port_key = 'server_port' if 'server_port' in node else 'port'
                if not matches(node.get('server'), node.get(port_key)):
                    raise ValueError('Экспорт содержит другой адрес или порт; используйте файл выбранного подключения')
                old_host = node['server']
                if isinstance(node.get('tls'), dict) and node['tls'].get('enabled'):
                    node['tls'].setdefault('server_name', old_host)
                if node.get('tls') is True or kind in ('hysteria2', 'tuic', 'trojan') and 'proxies' in value:
                    node.setdefault('sni', old_host)
                    if kind in ('vless', 'trojan'):
                        node.setdefault('servername', node['sni'])
                node['server'], node[port_key] = address, port
                changed += 1
            else:
                settings = node.get('settings', {})
                entries = settings.get('vnext') if kind == 'vless' else settings.get('servers')
                if not isinstance(entries, list) or len(entries) != 1 or not matches(entries[0].get('address'), entries[0].get('port')):
                    raise ValueError('Неоднозначный адрес Xray')
                old_host = entries[0]['address']
                stream = node.get('streamSettings', {})
                if stream.get('security') == 'tls':
                    stream.setdefault('tlsSettings', {}).setdefault('serverName', old_host)
                entries[0]['address'], entries[0]['port'] = address, port
                changed += 1
        result = json.dumps(value, ensure_ascii=False, indent=2) if is_json else yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    if not changed:
        raise ValueError('В экспорте нет подключения с адресом и портом выбранного маршрута')
    return result, changed
