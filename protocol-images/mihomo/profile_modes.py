"""Client capture mode and opt-in selective VPN routes, without live lookups."""
import json
from pathlib import Path

VPN_SERVICES = {entry['id']: entry for entry in json.loads(
    Path(__file__).with_name('vpn-services.json').read_text(encoding='utf-8'))['services']}


def connection_mode(routing):
    mode = routing.get('connection_mode')
    if mode in ('tun', 'proxy'):
        return mode
    return 'tun' if routing.get('tun_enabled', False) or routing.get('tun_force', False) else 'proxy'


def service_ids(value):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError('Некорректный список VPN-сервисов')
    selected = list(dict.fromkeys(part.strip() for part in value.split(',') if part.strip()))
    if any(key not in VPN_SERVICES for key in selected):
        raise ValueError('Неизвестный VPN-сервис')
    return selected


def service_rules(routing):
    if routing.get('routing_scope') != 'selected':
        return []
    rules = []
    for key in service_ids(routing.get('vpn_services', '')):
        entry = VPN_SERVICES[key]
        rules.extend(f'DOMAIN-SUFFIX,{domain},GATE.312' for domain in entry['domains'])
        rules.extend(f'DOMAIN,{host},GATE.312' for host in entry.get('hosts', []))
        rules.extend(f"{'IP-CIDR6' if ':' in network else 'IP-CIDR'},{network},GATE.312,no-resolve"
                     for network in entry['networks'])
    return list(dict.fromkeys(rules))
