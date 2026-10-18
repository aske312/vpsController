"""PRO-owned relay hosts and durable remote intents, separate from legacy DNS routes.

Never automatically replay an uncertain mutation. Reconciliation reads the exact remote ID
and commits its observed outcome. Foreign routes are visible but never adopted.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
from pathlib import Path
import re
import socket
import uuid

from application_operation import atomic_json, OperationConflict
from relay_client import RelayClient, AgentAPIError

CONFIG_KEYS = ('transport', 'listen_port', 'target_ip', 'target_port')


def public_ipv4(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global or address.is_multicast:
        raise ValueError('Нужен публичный IPv4')
    return str(address)


def validate_config(value):
    if not isinstance(value, dict) or value.get('transport') not in ('tcp', 'udp'):
        raise ValueError('Некорректный транспорт relay')
    for key, low, high in (('listen_port', 20000, 20999), ('target_port', 1, 65535)):
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise ValueError('Некорректный порт relay')
    return {**{key: value[key] for key in CONFIG_KEYS}, 'target_ip': public_ipv4(value['target_ip'])}


def route_snapshot(value):
    if not isinstance(value, dict) or type(value.get('revision')) is not int or value['revision'] < 0 or not isinstance(value.get('items'), list) or len(value['items']) > 64:
        raise ValueError('Неполный ответ маршрутов агента')
    seen, ports = set(), set()
    for item in value['items']:
        validate_config(item)
        identity = item.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', identity) or identity in seen:
            raise ValueError('Некорректный ID маршрута агента')
        port = (item['transport'], item['listen_port'])
        if port in ports or item.get('state') not in ('listening', 'failed') or item.get('target_verified') is not False:
            raise ValueError('Неизвестное состояние маршрута агента')
        for key in ('sent_bytes', 'received_bytes', 'errors', 'sessions'):
            if key in item and (type(item[key]) is not int or item[key] < 0):
                raise ValueError('Некорректные счётчики агента')
        seen.add(identity); ports.add(port)
    return value


def status_snapshot(value):
    if not isinstance(value, dict) or type(value.get('api_version')) is not int or value['api_version'] != 1 or value.get('address_family') != 'ipv4' or value.get('listen_ports') != {'min': 20000, 'max': 20999} or value.get('transports') != ['tcp', 'udp']:
        raise ValueError('Несовместимый контракт агента')
    if not isinstance(value.get('version'), str) or not re.fullmatch(r'\d+\.\d+\.\d+', value['version']):
        raise ValueError('Неизвестная версия агента')
    for key in ('revision', 'routes', 'failed_routes', 'max_routes'):
        if type(value.get(key)) is not int or value[key] < 0:
            raise ValueError('Неполный status агента')
    if value['max_routes'] != 64 or value['routes'] + value['failed_routes'] > 64:
        raise ValueError('Несовместимые лимиты агента')
    return value


def same_route(left, right):
    return all(left.get(key) == right.get(key) for key in CONFIG_KEYS)


class RelayManager:
    def __init__(self, data_dir: Path, origin: str, client_factory=RelayClient):
        self.root = Path(data_dir) / 'relay'
        self.file = self.root / 'registry.json'
        self.pending = self.root / 'pending.json'
        self.origin = origin
        self.client_factory = client_factory

    def read(self):
        if not self.file.exists():
            return {'schema': 1, 'revision': '0'*32, 'hosts': [], 'routes': []}
        value = json.loads(self.file.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('revision', ''))) or not isinstance(value.get('hosts'), list) or not isinstance(value.get('routes'), list) or len(value['hosts']) > 8:
            raise ValueError('Повреждён реестр relay')
        ids, origins = set(), set()
        for host in value['hosts']:
            self.client(host)
            if not re.fullmatch(r'[0-9a-f]{32}', str(host.get('id', ''))) or host['id'] in ids or host['agent_url'] in origins or not isinstance(host.get('name'), str):
                raise ValueError('Повреждён узел relay')
            address = host.get('client_address')
            if address is not None and (not isinstance(address, str) or not address or len(address) > 253 or any(character in address for character in '/:@\r\n ')):
                raise ValueError('Повреждён клиентский адрес Relay')
            ids.add(host['id']); origins.add(host['agent_url'])
        route_ids = set()
        for route in value['routes']:
            validate_config(route)
            if route.get('host_id') not in ids or not re.fullmatch(r'pro-[0-9a-f]{32}', str(route.get('id'))) or route['id'] in route_ids:
                raise ValueError('Повреждён маршрут relay')
            route_ids.add(route['id'])
        return value

    def write(self, registry):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        registry['revision'] = uuid.uuid4().hex
        atomic_json(self.file, registry)

    def checked(self, expected):
        value = self.read()
        if expected != value['revision']:
            raise OperationConflict('Реестр relay изменился; обновите данные и проверьте черновик')
        return value

    def client(self, host):
        return self.client_factory(host['agent_url'], host['token'], host['certificate_sha256'])

    def host(self, registry, identity):
        return next((item for item in registry['hosts'] if item['id'] == identity), None) or self.missing()

    @staticmethod
    def missing():
        raise ValueError('Узел relay не найден')

    def require_clear(self):
        if self.pending.exists():
            raise OperationConflict('Есть неподтверждённое изменение relay. Сначала проверьте его результат')

    def save_host(self, payload, *, reconnect=False):
        intent = None
        if reconnect and self.pending.exists():
            intent = json.loads(self.pending.read_text(encoding='utf-8'))
            if intent.get('route', {}).get('host_id') != payload.get('id'):
                raise OperationConflict('Восстанавливать подключение можно только к узлу незавершённой операции')
        else:
            self.require_clear()
        registry = self.checked(payload['expected_revision'])
        host_id = payload.get('id') or uuid.uuid4().hex
        previous = next((item for item in registry['hosts'] if item['id'] == host_id), {})
        host = {key: payload.get(key) for key in ('name', 'agent_url', 'certificate_sha256')}
        host.update(id=host_id, token=payload.get('token') or previous.get('token'), api_version=1)
        client = self.client(host)
        host['agent_url'] = f'https://{client.host}:9443'
        host['certificate_sha256'] = host['certificate_sha256'].lower()
        if client.host == public_ipv4(self.origin):
            raise ValueError('Узел relay должен отличаться от текущего VPS')
        address = payload.get('client_address')
        if address is None:
            address = previous.get('client_address')
        host['client_address'] = self.validate_address(address or client.host, client.host)
        if previous and previous['agent_url'] != host['agent_url']:
            raise ValueError('Адрес существующего узла неизменяем; добавьте новый узел')
        if any(item['agent_url'] == host['agent_url'] and item['id'] != host_id for item in registry['hosts']):
            raise ValueError('Этот агент уже добавлен')
        if not previous and len(registry['hosts']) >= 8:
            raise ValueError('Допустимо не более 8 relay-узлов')
        status_snapshot(client.request('GET', '/v1/status'))
        remote = route_snapshot(client.request('GET', '/v1/routes'))
        # A deliberate repin must still refer to the managed route inventory.
        for route in registry['routes']:
            if route['host_id'] != host_id:
                continue
            found = next((item for item in remote['items'] if item['id'] == route['id']), None)
            if intent and intent.get('action') == 'delete' and intent['route']['id'] == route['id'] and found is None:
                continue  # A lost delete acknowledgement may have closed this route.
            if not found or not same_route(found, route):
                raise OperationConflict('Маршруты узла не совпадают с реестром PRO; параметры не заменены')
        registry['hosts'] = [item for item in registry['hosts'] if item['id'] != host_id] + [host]
        self.write(registry)
        if intent:
            self.reconcile()

    @staticmethod
    def validate_address(value, agent_ip):
        value = str(value).strip().lower().rstrip('.')
        if value == agent_ip:
            return value
        if len(value) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
            raise ValueError('Адрес клиента должен быть IP агента или доменом этого узла без порта')
        resolved = {item[4][0] for item in socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)}
        if resolved != {agent_ip}:
            raise ValueError('Домен Relay должен разрешаться только в IPv4 выбранного агента, без CDN proxy')
        return value

    def ensure(self, host_id, groups, identity):
        """Prepare protocol-derived sockets, reusing owned rules and never replaying intent.

        Each group needs the same external port (e.g. Shadowsocks TCP + UDP).
        Unused rules are retained; their origin ports remain reserved separately.
        """
        self.require_clear()
        registry = self.read()
        host = self.host(registry, host_id)
        client = self.client(host)
        self.validate_address(host.get('client_address') or client.host, client.host)
        status_snapshot(client.request('GET', '/v1/status'))
        for group in groups:
            sockets = list(dict.fromkeys((transport, port) for transport, port in group))
            if not sockets or any(transport not in ('tcp', 'udp') or type(port) is not int or not 1 <= port <= 65535 or port in (22, 80, 8000, 9443) for transport, port in sockets):
                raise ValueError('Нельзя публиковать этот listener через Relay')
            registry = self.read()
            remote = route_snapshot(client.request('GET', '/v1/routes'))
            owned = [route for route in registry['routes'] if route['host_id'] == host_id]
            candidates = sorted({route['listen_port'] for route in owned if (route['transport'], route['target_port']) in sockets})
            candidates.extend(port for port in range(20000, 21000) if port not in candidates)
            def compatible(port):
                for transport, target in sockets:
                    found = next((route for route in remote['items'] if route['transport'] == transport and route['listen_port'] == port), None)
                    if found and not any(route['id'] == found['id'] and same_route(route, found) and route['target_ip'] == self.origin and route['target_port'] == target and found['state'] == 'listening' for route in owned):
                        return False
                return True
            selected = next((port for port in candidates if compatible(port)), None)
            if selected is None:
                raise OperationConflict('Нет свободного порта Relay для этого протокола')
            for transport, target in sockets:
                registry = self.read()
                remote = route_snapshot(client.request('GET', '/v1/routes'))
                found = next((route for route in remote['items'] if route['transport'] == transport and route['listen_port'] == selected), None)
                if found:
                    if not any(route['id'] == found['id'] and same_route(route, found) and route['target_ip'] == self.origin and route['target_port'] == target and found['state'] == 'listening' for route in registry['routes'] if route['host_id'] == host_id):
                        raise OperationConflict('Маршруты агента изменились во время настройки')
                    continue
                route_identity = uuid.uuid5(uuid.NAMESPACE_URL, f'{host_id}:{self.origin}:{transport}:{target}:{selected}').hex
                self.mutate_route({'host_id': host_id, 'transport': transport, 'target_port': target, 'listen_port': selected,
                                   'expected_revision': registry['revision'], 'agent_revision': remote['revision']}, identity, route_identity=route_identity)
        return self.read()

    def endpoint(self, host_id, transport, target_port, *, paired=False):
        """Resolve a saved binding without a network request during subscription refresh."""
        registry = self.read()
        host = self.host(registry, host_id)
        routes = [route for route in registry['routes'] if route['host_id'] == host_id and route['target_ip'] == self.origin and route['transport'] == transport and route['target_port'] == target_port]
        if paired:
            routes = [route for route in routes if any(other['host_id'] == host_id and other['target_ip'] == self.origin and other['target_port'] == target_port and other['listen_port'] == route['listen_port'] and other['transport'] != transport for other in registry['routes'])]
        if not routes:
            raise OperationConflict('Пересылка Relay для подключения не подготовлена. Сохраните настройки подключения после восстановления агента')
        route = min(routes, key=lambda route: route['listen_port'])
        return host.get('client_address') or self.client(host).host, route['listen_port']

    def delete_host(self, payload, identity):
        self.require_clear()
        registry = self.checked(payload['expected_revision'])
        host = self.host(registry, payload['host_id'])
        owned = [item for item in registry['routes'] if item['host_id'] == host['id']]
        if owned:
            client = self.client(host)
            remote = route_snapshot(client.request('GET', '/v1/routes'))
            # Check the whole cascade before deleting any known, changed rule.
            for route in owned:
                found = next((item for item in remote['items'] if item['id'] == route['id']), None)
                if found and not same_route(found, route):
                    raise OperationConflict('Конфигурация маршрута на агенте изменена; каскадное удаление остановлено')
            for route in owned:
                # Each step has its own durable intent and exact-ID readback.
                # A failed step retains the host; reconciliation never replays a DELETE.
                remote = route_snapshot(client.request('GET', '/v1/routes'))
                self.mutate_route({'host_id': host['id'], 'route_id': route['id'],
                                   'expected_revision': self.read()['revision'],
                                   'agent_revision': remote['revision']}, identity, delete=True)
        registry = self.read()
        registry['hosts'] = [item for item in registry['hosts'] if item['id'] != payload['host_id']]
        self.write(registry)

    def mutate_route(self, payload, identity, *, delete=False, route_identity=None):
        self.require_clear()
        registry = self.checked(payload['expected_revision'])
        host = self.host(registry, payload['host_id'])
        client = self.client(host)
        remote = route_snapshot(client.request('GET', '/v1/routes'))
        if remote['revision'] != payload['agent_revision']:
            raise OperationConflict('Маршруты агента изменились; обновите данные')
        if delete:
            route = next((item for item in registry['routes'] if item['id'] == payload['route_id'] and item['host_id'] == host['id']), None)
            if not route:
                raise ValueError('Управляемый маршрут не найден')
            found = next((item for item in remote['items'] if item['id'] == route['id']), None)
            if found and not same_route(found, route):
                raise OperationConflict('Конфигурация маршрута на агенте изменена; чужой маршрут не удалён')
        else:
            route = {'id': 'pro-'+(route_identity or identity), 'host_id': host['id'], **validate_config({**payload, 'target_ip': public_ipv4(self.origin)})}
            if route['target_ip'] == client.host or route['target_port'] in (22, 80, 8000, 9443):
                raise ValueError('Этот порт назначения нельзя публиковать через relay')
            if len(remote['items']) >= 64 or any(item['transport'] == route['transport'] and item['listen_port'] == route['listen_port'] for item in remote['items']):
                raise OperationConflict('Порт relay занят или достигнут лимит маршрутов')
        intent = {'schema': 1, 'operation_id': identity, 'action': 'delete' if delete else 'put', 'route': route,
                  'agent_revision': remote['revision'], 'acknowledged': False, 'rejected': False}
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_json(self.pending, intent)  # Durable before the first remote mutation.
        try:
            client.request('DELETE' if delete else 'PUT', '/v1/routes/'+route['id'],
                           None if delete else {key: route[key] for key in CONFIG_KEYS}, revision=remote['revision'])
            intent['acknowledged'] = True
        except AgentAPIError as exc:
            intent['rejected'] = exc.status in (401, 409, 422, 428, 495)
            intent['last_error'] = f'agent_http_{exc.status}'
        except Exception:
            # The response can be lost after apply; resolve by read, never replay.
            intent['last_error'] = 'response_unknown'
        atomic_json(self.pending, intent)
        return self.reconcile()

    def resume(self, operation_id):
        from relay_recovery import resume_intent
        return resume_intent(self, operation_id)

    def reconcile(self):
        if not self.pending.exists():
            return
        intent = json.loads(self.pending.read_text(encoding='utf-8'))
        if intent.get('schema') != 1 or intent.get('action') not in ('put', 'delete'):
            raise ValueError('Повреждена запись восстановления relay')
        route = intent['route']
        validate_config(route)
        if not re.fullmatch(r'pro-[0-9a-f]{32}', route.get('id', '')):
            raise ValueError('Некорректная запись восстановления relay')
        registry = self.read()
        host = self.host(registry, route['host_id'])
        remote = route_snapshot(self.client(host).request('GET', '/v1/routes'))
        found = next((item for item in remote['items'] if item['id'] == route['id']), None)
        if found and not same_route(found, route):
            raise OperationConflict('Маршрут агента не совпадает с сохранённым намерением; требуется проверка владельцем')
        applied = (found is None) if intent['action'] == 'delete' else (found is not None)
        if (not applied and type(intent.get('agent_revision')) is int
                and remote['revision'] > intent['agent_revision']):
            # The agent serializes mutations and requires the original If-Match.
            # An absent result plus an advanced revision fences a delayed request.
            intent['rejected'] = True
        if not applied and not intent.get('rejected'):
            raise OperationConflict('Результат relay пока неизвестен; сохранённое намерение оставлено для повторного чтения без отправки команды')
        if applied:
            registry['routes'] = [item for item in registry['routes'] if item['id'] != route['id']]
            if intent['action'] == 'put':
                registry['routes'].append(route)
            self.write(registry)
        self.pending.unlink()
        if not applied:
            raise OperationConflict('Чтение агента подтвердило: изменение не применено. Обновите данные перед новой командой')
        if found and found['state'] != 'listening':
            raise OperationConflict('Маршрут сохранён, но listener агента не запущен')

    def public(self, *, probe=True):
        registry = self.read()
        result = {'revision': registry['revision'], 'hosts': [], 'pending': self.pending.exists(), 'origin_ip': self.origin}
        if result['pending']:
            intent = json.loads(self.pending.read_text(encoding='utf-8'))
            route = intent['route']
            result['recovery'] = {key: route[key] for key in ('host_id', 'transport', 'listen_port', 'target_ip', 'target_port')}
            result['recovery'].update(operation_id=intent['operation_id'], action=intent['action'],
                                      error=intent.get('last_error', 'response_unknown'))

        def observe(host):
            public = {key: host[key] for key in ('id', 'name', 'agent_url', 'certificate_sha256', 'api_version')}
            public['client_address'] = host.get('client_address') or self.client(host).host
            public.update(token_configured=True, state='unknown', routes=[], foreign_routes=0)
            owned = [item for item in registry['routes'] if item['host_id'] == host['id']]
            if not probe:
                # Registry addresses are useful before agent I/O completes, but
                # carry no live state, counters or saved readiness evidence.
                public['routes'] = [{**{key: route[key] for key in ('id', 'host_id', 'transport', 'listen_port', 'target_ip', 'target_port')},
                                     'state': 'unknown', 'target_verified': False} for route in owned]
                return public
            try:
                client = self.client(host)
                status = status_snapshot(client.request('GET', '/v1/status'))
                remote = route_snapshot(client.request('GET', '/v1/routes'))
                public.update(state='connected', version=status['version'], agent_revision=remote['revision'])
                ids = {item['id'] for item in owned}
                public['foreign_routes'] = sum(item['id'] not in ids for item in remote['items'])
                for route in owned:
                    actual = next((item for item in remote['items'] if item['id'] == route['id']), None)
                    state = 'missing' if actual is None else 'drift' if not same_route(actual, route) else actual['state']
                    counters = {key: actual[key] for key in ('sent_bytes', 'received_bytes', 'errors', 'sessions') if actual and key in actual}
                    public['routes'].append({**route, **counters, 'state': state, 'target_verified': False})
            except Exception as exc:
                public['error'] = str(exc) if isinstance(exc, AgentAPIError) else 'Не удалось подтвердить состояние агента'
                public['routes'] = [{**route, 'state': 'unknown', 'target_verified': False} for route in owned]
            return public

        if probe:
            with ThreadPoolExecutor(max_workers=4) as pool:
                result['hosts'] = list(pool.map(observe, registry['hosts']))
        else:
            result['hosts'] = [observe(host) for host in registry['hosts']]
        return result
