"""Per-device subscription selection, durable decisions and one-step rollback."""
from copy import deepcopy
import hashlib
import json
import time

import yaml

from application_operation import atomic_json, read


def digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def path(root, profile_id, device_id):
    identity = hashlib.sha256(json.dumps([profile_id, device_id]).encode()).hexdigest()
    return root / 'channel-selections' / (identity + '.json')


def state(record):
    if record.get('state') == 'pending' and record.get('expires_at', 0) <= time.time():
        return 'expired'
    return record.get('state', 'missing')


def active(record):
    if state(record) in {'pending', 'accepted'}:
        return record
    return record.get('previous') or {}


def filter_config(config, names):
    nodes = {node['name']: node for node in config['proxies']}
    if not names or len(names) != len(set(names)) or any(name not in nodes for name in names):
        raise ValueError('Некорректный набор проверенных каналов')
    keep = set(names)
    pending = list(names)
    while pending:
        dependency = nodes[pending.pop()].get('dialer-proxy')
        if dependency and dependency not in keep:
            if dependency not in nodes:
                raise ValueError('Неподдерживаемая зависимость транспорта')
            keep.add(dependency)
            pending.append(dependency)
    result = deepcopy(config)
    result['proxies'] = [node for node in result['proxies'] if node['name'] in keep]
    failed = set(nodes) - keep
    for group in result.get('proxy-groups', []):
        group['proxies'] = [name for name in group.get('proxies', []) if name not in failed] or list(names)
    def repair(rule):
        parts = rule.split(',')
        target = -2 if parts[-1].lower() == 'no-resolve' else -1
        if len(parts) >= 2 and parts[target] in failed:
            parts[target] = names[0]
        return ','.join(parts)
    result['rules'] = [repair(rule) for rule in result.get('rules', [])]
    for key, rules in result.get('sub-rules', {}).items():
        result['sub-rules'][key] = [repair(rule) for rule in rules]
    return result


def render(text, record):
    selected = active(record)
    if not selected:
        return text
    config = yaml.safe_load(text)
    # An administrator edit or credential rotation invalidates the overlay.
    if digest(config) != selected.get('source_digest'):
        return text
    return yaml.safe_dump(filter_config(config, selected['names']), allow_unicode=True, sort_keys=False)


def prepare(root, profile_id, device_id, payload, config, revision):
    target = path(root, profile_id, device_id)
    record = read(target)
    signature = digest(payload)
    if record.get('id') == payload['operation_id']:
        if record.get('signature') != signature:
            raise ValueError('Идентификатор команды уже использован с другими параметрами')
        return record
    if state(record) == 'pending':
        raise ValueError('Сначала примите или отмените предыдущий подготовленный профиль')
    if (record.get('previous') or {}).get('id') == payload['operation_id']:
        raise ValueError('Команда уже завершена и заменена новой')
    if payload['revision'] != revision or payload['source_digest'] != digest(config):
        raise ValueError('Профиль изменился во время проверки. Повторите подбор')
    dependencies = {node.get('dialer-proxy') for node in config['proxies']}
    names = {node['name'] for node in config['proxies']
             if not (node['name'].startswith('__312_transport_') and node['name'] in dependencies)}
    rows = payload['reports']
    if len(rows) != len(names) or {row['name'] for row in rows} != names:
        raise ValueError('Отчёт должен описывать каждый канал текущего устройства один раз')
    accepted = []
    for row in rows:
        if row['state'] != 'passed':
            continue
        if (row['seconds'] < payload['duration'] or row['transfers'] < 3
                or row['bytes'] < row['transfers'] * 262144
                or payload['require_udp'] and not row['udp']):
            raise ValueError('Канал не прошёл требуемую длительную проверку')
        accepted.append(row['name'])
    filter_config(config, accepted)
    previous = deepcopy(active(record))
    previous.pop('previous', None)
    record = {'id': payload['operation_id'], 'signature': signature, 'state': 'pending',
              'source_digest': payload['source_digest'], 'revision': revision, 'names': accepted,
              'duration': payload['duration'], 'require_udp': payload['require_udp'],
              'created_at': int(time.time()), 'expires_at': int(time.time()) + 1800,
              'previous': previous}
    atomic_json(target, record)
    return record


def decision(root, profile_id, device_id, identity, action, config):
    target = path(root, profile_id, device_id)
    record = read(target)
    if record.get('id') != identity:
        raise ValueError('Подготовленный профиль не найден или уже заменён')
    current = state(record)
    if action == 'accept':
        if current not in {'pending', 'accepted'}:
            raise ValueError('Подготовленный профиль отменён или устарел')
        if record['source_digest'] != digest(config):
            raise ValueError('Профиль изменился на сервере. Повторите подбор')
        record['state'] = 'accepted'
    elif action in {'reject', 'rollback'}:
        if current in {'pending', 'accepted'}:
            record['state'] = 'rolled_back' if action == 'rollback' or current == 'accepted' else 'rejected'
        # Repeating a terminal decision never reactivates a prior profile.
    else:
        raise ValueError('Неизвестное решение')
    atomic_json(target, record)
    return record


def response(record, base):
    selected = active(record)
    valid = not selected or selected.get('source_digest') == digest(yaml.safe_load(base))
    current = state(record)
    return {'version': 1, 'operation_id': record['id'],
            'state': 'stale' if not valid and current in {'pending', 'accepted'} else current,
            'revision': record.get('revision'), 'source_digest': record.get('source_digest'),
            'duration': record.get('duration', 60), 'require_udp': record.get('require_udp', False),
            'expires_at': record.get('expires_at'), 'config': render(base, record) if valid else base}
