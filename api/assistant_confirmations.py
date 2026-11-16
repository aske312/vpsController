"""The model may present an existing operation, but never approve it."""
import hashlib
import hmac
import json
import re
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

import assistant_commands as commands
import assistant_control as control
import assistant_roles as roles


class Request(BaseModel):
    model_config = {'extra': 'forbid'}
    operation_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    title: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


def authorize(api, authorization, actor):
    settings = control.read(api)
    if not authorization:
        raise HTTPException(401, 'Нужен токен запроса подтверждения', headers={'WWW-Authenticate': 'Bearer'})
    if not re.fullmatch(r'panel|[1-9][0-9]{0,15}', actor):
        raise HTTPException(403, 'Нужен собеседник Nodera')
    token = control.mcp_transport_token(settings.get('control_token', ''), 'nodera-confirmations', actor)
    if not settings.get('control_token') or not hmac.compare_digest(authorization, 'Bearer ' + token):
        raise HTTPException(401, 'Нужен токен запроса подтверждения', headers={'WWW-Authenticate': 'Bearer'})
    require(api, settings, actor)
    return settings


def require(api, settings, actor):
    if not settings['modules'].get('ai-agent'):
        raise HTTPException(403, 'ИИ выключен')
    roles.require(settings, actor, 'chat')
    control.require_installed(api, 'ai-agent')


def fingerprint(record):
    return hashlib.sha256(json.dumps({key: value for key, value in record.items()
        if key not in ('confirmation_request', 'submitted', 'completed_text')},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate(record):
    presentation = record.get('confirmation_request')
    if presentation and presentation.get('fingerprint') != fingerprint(record):
        raise HTTPException(409, 'Операция изменилась после запроса подтверждения. Подготовьте её заново')


def for_model(api, actor):
    return [{key: row[key] for key in ('operation_id', 'service', 'action', 'expires', 'confirmation_request') if key in row}
            for row in commands.pending_actions(api, actor)]


def present(api, actor, payload):
    from assistant_diagnostics import sanitize
    title = sanitize(api, payload.title.strip(), max_chars=120, max_string=120)
    reason = sanitize(api, payload.reason.strip(), max_chars=1000, max_string=1000)
    if not title.strip() or not reason.strip():
        raise HTTPException(422, 'Укажите название и причину действия')
    with control.operations.locked(api.DATA_DIR):
        settings = control._read(api)
        require(api, settings, actor)
        path, record = next(((path, row) for path, row in commands.pending_records(api, actor)
                             if row.get('operation_id') == payload.operation_id), (None, None))
        if not record or record.get('submitted') or record.get('expires', 0) <= time.time() or record.get('revision') != settings['revision']:
            raise HTTPException(409, 'Операция не ожидает подтверждения. Подготовьте действие заново')
        validate(record)
        presentation = {'title': title, 'reason': reason, 'fingerprint': fingerprint(record)}
        if record.get('confirmation_request') and record['confirmation_request'] != presentation:
            raise HTTPException(409, 'Запрос уже показан пользователю; его описание нельзя менять')
        record['confirmation_request'] = presentation
        control.operations.atomic_json(path, record)
    return {'operation_id': payload.operation_id, 'title': title, 'reason': reason,
            'service': record['service'], 'action': record['action'],
            'confirmation_required': True, 'status': 'awaiting_user',
            'message': 'Запрос показан в чате и уведомлениях. Действие выполнится только после решения пользователя.'}
