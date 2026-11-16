"""Relay observations and confirmed changes through the existing operation worker."""
import json
import re
import secrets
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

import assistant_control as control
import assistant_roles as roles
from resource_country import country_override

HOST = r'^[0-9a-f]{32}$'
REVISION = r'^[0-9a-f]{32}$'


class Read(BaseModel):
    model_config = {'extra': 'forbid'}
    host_id: str | None = Field(default=None, pattern=HOST)
    view: Literal['live', 'inventory'] = 'live'
    route_offset: int = Field(default=0, strict=True, ge=0, le=64)
    route_limit: int = Field(default=10, strict=True, ge=1, le=64)


class HostChanges(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str | None = Field(default=None, min_length=1, max_length=64)
    client_address: str | None = Field(default=None, min_length=1, max_length=253)
    country_code_override: str | None = Field(default=None, max_length=2)

    @field_validator('name')
    @classmethod
    def name_value(cls, value):
        if value is not None and not value.strip(): raise ValueError('Нужно имя узла')
        return value.strip() if value is not None else None

    @field_validator('country_code_override')
    @classmethod
    def country(cls, value):
        return country_override(value) if value is not None else None


class RouteChanges(BaseModel):
    model_config = {'extra': 'forbid'}
    transport: Literal['tcp', 'udp']
    listen_port: int = Field(strict=True, ge=20000, le=20999)
    target_port: int = Field(strict=True, ge=1, le=65535)
    agent_revision: int = Field(strict=True, ge=0)


class PrivateChanges(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: bool = Field(strict=True)


class CheckChanges(BaseModel):
    model_config = {'extra': 'forbid'}
    route_id: str = Field(pattern=r'^pro-[0-9a-f]{32}$')
    consumer_id: str = Field(pattern=r'^mihomo:[a-zA-Z0-9_-]{1,64}:[a-zA-Z0-9_-]{1,64}$')
    mode: Literal['exchange', 'media'] = 'exchange'


class EmptyChanges(BaseModel):
    model_config = {'extra': 'forbid'}


class Change(BaseModel):
    model_config = {'extra': 'forbid'}
    action: Literal['configure_host', 'create_route', 'private_transport', 'check_route', 'reconnect']
    host_id: str = Field(pattern=HOST)
    expected_revision: str = Field(pattern=REVISION)
    changes: dict = Field(default_factory=dict)


MODELS = {'configure_host': HostChanges, 'create_route': RouteChanges,
          'private_transport': PrivateChanges, 'check_route': CheckChanges, 'reconnect': EmptyChanges}
ACTIONS = {'configure_host': 'relay-host-save', 'create_route': 'relay-route-create',
           'private_transport': 'relay-private-transport', 'check_route': 'relay-route-check', 'reconnect': 'relay-reconnect'}
LABELS = {'configure_host': 'Настройка узла', 'create_route': 'Создание маршрута',
          'private_transport': 'Защищённый транспорт', 'check_route': 'Проверка подключения', 'reconnect': 'Восстановление подключения'}


def require(settings, actor, *, manage=False):
    if not settings.get('mcp', {}).get('vps-controller-mcp'):
        raise HTTPException(403, 'Контур vpsController MCP выключен')
    roles.require(settings, actor, 'server_read')
    roles.require(settings, actor, 'network_read')
    if manage: roles.require(settings, actor, 'server_manage')


def parse(model, body):
    try: return model.model_validate(body)
    except ValidationError:
        raise HTTPException(422, 'Некорректные параметры Relay; используйте поля из схемы инструмента') from None


def registry(api, query):
    try:
        controller = api.relay_control()
        saved = controller.checked(query.expected_revision)
        host = next((row for row in saved['hosts'] if row['id'] == query.host_id), None)
        if host is None: raise HTTPException(404, 'Узел Relay не найден')
        changes = parse(MODELS[query.action], query.changes).model_dump(exclude_none=True)
        if query.action == 'configure_host' and not changes:
            raise HTTPException(422, 'Укажите хотя бы один параметр узла')
        if query.action == 'create_route' and {'transport': changes['transport'], 'port': changes['target_port']} not in api.relay_targets():
            raise HTTPException(422, 'Выберите подтверждённый listener из targets в relay_read')
        if query.action == 'check_route':
            if not any(row['host_id'] == query.host_id and row['id'] == changes['route_id'] for row in saved['routes']):
                raise HTTPException(404, 'Управляемый маршрут этого узла не найден')
            if not any(row['id'] == changes['consumer_id'] for row in api.network_consumers()):
                raise HTTPException(404, 'Подключение Mihomo не найдено')
        return host, changes
    except control.operations.OperationConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, ValueError):
        raise HTTPException(503, 'Реестр Relay недоступен') from None


def read(api, actor, body):
    require(control.read(api), actor)
    query = parse(Read, body)
    response = api.get_relays(None, view=query.view)
    snapshot = json.loads(response.body)
    hosts = snapshot.get('hosts', [])
    if query.host_id:
        hosts = [host for host in hosts if host['id'] == query.host_id]
        if not hosts: raise HTTPException(404, 'Узел Relay не найден')
    result = {key: snapshot[key] for key in ('revision', 'pending', 'origin_ip', 'recovery') if key in snapshot}
    result.update(hosts=[], profiles=[],
                  targets=[{key: row[key] for key in ('transport', 'port') if key in row} for row in snapshot.get('targets', [])[:80]],
                  consumers=[{key: row[key] for key in ('id', 'label') if key in row} for row in snapshot.get('consumers', [])[:80]],
                  targets_more=len(snapshot.get('targets', [])) > 80, consumers_more=len(snapshot.get('consumers', [])) > 80,
                  source='relay_observations',
                  instruction='Состояние unknown не означает исправность. Счётчики и listening не доказывают доставку клиенту. Для подробностей выбери host_id; учитывай routes_more/route_offset. Изменения требуют подтверждения человека; удаление не предоставлено.')
    host_fields = ('id', 'name', 'client_address', 'addresses', 'excluded_addresses', 'country_code_override',
                   'state', 'error', 'version', 'agent_revision', 'foreign_routes', 'capabilities', 'private_transport', 'address_checks', 'profile_failures')
    route_fields = ('id', 'host_id', 'transport', 'listen_port', 'target_ip', 'target_port', 'state',
                    'target_verified', 'sent_bytes', 'received_bytes', 'errors', 'check', 'diagnostics', 'cdn_domain')
    for host in hosts:
        row = {key: host[key] for key in host_fields if key in host}
        routes = host.get('routes', [])
        row.update(routes_total=len(routes), route_offset=query.route_offset,
                   routes=[{key: route[key] for key in route_fields if key in route}
                           for route in routes[query.route_offset:query.route_offset + query.route_limit]])
        row['routes_more'] = query.route_offset + len(row['routes']) < len(routes)
        result['hosts'].append(row)
    # These summaries contain references, not subscription credentials/configs.
    selected = {host['id'] for host in hosts}
    for profile in snapshot.get('profiles', [])[:80]:
        channels = [{key: channel[key] for key in ('id', 'label', 'transport', 'host_id', 'route_ids') if key in channel}
                    for channel in profile.get('channels', []) if channel.get('host_id') in selected]
        if channels: result['profiles'].append({'id': profile['id'], 'name': profile['name'], 'channels': channels[:20], 'channels_more': len(channels) > 20})
    result['profiles_more'] = len(snapshot.get('profiles', [])) > 80
    # Redact stored credentials even if a remote name/diagnostic echoes one.
    settings = control.read(api)
    private = api.relay_control().read()
    secret_values = [host.get('token') for host in private['hosts']]
    secret_values += [settings.get(key) for key in ('control_token', 'api_key', 'bot_token', 'telegram_api_hash')]
    secret_values += [row.get('api_key') for row in settings.get('providers', [])]
    secret_values += [row.get('session') for row in settings.get('accounts', [])]
    secret_values += [getattr(api, 'ADMIN_PASSWORD', '')]
    def scrub(value):
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if not re.search(r'(?i)password|token|secret|credential|private.?key|authorization', key)}
        if isinstance(value, list): return [scrub(item) for item in value]
        if isinstance(value, str):
            for secret in secret_values:
                if secret: value = value.replace(secret, '[redacted]')
            value = re.sub(r'(?i)(Bearer\s+|(?:password|token|secret|api[_-]?key)[\s=:]+)[^\s,;]+', r'\1[redacted]', value)
            return value[:1000]
        return value
    result = scrub(result)
    while len(json.dumps(result, ensure_ascii=False).encode()) > 28000:
        result['truncated'] = True
        if result['profiles']:
            result['profiles'].pop()
            result['profiles_more'] = True
        elif result['consumers']:
            result['consumers'].pop()
            result['consumers_more'] = True
        else:
            largest = max(result['hosts'], key=lambda host: len(host['routes']), default=None)
            if not largest or not largest['routes']:
                raise HTTPException(503, 'Слишком большой ответ Relay; выберите конкретный узел')
            largest['routes'].pop()
            largest['routes_more'] = True
    return result


