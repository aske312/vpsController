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
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

import application_operation as operations
import assistant_provider
from component_registry import ComponentRegistry, RegistryError

MODULES = ('nodera-bot', 'ai-agent')
MCP_MODULES = ('telegram-mcp', 'vps-controller-mcp')
BOT_UNIT = 'vps-control-nodera-bot.service'
AGENT_UNIT = 'vps-control-ai-agent.service'
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
    component: Literal['ai-agent', 'nodera-bot']
    modules: dict[str, StrictBool]
    mcp: dict[str, StrictBool]
    bot_token: str | None = Field(default=None, max_length=256)
    provider_url: str = Field(default='', max_length=512)
    model: str = Field(default='', max_length=128)
    model_mode: Literal['single', 'catalog'] = 'single'
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
        return assistant_provider.base_url(value)

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
    model: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator('model')
    @classmethod
    def valid_model(cls, value):
        if value is not None and not assistant_provider.model_id(value):
            raise ValueError('Некорректный ID модели')
        return value


class Catalog(BaseModel):
    model_config = {'extra': 'forbid'}
    provider_url: str = Field(max_length=512)
    api_key: str | None = Field(default=None, max_length=2048)

    @field_validator('provider_url')
    @classmethod
    def provider(cls, value):
        if not value.strip():
            raise ValueError('Укажите адрес AI-провайдера')
        return Settings.provider(value)


