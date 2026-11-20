"""The model may present an existing operation, but never approve it."""
import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

import assistant_commands as commands
import assistant_control as control
import assistant_roles as roles


class Request(BaseModel):
    model_config = {'extra': 'forbid'}
    operation_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    operation_ids: list[Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]] | None = Field(default=None, min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def exact_operations(self):
        if (self.operation_id is None) == (self.operation_ids is None):
            raise ValueError('Укажите operation_ids или прежний operation_id')
        if self.operation_ids is not None and len(set(self.operation_ids)) != len(self.operation_ids):
            raise ValueError('Операции не должны повторяться')
        return self


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


def plans(api, actor):
    # The actor is validated before constructing any path.
    if not re.fullmatch(r'panel|[1-9][0-9]{0,15}', actor):
        raise HTTPException(403, 'Нужен собеседник Nodera')
    root = control.directory(api) / 'nodera-bot' / 'commands' / 'requests' / actor
    return [(path, control.operations.read(path)) for path in sorted(root.glob('????????????????????????????????.json'))]


def validate(record, api=None, actor=None):
    presentation = record.get('confirmation_request')
    if presentation and presentation.get('fingerprint') != fingerprint(record):
        raise HTTPException(409, 'Операция изменилась после запроса подтверждения. Подготовьте её заново')
    if api is not None:
        for _, plan in plans(api, actor):
            if record['operation_id'] in plan.get('operation_ids', []) and plan.get('expires', 0) > time.time():
                if plan.get('fingerprints', {}).get(record['operation_id']) != fingerprint(record):
                    raise HTTPException(409, 'Действие плана изменилось. Подготовьте план заново')


def annotate(api, actor, rows, settings):
    by_id = {row['operation_id']: row for row in rows}
    for _, plan in plans(api, actor):
        if plan.get('expires', 0) <= time.time() or plan.get('revision') != settings['revision']: continue
        for identity in plan.get('operation_ids', []):
            if identity not in by_id: continue
            by_id[identity]['confirmation_request'] = {key: plan[key] for key in ('request_id', 'operation_ids', 'title', 'reason')}
            by_id[identity]['expires'] = min(by_id[identity]['expires'], plan['expires'])
    return rows


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
        if payload.operation_ids is not None:
            return present_plan(api, actor, payload, title, reason, settings)
        path, record = next(((path, row) for path, row in commands.pending_records(api, actor)
                             if row.get('operation_id') == payload.operation_id), (None, None))
        if not record or record.get('submitted') or record.get('expires', 0) <= time.time() or record.get('revision') != settings['revision']:
            raise HTTPException(409, 'Операция не ожидает подтверждения. Подготовьте действие заново')
        validate(record, api, actor)
        if any(record['operation_id'] in plan.get('operation_ids', []) and plan.get('expires', 0) > time.time() for _, plan in plans(api, actor)):
            raise HTTPException(409, 'Действие уже входит в показанный пользователю план')
        presentation = {'title': title, 'reason': reason, 'fingerprint': fingerprint(record)}
        if record.get('confirmation_request') and record['confirmation_request'] != presentation:
            raise HTTPException(409, 'Запрос уже показан пользователю; его описание нельзя менять')
        record['confirmation_request'] = presentation
        control.operations.atomic_json(path, record)
    return {'operation_id': payload.operation_id, 'title': title, 'reason': reason,
            'service': record['service'], 'action': record['action'],
            'confirmation_required': True, 'status': 'awaiting_user',
            'message': 'Запрос показан в чате и уведомлениях. Действие выполнится только после решения пользователя.'}


def present_plan(api, actor, payload, title, reason, settings):
    """Called with the application lock held. One atomic file binds the whole plan."""
    records = {row['operation_id']: row for _, row in commands.pending_records(api, actor)}
    submitted = [identity for identity in payload.operation_ids if records.get(identity, {}).get('submitted')
                 and control.operations.read(api.DATA_DIR / 'operations' / (identity + '.json')).get('id') == identity]
    if submitted and all(identity in records and records[identity].get('revision') == settings['revision']
                         and (identity in submitted or records[identity].get('expires', 0) > time.time())
                         for identity in payload.operation_ids):
        for identity in payload.operation_ids:
            validate(records[identity], api, actor)
        remaining = [identity for identity in payload.operation_ids if identity not in submitted]
        return {'confirmation_required': bool(remaining), 'status': 'user_decision_received',
                'operation_ids': remaining, 'submitted_operation_ids': submitted,
                'message': 'Пользователь уже принял решение по части операций. Остальные остаются в карточке подтверждения. Повторно ничего не запускайте.'}
    selected = []
    for identity in payload.operation_ids:
        row = records.get(identity)
        if not row or row.get('submitted') or row.get('expires', 0) <= time.time() or row.get('revision') != settings['revision']:
            raise HTTPException(409, 'Не все операции плана ожидают подтверждения; запрос не создан')
        validate(row, api, actor)
        if row.get('confirmation_request'):
            raise HTTPException(409, 'Операция уже показана отдельно; её описание нельзя менять')
        selected.append(row)
    previous = plans(api, actor)
    for _, plan in previous:
        if plan.get('expires', 0) <= time.time(): continue
        if set(plan.get('operation_ids', [])) & set(payload.operation_ids):
            if plan['operation_ids'] == payload.operation_ids and plan['title'] == title and plan['reason'] == reason:
                return plan_reply(plan, selected)
            raise HTTPException(409, 'Операция уже входит в другой показанный план')
    # Only requests that still have unsubmitted members need to be retained.
    retained = 0
    for path, plan in previous:
        if plan.get('expires', 0) <= time.time() or not any(identity in records and not records[identity].get('submitted') for identity in plan.get('operation_ids', [])):
            path.unlink(missing_ok=True)
        else: retained += 1
    if retained >= 32:
        raise HTTPException(409, 'Слишком много ожидающих планов')
    plan = {'schema': 1, 'request_id': uuid.uuid4().hex, 'operation_ids': payload.operation_ids,
            'title': title, 'reason': reason, 'revision': settings['revision'],
            'expires': min(row['expires'] for row in selected),
            'fingerprints': {row['operation_id']: fingerprint(row) for row in selected}}
    root = control.directory(api) / 'nodera-bot' / 'commands' / 'requests' / actor
    control.operations.atomic_json(root / (plan['request_id'] + '.json'), plan)
    return plan_reply(plan, selected)


def plan_reply(plan, records):
    return {key: plan[key] for key in ('request_id', 'operation_ids', 'title', 'reason', 'expires')} | {
        'confirmation_required': True, 'status': 'awaiting_user',
        'actions': [{key: row[key] for key in ('operation_id', 'service', 'action')} for row in records],
        'message': 'План показан в чате и уведомлении. Действия выполняются по порядку только после решения пользователя.'}
