"""Route-aware client DNS; derive domain policy from the emitted routing order."""
import ipaddress
import re
from urllib.parse import urlsplit


def resolver_parts(value, *, bootstrap=False, local=False):
    value = str(value).strip()
    if local and value == 'system':
        return 'system', '', None, ''
    if not value or len(value) > 2048 or any(c.isspace() for c in value) or '#' in value:
        raise ValueError('Укажите DNS без пробелов и параметров маршрута')
    try:
        address = ipaddress.ip_address(value)
        return 'udp', str(address), None, ''
    except ValueError:
        pass
    parsed = urlsplit(value if '://' in value else 'udp://' + value)
    if parsed.scheme not in {'udp', 'tcp', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query:
        raise ValueError('Укажите IP DNS или адрес DoH')
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Некорректный порт DNS') from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Некорректный порт DNS')
    if parsed.scheme != 'https' and parsed.path not in {'', '/'}:
        raise ValueError('Путь допустим только для DoH')
    if bootstrap:
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ValueError('Начальный DNS должен содержать IP, а не имя сервера') from None
    return parsed.scheme, parsed.hostname, port, parsed.path


def validate_route_dns(settings):
    if not settings.get('route_dns'):
        return
    resolver_parts(settings['direct_nameserver'])
    resolver_parts(settings['proxy_nameserver'])
    resolver_parts(settings['bootstrap_nameserver'], bootstrap=True)
    resolver_parts(settings['local_nameserver'], local=True)
    if settings.get('route_dns'):
        for key in ('nameserver', 'fallback'):
            if resolver_parts(settings[key])[0] != 'https':
                raise ValueError('Для DNS через VPN выберите зашифрованный DoH-резолвер')
        if settings['nameserver'] == settings['fallback']:
            raise ValueError('Основной и резервный DNS должны отличаться')


def domain_dns_routes(rules, mode='rule'):
    """Keep first-match routing, including a broad earlier personal exception.

    IP/process/conditional rules cannot classify a DNS query before resolution;
    those queries default to the tunnel. DIRECT can re-resolve at its exit.
    """
    if mode != 'rule':
        return []
    result, suffixes, exact = [], set(), set()
    direct_safe = True
    for rule in rules:
        parts = rule.removesuffix(',no-resolve').split(',')
        if parts[0] == 'MATCH':
            break
        if len(parts) != 3 or parts[0] not in {'DOMAIN', 'DOMAIN-SUFFIX'} or parts[2] not in {'DIRECT', 'GATE.312', 'REJECT'}:
            if parts[-1] == 'GATE.312':
                # An earlier process/IP/keyword condition may tunnel a name
                # otherwise covered by a later DIRECT domain rule.
                direct_safe = False
            continue
        kind, domain, target = parts
        if target == 'DIRECT' and not direct_safe:
            continue
        domain = domain.lower().rstrip('.')
        if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*', domain):
            continue
        labels = domain.split('.')
        if any('.'.join(labels[i:]) in suffixes for i in range(len(labels))):
            continue
        if kind == 'DOMAIN' and domain in exact:
            continue
        if kind == 'DOMAIN-SUFFIX':
            suffixes.add(domain)
        else:
            exact.add(domain)
        result.append((kind, domain, target))
    return result


def default_dns_target(rules, mode='rule'):
    if mode != 'rule':
        return 'DIRECT' if mode == 'direct' else 'GATE.312'
    uncertain_tunnel = False
    for rule in rules:
        parts = rule.removesuffix(',no-resolve').split(',')
        if len(parts) == 2 and parts[0] == 'MATCH':
            return 'DIRECT' if parts[1] == 'DIRECT' and not uncertain_tunnel else 'GATE.312'
        if parts[-1] == 'GATE.312' and parts[0] not in {'DOMAIN', 'DOMAIN-SUFFIX'}:
            uncertain_tunnel = True
    return 'GATE.312'


def mihomo_route_dns(settings, rules, proxy_tag, mode='rule'):
    tunnel = [f"{settings[key]}#{proxy_tag}" for key in ('nameserver', 'fallback')]
    direct = f"{settings['direct_nameserver']}#DIRECT"
    local = settings['local_nameserver']
    local = local if local == 'system' else local + '#DIRECT'
    policy = {}
    for kind, domain, target in domain_dns_routes(rules, mode):
        if target == 'DIRECT' and (domain in {'local', 'lan', 'home.arpa', 'localhost'}):
            resolvers = [local]
        else:
            resolvers = [direct] if target == 'DIRECT' else tunnel
        if kind == 'DOMAIN-SUFFIX':
            # '+.' expands into two trie entries and can overwrite an earlier
            # exact exception during Go map iteration. Emit the entries apart.
            policy.setdefault(domain, resolvers)
            policy['.' + domain] = resolvers
        else:
            policy[domain] = resolvers
    # DIRECT's dialer uses direct-nameserver even for a DNS endpoint hostname.
    # Resolve those infrastructure names through the IP bootstrap explicitly,
    # otherwise a named direct DoH/UDP resolver can recurse into itself.
    for key in ('nameserver', 'fallback', 'direct_nameserver', 'proxy_nameserver', 'local_nameserver'):
        _, host, _, _ = resolver_parts(settings[key], local=True)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host:
                policy[host] = [settings['bootstrap_nameserver'] + '#DIRECT']
    return {
        'nameserver': [direct] if default_dns_target(rules, mode) == 'DIRECT' else tunnel,
        # Both resolvers stay in the VPN pool, without a direct fallback.
        'default-nameserver': [settings['bootstrap_nameserver']],
        'proxy-server-nameserver': [settings['proxy_nameserver'] + '#DIRECT'],
        'direct-nameserver': [direct],
        'direct-nameserver-follow-policy': True,
        'nameserver-policy': policy,
    }