class Check(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int | None = Field(default=None, strict=True, gt=0)
    bot_token: str | None = Field(default=None, max_length=256)

    @field_validator('bot_token')
    @classmethod
    def token_value(cls, value):
        return Settings.token_value(value)


class ToolCall(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(pattern=r'^vps_(overview|services|components)$')


class Action(BaseModel):
    model_config = {'extra': 'forbid'}
    action: str = Field(pattern=r'^(start|stop|restart)$')


def directory(api):
    return api.DATA_DIR / 'assistant'


def runtime_directory(component='ai-agent'):
    if component not in MODULES:
        raise ValueError('Unknown assistant component')
    variable = 'AI_AGENT_RUNTIME_DIR' if component == 'ai-agent' else 'NODERA_BOT_RUNTIME_DIR'
    return Path(os.getenv(variable, '/var/lib/vps-control-' + component))


def runtime_ready(component='ai-agent'):
    root = runtime_directory(component)
    filename = 'runner.py' if component == 'ai-agent' else 'bot.py'
    try:
        return (root / 'venv/bin/python').is_file() and (root / filename).is_file() and bool((root / 'version').read_text().strip())
    except FileNotFoundError:
        return False


def _read(api):
    value = operations.read(directory(api) / 'settings.json')
    if value.get('schema') == 2:
        revision = value['revision']
        if not re.fullmatch(r'[0-9a-f]{32}', revision):
            raise operations.OperationConflict('Ревизия настроек ассистента повреждена')
        assembled = {'revision': revision, 'control_token': value['control_token'],
                     'modules': {key: False for key in MODULES}, 'mcp': {key: False for key in MCP_MODULES},
                     'provider_url': '', 'model': '', 'telegram_api_id': None, 'accounts': []}
        accounts = {}
        for component in ('ai-agent', 'nodera-bot'):
            folder = directory(api) / component
            settings = operations.read(folder / (revision + '.json'))
            if not settings and folder.exists():
                raise operations.OperationConflict('Файл настроек компонента не найден; автоматический сброс не выполняется')
            assembled['modules'].update(settings.pop('modules', {}))
            for account in settings.pop('accounts', []):
                item = accounts.setdefault(account['telegram_id'], {'telegram_id': account['telegram_id'], 'name': '',
                    'bot_access': False, 'telegram_access': False, 'vps_access': False, 'excluded_dialogs': [], 'session': ''})
                item.update(account)
            assembled.update(settings)
        assembled['accounts'] = list(accounts.values())
        value = assembled
    if not value:
        return {'revision': '0' * 32, 'modules': {key: False for key in MODULES},
                'mcp': {key: False for key in MCP_MODULES},
                'provider_url': '', 'model': '', 'accounts': [], 'telegram_api_id': None}
    try:
        Settings.model_validate({key: item for key, item in value.items() if key not in ('revision', 'control_token')} | {'expected_revision': value['revision'], 'component': 'ai-agent'})
    except (ValueError, KeyError):
        raise operations.OperationConflict('Настройки MCP повреждены; автоматический сброс не выполняется') from None
    return value


def read(api):
    # Observing an unused optional component must not create its data directory.
    if not (directory(api) / 'settings.json').exists():
        return _read(api)
    with operations.locked(api.DATA_DIR):
        return _read(api)


def store(api, value):
    """Publish both private config generations through one atomic pointer.

    Each image owns only its own configuration directory. Purging it cannot
    remove or resurrect credentials from the other image.
    """
    revision = value['revision']
    ai = {key: value.get(key) for key in ('provider_url', 'model', 'api_key', 'telegram_api_id', 'telegram_api_hash')}
    ai['model_mode'] = value.get('model_mode', 'single')
    ai.update(modules={'ai-agent': value['modules']['ai-agent']}, mcp=value['mcp'],
              accounts=[{key: item for key, item in a.items() if key != 'bot_access'} for a in value['accounts']])
    bot = {'modules': {'nodera-bot': value['modules']['nodera-bot']}, 'bot_token': value.get('bot_token', ''),
           'accounts': [{key: a.get(key, False if key == 'bot_access' else '') for key in ('telegram_id', 'name', 'bot_access')}
                        for a in value['accounts']]}
    for component, settings in (('ai-agent', ai), ('nodera-bot', bot)):
        operations.atomic_json(directory(api) / component / (revision + '.json'), settings)
    operations.atomic_json(directory(api) / 'settings.json', {'schema': 2, 'revision': revision, 'control_token': value['control_token']})
    for component in MODULES:
        for path in (directory(api) / component).glob('*.json'):
            if re.fullmatch(r'[0-9a-f]{32}\.json', path.name) and path.stem != revision:
                path.unlink(missing_ok=True)


def migrate_settings(api):
    with operations.locked(api.DATA_DIR):
        old = operations.read(directory(api) / 'settings.json')
        if old.get('schema') == 2 or not old:
            return
        value = _read(api)
        store(api, value)


def require_installed(api, component):
    try:
        ComponentRegistry(api.DATA_DIR).require_managed(component)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None
    if not runtime_ready(component):
        raise HTTPException(409, 'Компонент не установлен или его установка неполная')
    unit = AGENT_UNIT if component == 'ai-agent' else BOT_UNIT
    if api.observe_service(unit).get('unit_present') is not True:
        raise HTTPException(409, 'Регистрация службы компонента не подтверждена')


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
        return bool(value.get('bot_token'))
    if module == 'telegram-mcp':
        return bool(value.get('telegram_api_id') and value.get('telegram_api_hash') and
                    any(a.get('session') and a['telegram_access'] for a in value['accounts']))
    return True


def status(api):
    value = read(api)
    observations = {key: api.observe_service(AGENT_UNIT if key == 'ai-agent' else BOT_UNIT) for key in MODULES}
    installed = {key: runtime_ready(key) and observations[key].get('unit_present') is True for key in MODULES}
    modules = []
    for key, name in zip(MODULES, ('NoderaBot', 'AI-agent')):
        modules.append({'id': key, 'name': name, 'enabled': value['modules'][key],
                        'configured': ready(value, key), 'installed': installed[key],
                        'management': ComponentRegistry(api.DATA_DIR).management(key),
                        'execution': 'service' if key == 'nodera-bot' else 'on-demand'})
    try:
        bot_version = (runtime_directory('nodera-bot') / 'version').read_text().strip()
    except OSError:
        bot_version = ''
    return {'settings': public(value), 'modules': modules, 'runtime_installed': installed['ai-agent'],
            'mcp_modules': [{'id': key, 'name': name, 'enabled': value['mcp'][key], 'configured': ready(value, key),
                             'execution': 'on-demand'} for key, name in zip(MCP_MODULES, ('Telegram MCP', 'vpsController MCP'))],
            'bot': observations['nodera-bot'], 'agent': observations['ai-agent'],
            'bot_live_access': bool(re.fullmatch(r'\d+\.\d+\.\d+', bot_version) and tuple(map(int, bot_version.split('.'))) >= (1, 1, 0)),
            'legacy_bot': api.observe_service('tg-agent-gateway.service'),
            'tools': {'telegram-mcp': ['tg_auth_status', 'tg_dialogs', 'tg_history', 'tg_search'],
                      'vps-controller-mcp': ['vps_overview', 'vps_services', 'vps_components']}}


def configure(api, payload):
    require_installed(api, payload.component)
    bot = api.observe_service(BOT_UNIT)
    if payload.component == 'nodera-bot' and not bot.get('active') and bot.get('unit_present') is not False and bot.get('state') not in ('inactive', 'failed'):
        raise HTTPException(409, 'Состояние NoderaBot не подтверждено. Обновите раздел перед изменением настроек')
    with operations.locked(api.DATA_DIR):
        old = _read(api)
        if payload.component == 'nodera-bot' and bot.get('active') and (
                payload.modules['nodera-bot'] != old['modules']['nodera-bot']
                or payload.bot_token is not None and payload.bot_token != old.get('bot_token', '')):
            raise HTTPException(409, 'Для изменения токена или включения NoderaBot сначала остановите его. Список администраторов можно менять без остановки')
        if old['revision'] != payload.expected_revision:
            raise operations.OperationConflict('Настройки MCP изменились. Обновите раздел перед сохранением')
        submitted = payload.model_dump(exclude={'expected_revision', 'component'})
        value = {**old, 'modules': dict(old['modules']), 'accounts': []}
        keys = ('provider_url', 'model', 'model_mode', 'api_key', 'telegram_api_id', 'telegram_api_hash', 'mcp') if payload.component == 'ai-agent' else ('bot_token',)
        value.update({key: submitted[key] for key in keys})
        value['modules'][payload.component] = submitted['modules'][payload.component]
        for key in ('api_key', 'bot_token', 'telegram_api_hash'):
            if value.get(key) is None:
                value[key] = old.get(key, '')
        previous = {a['telegram_id']: a for a in old['accounts']}
        for account in submitted['accounts']:
            prior = previous.pop(account['telegram_id'], {})
            if payload.component == 'ai-agent':
                account['bot_access'] = prior.get('bot_access', False)
                if account['session'] is None: account['session'] = prior.get('session', '')
            else:
                for key, default in (('telegram_access', False), ('vps_access', False), ('session', ''), ('excluded_dialogs', [])):
                    account[key] = prior.get(key, default)
            value['accounts'].append(account)
        for prior in previous.values():
            if payload.component == 'ai-agent' and prior.get('bot_access'):
                value['accounts'].append({**prior, 'telegram_access': False, 'vps_access': False, 'session': '', 'excluded_dialogs': []})
            elif payload.component == 'nodera-bot' and (prior.get('session') or prior.get('telegram_access') or prior.get('vps_access')):
                value['accounts'].append({**prior, 'bot_access': False})
        value['revision'] = uuid.uuid4().hex
        value['control_token'] = old.get('control_token') or secrets.token_urlsafe(48)
        store(api, value)
    return status(api)


def model_catalog(api, payload):
    require_installed(api, 'ai-agent')
    value = read(api)
    key = payload.api_key
    if key is None:
        old_url = value.get('provider_url', '')
        if not old_url or assistant_provider.base_url(old_url) != payload.provider_url:
            raise HTTPException(409, 'Для нового адреса провайдера введите API-ключ заново')
        key = value.get('api_key', '')
    if not key:
        raise HTTPException(422, 'Укажите API-ключ для загрузки каталога')
    if not _runtime_slots.acquire(blocking=False):
        raise HTTPException(429, 'Два запроса к модулям уже выполняются; дождитесь завершения')
    try:
        return {'models': assistant_provider.catalog(payload.provider_url, key)}
    except ValueError as exc:
        # Forward only our own validation messages, never provider bodies/URLs.
        message = str(exc)
        if not message.startswith(('Каталог ', 'Адрес AI-', 'Провайдер вернул неверный')):
            message = 'Не удалось прочитать каталог моделей провайдера'
        raise HTTPException(502, message) from None
    except Exception:
        raise HTTPException(502, 'Не удалось загрузить каталог моделей. Проверьте адрес, ключ и доступность провайдера') from None
    finally:
        _runtime_slots.release()


def runtime_request(api, mode, body):
    component = 'nodera-bot' if mode == '--check' and body.get('module') == 'nodera-bot' else 'ai-agent'
    require_installed(api, component)
    if component == 'ai-agent' and not api.observe_service(AGENT_UNIT).get('active'):
        raise HTTPException(409, 'Среда AI-agent остановлена. Запустите компонент в разделе Службы')
    python = runtime_directory(component) / 'venv' / 'bin' / 'python'
    value = read(api)
    body = dict(body)
    selected_model = body.pop('model', None)
    if mode == '--chat' and selected_model is not None:
        if not assistant_provider.model_id(selected_model):
            raise HTTPException(422, 'Некорректный ID модели')
        if value.get('model_mode', 'single') != 'catalog' and selected_model != value.get('model'):
            raise HTTPException(409, 'Выбор другой модели доступен в режиме каталога')
        value = {**value, 'model': selected_model}
    if value.get('provider_url'):
        value = {**value, 'provider_url': assistant_provider.base_url(value['provider_url'])}
    if component == 'ai-agent' and value['modules']['nodera-bot']:
        actor = str(body.get('actor', ''))
        value = {**value, 'accounts': [{**account, 'vps_access': True} if str(account['telegram_id']) == actor and account.get('bot_access') else account for account in value['accounts']]}
    if component == 'nodera-bot':
        token = body.pop('bot_token', None)
        token = value.get('bot_token') if token is None else token
        if not token:
            raise HTTPException(422, 'Укажите токен Telegram-бота для проверки')
        # getMe validates credentials only. It neither starts polling nor needs
        # enabled access or users; the saved configuration remains untouched.
        value = {**value, 'bot_token': token, 'modules': {**value['modules'], 'nodera-bot': True}}
    command = [str(python), str(runtime_directory(component) / ('runner.py' if component == 'ai-agent' else 'bot.py')), mode]
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
    if not authorization or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{32,128}', authorization):
        raise HTTPException(401, 'Нужен служебный токен MCP', headers={'WWW-Authenticate': 'Bearer'})
    value = read(api)
    expected = value.get('control_token', '')
    if not expected or not hmac.compare_digest((authorization or '').encode(), ('Bearer ' + expected).encode()):
        raise HTTPException(401, 'Нужен служебный токен MCP', headers={'WWW-Authenticate': 'Bearer'})
    if not value['mcp']['vps-controller-mcp']:
        raise HTTPException(403, 'Модуль vpsController MCP выключен')
    if actor != 'panel' and not any(str(a['telegram_id']) == actor and (a['vps_access'] or value['modules']['nodera-bot'] and a.get('bot_access')) for a in value['accounts']):
        raise HTTPException(403, 'Пользователю не разрешён контур vpsController')
    require_installed(api, 'ai-agent')


def authorize_bot_transport(api, authorization):
    if not authorization or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{32,128}', authorization):
        raise HTTPException(401, 'Нужен служебный токен бота', headers={'WWW-Authenticate': 'Bearer'})
    value = read(api)
    expected = value.get('control_token', '')
    if not expected or not hmac.compare_digest((authorization or '').encode(), ('Bearer ' + expected).encode()):
        raise HTTPException(401, 'Нужен служебный токен бота', headers={'WWW-Authenticate': 'Bearer'})
    if not value['modules']['nodera-bot'] or not value.get('bot_token'):
        raise HTTPException(403, 'NoderaBot выключен или не настроен')
    require_installed(api, 'nodera-bot')
    return value


def authorize_bot(api, authorization, actor):
    value = authorize_bot_transport(api, authorization)
    if not any(str(a['telegram_id']) == actor and a.get('bot_access') for a in value['accounts']):
        raise HTTPException(403, 'Доступ администратора NoderaBot не выдан')


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