def prepare(api, actor, body):
    settings = control.read(api)
    require(settings, actor, manage=True)
    query = parse(Change, body)
    host, changes = registry(api, query)
    import assistant_commands
    record = {'kind': 'relay', 'service': 'Relay ' + host['id'], 'action': LABELS[query.action],
              'body': {**query.model_dump(), 'changes': changes}, 'code': secrets.token_hex(4),
              'operation_id': uuid.uuid4().hex, 'expires': time.time() + 120,
              'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        assistant_commands.enqueue(api, actor, record)
    return {'confirmation_required': True, 'changes': record['body'], 'operation_id': record['operation_id'],
            'message': 'Подтвердите действие кнопкой в панели или /confirm в Telegram. Запрос ещё не выполнен.'}


def confirm(api, actor, record):
    settings = control.read(api)
    require(settings, actor, manage=True)
    query = parse(Change, record['body'])
    host, changes = registry(api, query)
    payload = {'host_id': query.host_id, 'expected_revision': query.expected_revision, **changes}
    if query.action in ('configure_host', 'reconnect'):
        # Existing endpoint and certificate pin cannot be changed by the model.
        payload = {key: host[key] for key in ('id', 'name', 'agent_url', 'certificate_sha256', 'api_version')}
        payload.update({'client_address': host.get('client_address'), 'country_code_override': host.get('country_code_override'),
                        'expected_revision': query.expected_revision, **changes})
        payload = api.RelayHostSettings.model_validate(payload).model_dump()
    try:
        with control.operations.short_mutation(api.DATA_DIR): pass
        result = api.start_relay_mutation(ACTIONS[query.action], payload, record['operation_id'])
    except control.operations.OperationConflict as exc:
        raise HTTPException(409, str(exc)) from None
    text = 'Операция Relay: ' + result['id'] + '\n/operation ' + result['id']
    import assistant_commands
    with control.operations.locked(api.DATA_DIR):
        target = assistant_commands.pending_path(api, actor, code=record['code'])
        current = control.operations.read(target)
        if current.get('operation_id') == record['operation_id']:
            control.operations.atomic_json(target, {**current, 'completed_text': text})
    return text