def singbox_resolver(address, tag, detour, bootstrap='dns-bootstrap'):
    scheme, host, port, path = resolver_parts(address, local=True)
    if scheme == 'system':
        return {'type': 'local', 'tag': tag, 'detour': detour}
    server = {'type': scheme, 'tag': tag, 'server': host, 'detour': detour}
    if port:
        server['server_port'] = port
    if scheme == 'https':
        server['path'] = path or '/dns-query'
    try:
        ipaddress.ip_address(host)
    except ValueError:
        server['domain_resolver'] = bootstrap
    return server


def singbox_route_dns(settings, rules, proxy_tag, mode='rule'):
    servers = [
        singbox_resolver(settings['bootstrap_nameserver'], 'dns-bootstrap', 'direct'),
        singbox_resolver(settings['proxy_nameserver'], 'bootstrap', 'direct'),
        singbox_resolver(settings['direct_nameserver'], 'direct-dns', 'direct'),
        singbox_resolver(settings['local_nameserver'], 'local-dns', 'direct'),
        singbox_resolver(settings['nameserver'], 'remote', proxy_tag),
        singbox_resolver(settings['fallback'], 'fallback', proxy_tag),
    ]
    dns_rules = [] if settings.get('ipv6') else [{'query_type': ['AAAA'], 'action': 'predefined', 'rcode': 'NOERROR'}]
    for index, (kind, domain, target) in enumerate(domain_dns_routes(rules, mode)):
        match = {'domain' if kind == 'DOMAIN' else 'domain_suffix': [domain]}
        if target == 'DIRECT':
            server = 'local-dns' if domain in {'local', 'lan', 'home.arpa', 'localhost'} else 'direct-dns'
            dns_rules.append({**match, 'action': 'route', 'server': server})
        else:
            # Preserve earlier tunnel exceptions before broader DIRECT domains.
            dns_rules.extend(singbox_tunnel_queries(match, f'policy-{index}'))
    if default_dns_target(rules, mode) == 'DIRECT':
        dns_rules.append({'action': 'route', 'server': 'direct-dns'})
        final = 'direct-dns'
    else:
        dns_rules.extend(singbox_tunnel_queries({}, 'default'))
        final = 'remote'
    return {'servers': servers, 'rules': dns_rules, 'final': final}


def singbox_tunnel_queries(match, tag):
    # sing-box 1.14: only a successful response or NXDOMAIN ends the query;
    # errors use the second resolver, through the same VPN outbound.
    return [
        {**match, 'action': 'evaluate', 'server': 'remote', 'tag': tag},
        {**match, 'match_response': tag, 'response_rcode': 'NOERROR', 'action': 'respond'},
        {**match, 'match_response': tag, 'response_rcode': 'NXDOMAIN', 'action': 'respond'},
        {**match, 'action': 'route', 'server': 'fallback'},
    ]


def xray_route_dns(settings, rules, node):
    endpoint_hosts = []
    for key in ('nameserver', 'fallback', 'direct_nameserver', 'proxy_nameserver', 'local_nameserver'):
        _, host, _, _ = resolver_parts(settings[key], local=True)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host:
                endpoint_hosts.append('full:' + host)
    proxy_hosts = []
    for key in ('servers', 'vnext'):
        for server in node.get('settings', {}).get(key, []):
            host = str(server.get('address', ''))
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if host:
                    proxy_hosts.append('full:' + host)

    def server(address, tag, domains=None, final=True):
        scheme, host, port, _ = resolver_parts(address, local=True)
        value = {'address': 'localhost' if scheme == 'system' else (host if scheme == 'udp' else address),
                 'tag': tag, 'finalQuery': final}
        if scheme == 'udp' and port:
            value['port'] = port
        if domains:
            value.update(domains=domains, skipFallback=True)
        return value

    servers = []
    if endpoint_hosts:
        servers.append(server(settings['bootstrap_nameserver'], 'route-dns-bootstrap', list(dict.fromkeys(endpoint_hosts))))
    if proxy_hosts:
        servers.append(server(settings['proxy_nameserver'], 'route-dns-bootstrap', list(dict.fromkeys(proxy_hosts))))
    for kind, domain, target in domain_dns_routes(rules):
        match = [('full:' if kind == 'DOMAIN' else 'domain:') + domain]
        if target == 'DIRECT':
            resolver = settings['local_nameserver'] if domain in {'local', 'lan', 'home.arpa', 'localhost'} else settings['direct_nameserver']
            servers.append(server(resolver, 'route-dns-direct', match))
        else:
            servers.append(server(settings['nameserver'], 'route-dns-vpn', match, final=False))
            servers.append(server(settings['fallback'], 'route-dns-vpn', match))
    if default_dns_target(rules) == 'DIRECT':
        servers.append(server(settings['direct_nameserver'], 'route-dns-direct'))
    else:
        servers.extend([server(settings['nameserver'], 'route-dns-vpn', final=False),
                        server(settings['fallback'], 'route-dns-vpn')])
    return {'servers': servers, 'tag': 'route-dns-vpn', 'disableFallbackIfMatch': True,
            'queryStrategy': 'UseIP' if settings.get('ipv6') else 'UseIPv4'}
