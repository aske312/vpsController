"""Optional assistant modules. Stored configuration is never a health claim."""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import urllib.parse
import uuid
import threading

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

import application_operation as operations

MODULES = ('nodera-bot', 'ai-agent')
MCP_MODULES = ('telegram-mcp', 'vps-controller-mcp')
BOT_UNIT = 'vps-control-nodera-bot.service'
_runtime_slots = threading.BoundedSemaphore(2)


class Account(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int = Field(strict=True, gt=0, le=2**52)
    name: str = Field(default='', max_length=80)
    bot_access: StrictBool = False
    telegram_access: StrictBool = False
    vps_access: StrictBool = False
    excluded_dialogs: list[int] = Field(default_factory=list, max_length=100)
    session: str | None = Field(default=None, max_length=4096)

    @field_validator('excluded_dialogs')
    @classmethod
    def exclusions(cls, values):
        if any(type(value) is not int or value == 0 for value in values):
            raise ValueError('Исключения должны содержать числовые ID диалогов')
        return list(dict.fromkeys(values))

    @field_validator('session')
    @classmethod
    def session_value(cls, value):
        if value is not None and value and not re.fullmatch(r'1[A-Za-z0-9_=-]{100,4095}', value):
            raise ValueError('Нужна строка Telethon StringSession, а не файл или пароль')
        return value


class Settings(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: str = Field(pattern=r'^[0-9a-f]{32}$')
    modules: dict[str, StrictBool]
    mcp: dict[str, StrictBool]
    bot_token: str | None = Field(default=None, max_length=256)
    provider_url: str = Field(default='', max_length=512)
    model: str = Field(default='', max_length=128)
    api_key: str | None = Field(default=None, max_length=2048)
    telegram_api_id: int | None = Field(default=None, strict=True, gt=0)
    telegram_api_hash: str | None = Field(default=None, max_length=32)
    accounts: list[Account] = Field(default_factory=list, max_length=30)

    @field_validator('modules')
    @classmethod
    def module_names(cls, value):
        if set(value) != set(MODULES):
            raise ValueError('Укажите состояние NoderaBot и AI-agent')
        return value

    @field_validator('mcp')
    @classmethod
    def mcp_names(cls, value):
        if set(value) != set(MCP_MODULES):
            raise ValueError('Укажите состояние двух MCP-подключений агента')
        return value

    @field_validator('bot_token')
    @classmethod
    def token_value(cls, value):
        if value and not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,200}', value):
            raise ValueError('Некорректный токен Telegram-бота')
        return value

    @field_validator('telegram_api_hash')
    @classmethod
    def hash_value(cls, value):
        if value and not re.fullmatch(r'[0-9a-fA-F]{32}', value):
            raise ValueError('Telegram API hash должен содержать 32 шестнадцатеричных символа')
        return value

    @field_validator('provider_url')
    @classmethod
    def provider(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return value
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Нужен HTTPS base URL провайдера без пароля, query и fragment')
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                raise ValueError('Внутренний адрес провайдера недоступен')
        except ValueError as exc:
            if 'Внутренний' in str(exc):
                raise
            if parsed.hostname.lower() == 'localhost' or parsed.hostname.endswith('.localhost'):
                raise ValueError('Внутренний адрес провайдера недоступен') from None
        if parsed.port not in (None, 443):
            raise ValueError('Для провайдера поддерживается HTTPS на порту 443')
        return value

    @model_validator(mode='after')
    def unique_accounts(self):
        ids = [a.telegram_id for a in self.accounts]
        if len(set(ids)) != len(ids):
            raise ValueError('Telegram ID пользователей не должны повторяться')
        return self


class Chat(BaseModel):
    model_config = {'extra': 'forbid'}
    prompt: str = Field(min_length=1, max_length=12000)
    telegram_id: int | None = Field(default=None, strict=True, gt=0)


class Check(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int | None = Field(default=None, strict=True, gt=0)


class ToolCall(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(pattern=r'^vps_(overview|services|components)$')


class Action(BaseModel):
    model_config = {'extra': 'forbid'}
    action: str = Field(pattern=r'^(install|start|stop|restart)$')


def directory(api):
    return api.DATA_DIR / 'assistant'


def runtime_directory():
    return Path(os.getenv('ASSISTANT_RUNTIME_DIR', '/var/lib/vps-control-assistant-runtime'))


def runtime_ready():
    root = runtime_directory()
    requirements = Path(__file__).with_name('assistant_runtime') / 'requirements.txt'
    return (root / 'venv/bin/python').is_file() and (root / 'requirements.txt').is_file() and (root / 'requirements.txt').read_bytes() == requirements.read_bytes()


def read(api):
    value = operations.read(directory(api) / 'settings.json')
    if not value:
        return {'revision': '0' * 32, 'modules': {key: False for key in MODULES},
                'mcp': {key: False for key in MCP_MODULES},
                'provider_url': '', 'model': '', 'accounts': [], 'telegram_api_id': None}
    try:
        Settings.model_validate({key: item for key, item in value.items() if key not in ('revision', 'control_token')} | {'expected_revision': value['revision']})
    except (ValueError, KeyError):
        raise operations.OperationConflict('Настройки MCP повреждены; автоматический сброс не выполняется') from None
    return value


def public(value):
    hidden = {'api_key', 'bot_token', 'telegram_api_hash', 'control_token', 'session'}
    result = {key: item for key, item in value.items() if key not in hidden}
    for key in ('api_key', 'bot_token', 'telegram_api_hash'):
        result[key + '_configured'] = bool(value.get(key))
    result['accounts'] = [{key: item for key, item in account.items() if key != 'session'} |
                          {'session_configured': bool(account.get('session'))} for account in value['accounts']]
    return result


def ready(value, module):
    if module == 'ai-agent':
        return bool(value.get('provider_url') and value.get('model') and value.get('api_key'))
    if module == 'nodera-bot':
        return bool(value.get('bot_token') and any(a.get('bot_access') for a in value['accounts']))
    if module == 'telegram-mcp':
        return bool(value.get('telegram_api_id') and value.get('telegram_api_hash') and
                    any(a.get('session') and a['telegram_access'] for a in value['accounts']))
    return True


def status(api):
    value = read(api)
    runtime = runtime_ready()
    modules = []
    for key, name in zip(MODULES, ('NoderaBot', 'AI-agent')):
        modules.append({'id': key, 'name': name, 'enabled': value['modules'][key],
                        'configured': ready(value, key), 'execution': 'service' if key == 'nodera-bot' else 'on-demand'})
    return {'settings': public(value), 'modules': modules, 'runtime_installed': runtime,
            'mcp_modules': [{'id': key, 'name': name, 'enabled': value['mcp'][key], 'configured': ready(value, key),
                             'execution': 'on-demand'} for key, name in zip(MCP_MODULES, ('Telegram MCP', 'vpsController MCP'))],
            'bot': api.observe_service(BOT_UNIT),
            'legacy_bot': api.observe_service('tg-agent-gateway.service'),
            'tools': {'telegram-mcp': ['tg_auth_status', 'tg_dialogs', 'tg_history', 'tg_search'],
                      'vps-controller-mcp': ['vps_overview', 'vps_services', 'vps_components']}}


def configure(api, payload):
    bot = api.observe_service(BOT_UNIT)
    if bot.get('active'):
        raise HTTPException(409, 'Остановите NoderaBot перед изменением настроек; затем запустите его с новой конфигурацией')
    if bot.get('unit_present') is not False and bot.get('state') not in ('inactive', 'failed'):
        raise HTTPException(409, 'Состояние NoderaBot не подтверждено. Обновите раздел перед изменением настроек')
    with operations.locked(api.DATA_DIR):
        old = read(api)
        if old['revision'] != payload.expected_revision:
            raise operations.OperationConflict('Настройки MCP изменились. Обновите раздел перед сохранением')
        value = payload.model_dump(exclude={'expected_revision'})
        for key in ('api_key', 'bot_token', 'telegram_api_hash'):
            if value[key] is None:
                value[key] = old.get(key, '')
        previous = {a['telegram_id']: a for a in old['accounts']}
        for account in value['accounts']:
            if account['session'] is None:
                account['session'] = previous.get(account['telegram_id'], {}).get('session', '')
        value['revision'] = uuid.uuid4().hex
        value['control_token'] = old.get('control_token') or secrets.token_urlsafe(48)
        operations.atomic_json(directory(api) / 'settings.json', value)
    return status(api)


def runtime_request(api, mode, body):
    python = runtime_directory() / 'venv' / 'bin' / 'python'
    if not runtime_ready():
        raise HTTPException(409, 'Сначала установите среду выполнения модулей MCP')
    value = read(api)
    command = [str(python), str(Path(__file__).with_name('assistant_runtime') / 'runner.py'), mode]
    # The worker never inherits panel passwords, cookies or unrelated service credentials.
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT', 'SSL_CERT_FILE') if key in os.environ}
    if not _runtime_slots.acquire(blocking=False):
        raise HTTPException(429, 'Два запроса к модулям уже выполняются; дождитесь завершения')
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, env=env, start_new_session=os.name == 'posix') as process:
            try:
                stdout, _ = process.communicate(json.dumps({'settings': value, **body}), timeout=150)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
                raise
            output = json.loads(stdout)
            code = process.returncode
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'Проверка или запрос AI превысили время ожидания') from None
    except (OSError, ValueError):
        raise HTTPException(502, 'Модуль не вернул корректный результат; проверьте его установку') from None
    finally:
        _runtime_slots.release()
    if code or not output.get('ok'):
        raise HTTPException(502, output.get('error', 'Модуль завершился с ошибкой'))
    return output


def authorize_control(api, authorization, actor):
    value = read(api)
    expected = value.get('control_token', '')
    if not expected or not hmac.compare_digest(authorization or '', 'Bearer ' + expected):
        raise HTTPException(401, 'Нужен служебный токен MCP', headers={'WWW-Authenticate': 'Bearer'})
    if not value['mcp']['vps-controller-mcp']:
        raise HTTPException(403, 'Модуль vpsController MCP выключен')
    if actor != 'panel' and not any(str(a['telegram_id']) == actor and a['vps_access'] for a in value['accounts']):
        raise HTTPException(403, 'Пользователю не разрешён контур vpsController')


def control_tool(api, name):
    if name == 'vps_overview':
        result = api.overview(None)
        return {'server': result['server'], 'resources': result['resources']}
    if name == 'vps_services':
        return {'services': [{'id': key, 'name': definition['name'], **{
            field: observation.get(field) for field in ('state', 'substate', 'active', 'enabled', 'runtime')
        }} for key, definition in api.managed_services().items()
            for observation in [api.observe_service(definition['unit'])]]}
    if name == 'vps_components':
        return {'components': [{key: item.get(key) for key in ('id', 'name', 'installed', 'version', 'management')}
                               for item in api.protocol_images(None)['items']]}
    raise HTTPException(404, 'Инструмент не найден')
