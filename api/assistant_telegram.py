"""Per-account Telegram tool policy and human approval of Telegram actions."""
import json
from pathlib import Path
import secrets
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'protocol-images/ai-agent/telegram_catalog.json').read_text(encoding='utf-8'))
TOOLS = {row['name']: row for row in CATALOG}
DEFAULT_TOOLS = ['tg_auth_status', 'tg_get_my_dialogs', 'tg_get_messages', 'tg_search_messages', 'tg_get_stats']


class PreparedAction(BaseModel):
    model_config = {'extra': 'forbid'}
    tool: str = Field(max_length=80)
    arguments: dict
    review: dict = Field(default_factory=dict)


def enabled(account):
    selected = account.get('telegram_tools')
    return list(DEFAULT_TOOLS if selected is None else selected)


def require(settings, actor, name):
    import assistant_roles as roles
    roles.require(settings, actor, 'telegram_read')
    account = next((row for row in settings['accounts'] if str(row['telegram_id']) == str(actor)), None)
    if not settings['modules'].get('ai-agent') or not settings['mcp'].get('telegram-mcp') or not account or not account.get('session'):
        raise HTTPException(403, 'Telegram MCP недоступен')
    if not isinstance(name, str) or name not in TOOLS or name not in enabled(account):
        raise HTTPException(403, 'Инструмент Telegram выключен для пользователя')
    return account


def prepare(api, actor, body):
    import assistant_control as control
    import assistant_commands
    settings = control.read(api)
    try: body = PreparedAction.model_validate(body).model_dump()
    except ValidationError: raise HTTPException(422, 'Некорректное действие Telegram') from None
    name, arguments = body['tool'], body['arguments']
    require(settings, actor, name)
    if TOOLS[name]['profile'] != 'actions' or len(json.dumps(body).encode()) > 24000:
        raise HTTPException(422, 'Некорректное действие Telegram')
    record = {'kind': 'telegram', 'service': 'Telegram', 'action': TOOLS[name]['title'], 'body': body,
              'code': secrets.token_hex(4), 'operation_id': uuid.uuid4().hex, 'expires': time.time() + 60,
              'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        previous = control.operations.read(path)
        if previous.get('submitted') and not previous.get('completed_text'):
            raise HTTPException(409, 'Сначала проверьте результат предыдущего действия')
        control.operations.atomic_json(path, record)
    return {'confirmation_required': True, 'tool': name, 'arguments': arguments,
            'operation_id': record['operation_id'], 'message': 'Подтвердите действие кнопкой в панели или /confirm в Telegram.'}


def confirm(api, actor, record):
    import assistant_control as control
    import assistant_commands
    settings = control.read(api)
    require(settings, actor, record['body']['tool'])
    if settings['revision'] != record['revision']:
        raise HTTPException(409, 'Настройки изменились; подготовьте действие заново')
    # Admission is persisted before launching the worker. An uncertain outcome
    # must never silently execute the same destructive request a second time.
    operation_path = api.DATA_DIR / 'operations' / (record['operation_id'] + '.json')
    operation = {'id': record['operation_id'], 'action': 'telegram-action', 'state': 'running',
                 'started_at': control.operations.timestamp(), 'updated_at': control.operations.timestamp()}
    with control.operations.locked(api.DATA_DIR): control.operations.atomic_json(operation_path, operation)
    try:
        result = control.runtime_request(api, '--telegram-action', {'actor': actor, 'tool': record['body']['tool'], 'arguments': record['body']['arguments']})['result']
    except Exception:
        with control.operations.locked(api.DATA_DIR):
            control.operations.atomic_json(operation_path, {**operation, 'state': 'unknown', 'result': 'unknown',
                'message': 'Результат действия Telegram не подтверждён. Проверьте чат перед повтором.', 'updated_at': control.operations.timestamp()})
        raise
    text = 'Результат действия Telegram: ' + json.dumps(result, ensure_ascii=False)
    with control.operations.locked(api.DATA_DIR):
        control.operations.atomic_json(assistant_commands.pending_path(api, actor), {**record, 'completed_text': text})
        control.operations.atomic_json(operation_path, {
            'id': record['operation_id'], 'action': 'telegram-action', 'state': 'succeeded' if result.get('success') else 'failed',
            'result': 'success' if result.get('success') else 'error', 'message': text,
            'started_at': control.operations.timestamp(), 'updated_at': control.operations.timestamp()})
    return text
