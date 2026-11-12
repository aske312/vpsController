"""Bounded observability API and human-confirmed MCP settings changes."""
import re
import json
from datetime import datetime, timezone
import secrets
import sqlite3
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

import observability


class EventQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    minutes: int = Field(default=60, strict=True, ge=1, le=129600)
    request_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    service: str | None = Field(default=None, pattern='^(panel|mihomo)$')
    errors: bool = False
    limit: int = Field(default=60, strict=True, ge=1, le=120)
    trace_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    operation_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    profile_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    error_code: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    stage: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    kind: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    component: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    operation_type: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')


class JournalQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    service: str = 'application'
    minutes: int = Field(default=60, strict=True, ge=1, le=10080)
    priority: str = Field(default='info', pattern='^(err|warning|info|debug)$')
    limit: int = Field(default=60, strict=True, ge=1, le=120)
    operation_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')


def evidence(api, minutes=60, request_id=None, service=None, errors=False, limit=60, trace_id=None, operation_id=None, profile_id=None, error_code=None, stage=None, kind=None, component=None, operation_type=None):
    try: EventQuery.model_validate(dict(minutes=minutes, request_id=request_id, service=service, errors=errors, limit=limit,
                                        trace_id=trace_id, operation_id=operation_id, profile_id=profile_id, error_code=error_code, stage=stage, kind=kind, component=component, operation_type=operation_type))
    except ValidationError: raise HTTPException(422, 'Некорректные фильтры событий') from None
    if type(minutes) is not int or not 1 <= minutes <= 129600 or type(limit) is not int or not 1 <= limit <= 120:
        raise HTTPException(422, 'Некорректное окно или лимит событий')
    if request_id and (not isinstance(request_id, str) or not re.fullmatch('[0-9a-f]{32}', request_id)):
        raise HTTPException(422, 'Укажите ID запроса из X-Request-ID')
    if service is not None and service not in ('panel', 'mihomo'):
        raise HTTPException(422, 'Выберите panel или mihomo')
    try:
        return observability.Store(api.DATA_DIR).read(minutes, request_id, service, errors, limit, trace_id, operation_id, profile_id, error_code=error_code, stage=stage, kind=kind, component=component, operation_type=operation_type)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, 'Журнал наблюдаемости недоступен') from None


def configure(api, payload):
    try:
        return observability.Store(api.DATA_DIR).configure(payload)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, 'Не удалось сохранить настройки наблюдаемости') from None


def connections(api):
    # Use transport evidence, never subscription deliveries or configured counts.
    import assistant_mihomo
    try:
        data = assistant_mihomo.request('GET', '/stats', timeout=15)
        if not isinstance(data, dict) or not isinstance(data.get('items'), list): raise ValueError('Invalid transport data')
        if any(not isinstance(item, dict) or not isinstance(item.get('connections'), dict) or
               any(not isinstance(row, dict) for row in item['connections'].values()) for item in data['items']):
            raise ValueError('Invalid transport rows')
        rows = [row for item in data['items'] for row in item.get('connections', {}).values()]
        counted = [row['active_connections'] for row in rows if row.get('activity_available') is not False and type(row.get('active_connections')) is int and 0 <= row['active_connections'] <= 2**53]
        return {'available': True, 'checked_at': time.time(), 'configured_channels': len(rows),
                'observed_active_channels': sum(row.get('active') is True and row.get('activity_available') is not False for row in rows),
                'established_connections': sum(counted) if len(counted) == len(rows) else None,
                'known_established_connections': sum(counted), 'channels_with_connection_count': len(counted),
                'channels_without_connection_count': len(rows) - len(counted),
                'scope': 'Mihomo server transports; active channels and handshakes are not a count of client sessions'}
    except (HTTPException, OSError, KeyError, TypeError, ValueError):
        return {'available': False, 'checked_at': time.time(), 'established_connections': None,
                'reason': 'Mihomo transport observations unavailable'}


