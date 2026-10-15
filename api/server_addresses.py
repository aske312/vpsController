"""Assigned public addresses and user-selected roles; never changes OS networking."""
from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from application_operation import OperationConflict, atomic_json, locked

ROLES = {'primary', 'reserve', 'additional'}
INITIAL_REVISION = '0' * 32


def public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None
    # Python's is_global includes IPv4 multicast; those are not host endpoints.
    return str(address) if address.is_global and not address.is_multicast and not address.is_unspecified else None


def detect():
    result = subprocess.run(['ip', '-j', 'address', 'show'], capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise ValueError('Не удалось прочитать адреса сетевых интерфейсов')
    return parse_interfaces(json.loads(result.stdout))


def parse_interfaces(interfaces):
    if not isinstance(interfaces, list):
        raise ValueError('Некорректный список сетевых интерфейсов')
    found = {}
    for interface in interfaces:
        if not isinstance(interface, dict) or not isinstance(interface.get('ifname'), str) or not isinstance(interface.get('addr_info'), list):
            raise ValueError('Некорректные сведения об интерфейсе')
        # Do not publish addresses on administratively disabled interfaces.
        if 'UP' not in interface.get('flags', []):
            continue
        for entry in interface['addr_info']:
            if not isinstance(entry, dict) or not isinstance(entry.get('local'), str):
                raise ValueError('Некорректный адрес интерфейса')
            flags = entry.get('flags', [])
            if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
                raise ValueError('Некорректное состояние адреса интерфейса')
            if entry.get('scope') != 'global' or any(entry.get(flag) or flag in flags for flag in ('tentative', 'dadfailed', 'deprecated')) or entry.get('valid_life_time') == 0:
                continue
            address = public_address(entry['local'])
            if address:
                found.setdefault(address, set()).add(interface['ifname'])
    return {address: sorted(names) for address, names in found.items()}


def policy(root):
    path = Path(root) / 'server-addresses.json'
    try:
        if path.is_symlink():
            raise ValueError('Реестр адресов заменён ссылкой')
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'revision': INITIAL_REVISION, 'roles': {}, 'changes': []}
    if not isinstance(value, dict) or not isinstance(value.get('revision'), str) or len(value['revision']) != 32 or any(char not in '0123456789abcdef' for char in value['revision']):
        raise ValueError('Реестр адресов повреждён')
    roles = value.get('roles')
    if not isinstance(roles, dict) or any(public_address(address) != address or not isinstance(role, str) or role not in ROLES for address, role in roles.items()):
        raise ValueError('Назначения адресов повреждены')
    for version in (4, 6):
        if sum(role == 'primary' and ipaddress.ip_address(address).version == version for address, role in roles.items()) > 1:
            raise ValueError('Несколько основных адресов одного семейства')
    changes = value.get('changes', [])
    if not isinstance(changes, list) or len(changes) > 128 or any(not isinstance(change, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(change.get('id', ''))) or not re.fullmatch(r'[0-9a-f]{32}', str(change.get('revision', ''))) or not re.fullmatch(r'[0-9a-f]{32}', str(change.get('previous_revision', ''))) or not public_address(change.get('address')) or not isinstance(change.get('role'), str) or change['role'] not in ROLES for change in changes):
        raise ValueError('История назначений IP повреждена')
    value['changes'] = changes
    return value


