"""Owner-only role changes, admitted through the same human confirmation as service tools."""
import secrets
import time
import uuid
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal
import assistant_control as control
import assistant_roles as roles


class Change(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int | None = Field(default=None, strict=True, gt=0, le=2**52)
    role: Literal['owner', 'administrator', 'moderator', 'user', 'guest'] | None = None
    products: list[Literal['assistant', 'mihomo']] | None = Field(default=None, max_length=2)
    mihomo_profile_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    role_permissions: dict[str, list[str]] | None = None


def payload(settings, body):
    change = Change.model_validate(body)
    account_change = change.telegram_id is not None and any(key in change.model_fields_set for key in ('role', 'products', 'mihomo_profile_id'))
    if change.role_permissions is not None:
        if change.telegram_id is not None or any(key in change.model_fields_set for key in ('role', 'products', 'mihomo_profile_id')): raise HTTPException(422, 'Разделите настройку роли и учётной записи')
    elif not account_change: raise HTTPException(422, 'Укажите telegram_id и настройки учётной записи либо role_permissions')
    value = {key: settings[key] for key in control.Settings.model_fields if key in settings}
    value.update(expected_revision=settings['revision'], component='nodera')
    if change.role_permissions is not None:
        value['role_permissions'] = {**settings.get('role_permissions', {}), **change.role_permissions}
    else:
        accounts = [dict(row) for row in settings['accounts']]
        target = next((row for row in accounts if row['telegram_id'] == change.telegram_id), None)
        if target is None:
            if change.role is None: raise HTTPException(422, 'Укажите роль новой учётной записи')
            target = control.Account(telegram_id=change.telegram_id).model_dump()
            accounts.append(target)
        previous_role = roles.role(target)
        target['role'] = change.role or previous_role
        if change.role is not None:
            target['permission_overrides'] = {}
            if target['role'] in roles.FIXED: target['products'] = [] if target['role'] == 'guest' else ['assistant', 'mihomo']
            elif previous_role in roles.FIXED: target['products'] = ['assistant']
        if change.products is not None: target['products'] = change.products
        if 'mihomo_profile_id' in change.model_fields_set: target['mihomo_profile_id'] = change.mihomo_profile_id
        value['accounts'] = accounts
    return control.Settings.model_validate(value)


def call(api, actor, arguments):
    settings = control.read(api)
    roles.require(settings, actor, 'roles_manage')
    if arguments.get('method', 'GET') == 'GET':
        public = control.public(settings)
        return {'role_permissions': public['role_permissions'], 'permissions': list(roles.PERMISSIONS),
                'accounts': [{key: row.get(key) for key in ('telegram_id', 'name', 'role', 'products', 'mihomo_profile_id', 'permission_overrides')} for row in public['accounts']]}
    if arguments.get('method') != 'PATCH': raise HTTPException(422, 'Доступны GET и PATCH')
    body = arguments.get('body', {})
    try: payload(settings, body)
    except ValueError: raise HTTPException(422, 'Проверьте роль, ID пользователя и разрешения') from None
    import assistant_commands
    record = {'kind': 'roles', 'service': 'Роли Nodera', 'action': 'Изменить доступ', 'body': body,
              'code': secrets.token_hex(4), 'operation_id': uuid.uuid4().hex, 'expires': time.time() + 60,
              'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        previous = control.operations.read(path)
        if previous.get('submitted') and not previous.get('completed_text'):
            raise HTTPException(409, 'Сначала проверьте результат прежней команды')
        control.operations.atomic_json(path, record)
    return {'confirmation_required': True, 'changes': body, 'operation_id': record['operation_id'],
            'message': 'Владелец должен подтвердить изменение ролей кнопкой или /confirm.'}


def confirm(api, actor, record):
    settings = control.read(api)
    roles.require(settings, actor, 'roles_manage')
    if settings['revision'] != record['revision']: raise HTTPException(409, 'Права изменились; подготовьте подтверждение заново')
    control.configure(api, payload(settings, record['body']))
    import assistant_commands
    text = 'Роли и права Nodera обновлены.'
    with control.operations.locked(api.DATA_DIR):
        control.operations.atomic_json(assistant_commands.pending_path(api, actor), {**record, 'completed_text': text})
        control.operations.atomic_json(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'), {
            'id': record['operation_id'], 'action': 'nodera-roles', 'state': 'succeeded', 'result': 'success',
            'message': text, 'started_at': control.operations.timestamp(), 'updated_at': control.operations.timestamp()})
    return text