def call(api, arguments, actor):
    import assistant_control as control
    import assistant_roles as roles
    settings = control.read(api)
    roles.require(settings, actor, 'logs_read')
    method = arguments.get('method', 'GET')
    path = arguments.get('path') or '/events'
    body = arguments.get('body') or {}
    if method == 'GET':
        if path == '/journal':
            try: query = JournalQuery.model_validate(body)
            except ValidationError: raise HTTPException(422, 'Некорректные фильтры журналов') from None
            return journal(api, **query.model_dump())
        if path == '/connections':
            roles.require(settings, actor, 'server_read')
            return connections(api)
        if path not in ('/events', '/settings'): raise HTTPException(422, 'Путь: /events, /settings, /connections')
        try: query = EventQuery.model_validate(body)
        except ValidationError: raise HTTPException(422, 'Некорректные фильтры событий') from None
        result = evidence(api, **query.model_dump())
        return result['settings'] if path == '/settings' else result
    if method != 'PATCH' or path != '/settings': raise HTTPException(422, 'Изменение: PATCH /settings')
    roles.require(settings, actor, 'server_manage')
    try: payload = observability.Settings.model_validate(body)
    except ValidationError: raise HTTPException(422, 'Укажите enabled, errors_only, retention_days, max_events и текущую revision') from None
    current = evidence(api)['settings']
    if current['revision'] != payload.revision: raise HTTPException(409, 'Настройки изменились; прочитайте /settings')
    import assistant_commands
    record = {'kind': 'observability', 'service': 'observability', 'action': 'logging-settings',
              'body': payload.model_dump(), 'code': secrets.token_hex(4), 'operation_id': uuid.uuid4().hex,
              'expires': time.time() + 120, 'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        previous = control.operations.read(path)
        if previous.get('submitted') and not previous.get('completed_text'):
            raise HTTPException(409, 'Предыдущее действие ещё выполняется')
        control.operations.atomic_json(path, record)
    return {'confirmation_required': True, 'changes': record['body'], 'message': 'Настройки ещё не изменены. Подтвердите действие в чате.'}


def confirm(api, actor, record):
    import assistant_control as control
    import assistant_roles as roles
    import assistant_commands
    settings = control.read(api)
    roles.require(settings, actor, 'logs_read')
    roles.require(settings, actor, 'server_manage')
    if settings['revision'] != record['revision']:
        raise HTTPException(409, 'Права изменились; подготовьте подтверждение заново')
    configure(api, observability.Settings.model_validate(record['body']))
    text = 'Настройки наблюдаемости сохранены.'
    with control.operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        current = control.operations.read(path)
        if current.get('operation_id') == record['operation_id']:
            current['completed_text'] = text
            control.operations.atomic_json(path, current)
            control.operations.atomic_json(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'), {
                'id': record['operation_id'], 'action': 'logging-config', 'state': 'succeeded', 'result': 'success',
                'message': text, 'started_at': control.operations.timestamp(), 'updated_at': control.operations.timestamp()})
    return text


def journal(api, service='application', minutes=60, priority='info', limit=60, operation_id=None):
    if type(minutes) is not int or not 1 <= minutes <= 10080 or type(limit) is not int or not 1 <= limit <= 120 or priority not in ('err', 'warning', 'info', 'debug'):
        raise HTTPException(422, 'Некорректный фильтр журналов')
    units = {'application': ['vps-control-api.service', 'vps-control-web.service'],
             'system': [], 'dns': ['systemd-resolved.service'], 'gateway': ['caddy.service'],
             **{key: [value['unit']] for key, value in api.managed_services().items()}}
    if not isinstance(service, str) or service not in units: raise HTTPException(422, 'Выберите application, system, dns, gateway или ID из vps_services')
    selected_units = units[service]
    operation_unit = False
    if operation_id:
        if not re.fullmatch(r'[0-9a-f]{32}', operation_id): raise HTTPException(422, 'Неверный ID операции')
        import application_operation
        record = application_operation.read(api.DATA_DIR / 'operations' / (operation_id + '.json'))
        unit = record.get('unit', '')
        if re.fullmatch(r'vps-control-(?:action-[0-9]+|cdn-security-[0-9a-f]{32})\.service', unit):
            selected_units, operation_unit = [unit], True
    args = ['journalctl', '-r', '--since', f'{minutes} minutes ago', '-p', priority, '-n', str(limit), '--no-pager', '-o', 'json',
            '--output-fields=__REALTIME_TIMESTAMP,__CURSOR,_SYSTEMD_UNIT,PRIORITY,MESSAGE']
    for unit in selected_units: args += ['-u', unit]
    output = api.run(*args, timeout=12, check=True)
    import assistant_diagnostics
    records, legacy, unparsed, budget = [], [], 0, 9000
    for index, line in enumerate(output.splitlines()[:limit]):
        if budget <= 0: break
        try:
            row = json.loads(line)
            at = int(row['__REALTIME_TIMESTAMP']) / 1000000
            if at <= 0 or not isinstance(row.get('MESSAGE'), str): raise ValueError('invalid journal record')
            message = row['MESSAGE']
            ids = {}
            for key in ('request_id', 'trace_id', 'operation_id'):
                match = re.search(r'\b' + key + r'[=:]([0-9a-f]{32})\b', message)
                if match: ids[key] = match.group(1)
            safe = assistant_diagnostics.sanitize(api, message)[:budget]
            budget -= len(safe)
            records.append({'id': 'journal:' + str(at) + ':' + str(index) + ':' + str(row.get('_SYSTEMD_UNIT', 'unknown'))[:160], 'at': at, 'source': 'journald', 'message': safe,
                            'unit': row.get('_SYSTEMD_UNIT') if re.fullmatch(r'[a-zA-Z0-9@_.-]{1,160}', str(row.get('_SYSTEMD_UNIT', ''))) else None,
                            'priority': str(row.get('PRIORITY', ''))[:1], **ids})
            legacy.append(datetime.fromtimestamp(at, timezone.utc).isoformat() + ' ' + safe)
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            unparsed += 1
            safe = assistant_diagnostics.sanitize(api, line)[:budget]
            budget -= len(safe)
            legacy.append(safe)
    return {'service': service, 'window_minutes': minutes, 'priority': priority, 'lines': legacy, 'records': records, 'available': True,
            'coverage': {'requested_from': time.time() - minutes * 60, 'requested_until': time.time(), 'limit': limit,
                         'possibly_truncated': len(output.splitlines()) >= limit or budget <= 0, 'unparsed_records': unparsed,
                         'operation_unit_selected': operation_unit,
                         'correlation': 'explicit_valid_IDs_only; other_events_are_temporal_context; rotation_and_prior_gaps_unknown'}}