def inventory(root, configured=(), *, detector=None, change_id=None):
    saved = policy(root)
    configured = list(dict.fromkeys(address for value in configured if (address := public_address(value))))
    try:
        found = (detector or detect)()
        state, message = 'ready', 'Адреса прочитаны из сетевых интерфейсов'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        found = {}
        state, message = 'error', 'Не удалось определить адреса интерфейсов. Сохранённые назначения оставлены без изменений.'
    addresses = set(found) | set(configured) | set(saved['roles'])
    roles = dict(saved['roles'])
    # Defaults preserve the installed PUBLIC_IPV4/PUBLIC_IPV6, including NAT.
    # Once explicitly saved, a missing primary never silently moves to another IP.
    if saved['revision'] == INITIAL_REVISION:
        for version in (4, 6):
            candidates = [address for address in configured if ipaddress.ip_address(address).version == version]
            if not candidates:
                candidates = sorted((address for address in found if ipaddress.ip_address(address).version == version), key=lambda address: int(ipaddress.ip_address(address)))
            if candidates:
                roles[candidates[0]] = 'primary'
    items = []
    for address in sorted(addresses, key=lambda address: (ipaddress.ip_address(address).version, int(ipaddress.ip_address(address)))):
        items.append({'address': address, 'family': 'ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6',
                      'interfaces': found.get(address, []), 'source': 'interface' if address in found else 'configured' if address in configured else 'saved',
                      'presence': 'present' if address in found else 'unknown' if state == 'error' or address in configured else 'missing',
                      'role': roles.get(address, 'additional')})
    return {'revision': saved['revision'], 'items': items, 'discovery': {'state': state, 'message': message},
            'change': next((change for change in saved['changes'] if change['id'] == change_id), None),
            'detected_at': datetime.now(timezone.utc).isoformat()}


def configure(root, address, role, expected_revision, change_id, configured=(), *, detector=None):
    address = public_address(address)
    if not address or not isinstance(role, str) or role not in ROLES or not isinstance(change_id, str) or not re.fullmatch(r'[0-9a-f]{32}', change_id):
        raise ValueError('Выберите публичный IP и допустимое назначение')
    with locked(Path(root)):
        current = inventory(root, configured, detector=detector, change_id=change_id)
        if current['change']:
            change = current['change']
            if (change['address'], change['role'], change.get('previous_revision')) != (address, role, expected_revision):
                raise OperationConflict('Идентификатор изменения уже использован для другого назначения')
            return current
        if current['revision'] != expected_revision:
            raise OperationConflict('Назначения IP изменились. Обновите таблицу и повторите выбор.')
        row = next((row for row in current['items'] if row['address'] == address), None)
        if current['discovery']['state'] != 'ready' or not row or row['presence'] != 'present':
            raise OperationConflict('Адрес не подтверждён на активном интерфейсе сервера. Обновите таблицу.')
        if row['role'] == 'primary' and role != 'primary':
            raise ValueError('Сначала назначьте другой основной IP этого семейства. Прежний станет резервным.')
        roles = {item['address']: item['role'] for item in current['items'] if item['role'] != 'additional'}
        if role == 'primary':
            for item in current['items']:
                if item['family'] == row['family'] and item['role'] == 'primary':
                    roles[item['address']] = 'reserve'
        if role == 'additional':
            roles.pop(address, None)
        else:
            roles[address] = role
        revision = uuid.uuid4().hex
        change = {'id': change_id, 'address': address, 'role': role, 'previous_revision': expected_revision, 'revision': revision}
        changes = [*policy(root)['changes'], change][-128:]
        atomic_json(Path(root) / 'server-addresses.json', {'revision': revision, 'roles': roles, 'changes': changes})
        # Return the exact observed addresses; avoid a second, inconsistent probe.
        return {**current, 'revision': revision, 'change': change, 'items': [{**item, 'role': roles.get(item['address'], 'additional')} for item in current['items']]}


def primary_for_new_config(root, fallback):
    saved = policy(root)
    primary = sorted((address for address, role in saved['roles'].items() if role == 'primary'), key=lambda address: ipaddress.ip_address(address).version)
    if not primary:
        return fallback
    selected = primary[0]
    if selected != str(fallback).strip('[]'):
        try:
            found = detect()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ValueError('Не удалось подтвердить основной IP перед созданием конфигурации') from None
        if selected not in found:
            raise ValueError('Основной IP больше не назначен активному интерфейсу. Проверьте его в Сеть → ROUTES.')
    return selected
