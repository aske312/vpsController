"""Versioned alert policy; MCP writes use the existing human confirmation flow."""
import secrets
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

import application_operation as operations
import assistant_control as control
import assistant_roles as roles


class Rules(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: str = Field(pattern='^[0-9a-f]{32}$')
    service_error: StrictBool = True
    local_health: StrictBool = True
    disk_pressure: StrictBool = True
    http_error_rate: StrictBool = True
    disk_percent: float = Field(default=95, ge=50, le=100)
    http_error_percent: float = Field(default=10, gt=0, le=100)
    http_min_requests: int = Field(default=20, ge=1, le=1000000)
    window_minutes: int = Field(default=5, ge=1, le=60)
    trigger_samples: int = Field(default=2, ge=1, le=60)
    recovery_samples: int = Field(default=2, ge=1, le=60)


def read(api):
    target = api.DATA_DIR / 'diagnostic-rules.json'
    if target.is_symlink(): raise ValueError('Invalid alert policy path')
    stored = operations.read(target)
    return Rules.model_validate({key: value for key, value in stored.items() if key != 'applied_operation_id'} if stored else {'revision': '0' * 32}).model_dump()


def call(api, arguments, actor):
    settings = control.read(api)
    roles.require(settings, actor, 'diagnostics')
    roles.require(settings, actor, 'logs_read')
    method = arguments.get('method', 'GET')
    if method == 'GET': return {'rules': read(api), 'scope': 'local_alert_policy', 'changes_require_human_confirmation': True}
    if method != 'PATCH': raise HTTPException(422, 'Используйте GET или PATCH')
    roles.require(settings, actor, 'server_manage')
    submitted = arguments.get('body') or {}
    if not isinstance(submitted, dict) or 'revision' not in submitted: raise HTTPException(422, 'Укажите revision правил')
    try: body = Rules.model_validate({**read(api), **submitted}).model_dump()
    except ValidationError: raise HTTPException(422, 'Укажите revision и допустимые параметры правил') from None
    import assistant_commands
    with operations.locked(api.DATA_DIR):
        if body['revision'] != read(api)['revision']: raise HTTPException(409, 'Правила изменились; прочитайте их заново')
        pending = assistant_commands.pending_path(api, actor)
        previous = operations.read(pending)
        if previous.get('submitted') and not previous.get('completed_text'): raise HTTPException(409, 'Предыдущее действие ещё выполняется')
        record = {'kind': 'diagnostic_rules', 'service': 'diagnostic_rules', 'action': 'diagnostic-rules',
                  'body': body, 'code': secrets.token_hex(4), 'operation_id': uuid.uuid4().hex,
                  'expires': time.time() + 120, 'revision': settings['revision'], 'submitted': False}
        operations.atomic_json(pending, record)
    return {'confirmation_required': True, 'changes': body, 'operation_id': record['operation_id'],
            'message': 'Правила ещё не изменены. Требуется подтверждение пользователя в панели или /confirm.'}


def confirm(api, actor, record):
    import assistant_commands
    settings = control.read(api)
    for permission in ('diagnostics', 'logs_read', 'server_manage'): roles.require(settings, actor, permission)
    if settings['revision'] != record['revision'] or not settings.get('mcp', {}).get('vps-controller-mcp'):
        raise HTTPException(409, 'Права или контур MCP изменились; подготовьте подтверждение заново')
    body = Rules.model_validate(record['body']).model_dump()
    text = 'Правила диагностических алертов сохранены.'
    with operations.locked(api.DATA_DIR):
        target = api.DATA_DIR / 'diagnostic-rules.json'
        stored = operations.read(target)
        if stored.get('applied_operation_id') != record['operation_id']:
            if read(api)['revision'] != body['revision']: raise HTTPException(409, 'Правила изменились; подготовьте изменение заново')
            operations.atomic_json(target, {**body, 'revision': uuid.uuid4().hex, 'applied_operation_id': record['operation_id']})
        operation = {'id': record['operation_id'], 'action': 'diagnostic-rules', 'state': 'succeeded',
                     'started_at': operations.timestamp(), 'updated_at': operations.timestamp(), 'message': text}
        operations.atomic_json(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'), operation)
        import operation_log
        operation_log.append(api.DATA_DIR, {**operation, 'component': 'observability', 'stage': 'configure', 'result': 'passed'})
        pending = assistant_commands.pending_path(api, actor)
        current = operations.read(pending)
        if current.get('operation_id') == record['operation_id']:
            operations.atomic_json(pending, {**current, 'completed_text': text})
    return text
