"""PRO panel access is carried by authenticated Mihomo subscription channels."""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

from component_registry import ComponentRegistry
from service_state import observe_service

PANEL_HOST = 'admin.312.net'
TRANSPORTS = {
    'transport-wg': ('WireGuard', 'wg-quick@mh-wg0.service'),
    'transport-awg': ('AmneziaWG', 'awg-quick@mh-awg0.service'),
    'transport-reality': ('Xray', 'vps-control-mihomo-reality.service'),
    'transport-shadowsocks': ('Shadowsocks', 'vps-control-mihomo-ss.target'),
    'transport-hysteria2': ('Hysteria2', 'vps-control-mihomo-hysteria2.service'),
    'transport-tuic': ('TUIC', 'vps-control-mihomo-tuic.service'),
}
INTERFACES = ('mh-wg0', 'mh-awg0')
QUIC_ROOT = Path('/etc/vps-control/mihomo/quic')
_limits = None


def limits():
    global _limits
    if _limits is None:
        source = Path(__file__).resolve().parents[1] / 'protocol-images/mihomo/subscription_limits.py'
        spec = importlib.util.spec_from_file_location('panel_subscription_limits', source)
        _limits = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _limits
        spec.loader.exec_module(_limits)
    return _limits


def read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def panel_route_ready(component):
    if component not in {'transport-hysteria2', 'transport-tuic'}:
        return True
    try:
        config = read(QUIC_ROOT / component.removeprefix('transport-') / 'config.json', {})
        rules = config.get('route', {}).get('rules', [])
        return bool(rules and rules[0] == {'domain': [PANEL_HOST], 'network': 'tcp', 'port': [80],
                                          'action': 'route-options', 'override_address': '127.0.0.1', 'override_port': 80})
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def available(data_dir: Path, *, profiles=None, excluded=(), observe=None):
    """Fail closed on unknown ownership, expired quota or unavailable runtime."""
    try:
        management = ComponentRegistry(data_dir).management('mihomo')
        if management.get('state') != 'managed' or management.get('retained'):
            return []
        root = data_dir / 'mihomo'
        modules = read(root / 'state.json', {}).get('modules', {})
        profiles = read(root / 'profiles.json', []) if profiles is None else profiles
        if not isinstance(modules, dict) or not isinstance(profiles, list):
            return []
        observe = observe_service if observe is None else observe
        if observe('vps-control-mihomo-manager.service').get('active') is not True:
            return []
        candidates = set()
        for profile in profiles:
            if not isinstance(profile, dict) or not profile.get('id') or not profile.get('subscription_token'):
                continue
            if limits().status(profile, root / 'subscription-limits')['state'] != 'active':
                continue
            devices = {str(row['id']) for row in profile.get('devices', []) if isinstance(row, dict) and row.get('id')}
            for connection in profile.get('connections', []):
                if not isinstance(connection, dict):
                    continue
                component = connection.get('component')
                if (component in TRANSPORTS and component not in excluded and modules.get(component) is True
                        and str(connection.get('device_id', '')) in devices
                        and isinstance(connection.get('credential'), dict) and connection['credential']):
                    candidates.add(component)
        return sorted(component for component in candidates if observe(TRANSPORTS[component][1]).get('active') is True
                      and panel_route_ready(component))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def labels(data_dir: Path):
    return ['Подписка Mihomo · ' + TRANSPORTS[key][0] for key in available(data_dir)]


def interface_addresses():
    result = {}
    for interface in INTERFACES:
        try:
            response = subprocess.run(['ip', '-j', '-4', 'addr', 'show', 'dev', interface],
                                      capture_output=True, text=True, timeout=3, check=True)
            for row in json.loads(response.stdout):
                for value in row.get('addr_info', []):
                    address = ipaddress.ip_interface(f"{value['local']}/{value['prefixlen']}")
                    result[interface] = {'address': str(address.ip), 'subnet': str(address.network)}
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            continue
    return result


def gateway_values(values):
    """Only loopback, server hairpin traffic and isolated Mihomo tunnel ranges."""
    interfaces = interface_addresses()
    sources = ['127.0.0.0/8', '::1/128']
    for key in ('PUBLIC_IPV4', 'PUBLIC_IPV6', 'PUBLIC_IP'):
        try:
            sources.append(str(ipaddress.ip_address(values.get(key, ''))))
        except ValueError:
            pass
    sources.extend(item['subnet'] for item in interfaces.values())
    return {
        'PANEL_SUBSCRIPTION_SOURCES': ' '.join(dict.fromkeys(sources)),
        'MH_WG_PANEL_ADDRESS': interfaces.get('mh-wg0', {}).get('address', '10.82.0.1'),
        'MH_AWG_PANEL_ADDRESS': interfaces.get('mh-awg0', {}).get('address', '10.83.0.1'),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('count', 'interfaces', 'origins'))
    parser.add_argument('--data-dir', type=Path, default=Path('/var/lib/vps-control'))
    args = parser.parse_args()
    if args.action == 'count':
        print(len(available(args.data_dir)))
    elif args.action == 'interfaces':
        print('\n'.join(interface_addresses()))
    else:
        print('\n'.join('http://' + item['address'] for item in interface_addresses().values()))
