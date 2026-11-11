"""Optional assistant modules. Stored configuration is never a health claim."""
from __future__ import annotations

import hashlib
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
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

import application_operation as operations
import assistant_provider
import assistant_memory
import socket
from component_registry import ComponentRegistry, RegistryError

MODULES = ('nodera-bot', 'ai-agent')
MCP_MODULES = ('telegram-mcp', 'vps-controller-mcp')
ACCOUNT_OPTIONS = ('address_as', 'bot_chat_access', 'mihomo_profile_id', 'address_gender')
BOT_UNIT = 'vps-control-nodera-bot.service'
AGENT_UNIT = 'vps-control-ai-agent.service'
_runtime_slots = threading.BoundedSemaphore(2)


class Account(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int = Field(strict=True, gt=0, le=2**52)
    name: str = Field(default='', max_length=80)
    bot_access: StrictBool = False
    bot_chat_access: StrictBool = False
    address_as: str = Field(default='', max_length=80)
    mihomo_profile_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    address_gender: Literal['auto', 'neutral', 'male', 'female'] = 'auto'
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


class Provider(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    name: str = Field(min_length=1, max_length=80)
    provider_url: str = Field(min_length=1, max_length=512)
    model: str = Field(default='', max_length=128)
    model_mode: Literal['single', 'catalog'] = 'single'
    api_key: str | None = Field(default=None, max_length=2048)


class Settings(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: str = Field(pattern=r'^[0-9a-f]{32}$')
    component: Literal['ai-agent', 'nodera-bot', 'nodera']
    modules: dict[str, StrictBool]
    mcp: dict[str, StrictBool]
    mcp_installed: list[Literal['telegram-mcp', 'vps-controller-mcp']] | None = None
    bot_token: str | None = Field(default=None, max_length=256)
    provider_url: str = Field(default='', max_length=512)
    model: str = Field(default='', max_length=128)
    model_mode: Literal['single', 'catalog'] = 'single'
    api_key: str | None = Field(default=None, max_length=2048)
    providers: list[Provider] | None = Field(default=None, max_length=12)
    active_provider: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    assistant_style: Literal['balanced', 'concise', 'friendly', 'technical'] = 'balanced'
    assistant_character: str = Field(default='Нодера — доброжелательная, самостоятельная и внимательная собеседница. Говорит ясно, с лёгким юмором, без лести. Признаёт неопределённость, объясняет решения и уважает предпочтения собеседника.', max_length=2000)
    assistant_instructions: str = Field(default='', max_length=4000)
    personalization_enabled: StrictBool = True
    web_enabled: StrictBool = True
    web_domains: list[str] = Field(default_factory=list, max_length=8)
    memory_enabled: StrictBool = True
    memory_days: int = Field(default=30, strict=True, ge=1, le=90)
    telegram_api_id: int | None = Field(default=None, strict=True, gt=0)
    telegram_api_hash: str | None = Field(default=None, max_length=32)
    accounts: list[Account] = Field(default_factory=list, max_length=30)

    @field_validator('web_domains')
    @classmethod
    def web_sites(cls, values):
        result = []
        for value in values:
            domain = value.strip().lower().encode('idna').decode('ascii')
            if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain) or domain.endswith(('.local', '.internal', '.localhost')) or domain.replace('.', '').isdigit():
                raise ValueError('Укажите публичные домены без https:// и пути')
            if domain not in result: result.append(domain)
        return result

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
        if self.providers is not None:
            ids = {provider.id for provider in self.providers}
            if len(ids) != len(self.providers) or (self.providers and self.active_provider not in ids) or (not self.providers and self.active_provider):
                raise ValueError('Укажите активное подключение из списка API')
            for provider in self.providers:
                provider.provider_url = self.provider(provider.provider_url)
                if not provider.provider_url or provider.model and not assistant_provider.model_id(provider.model):
                    raise ValueError('Укажите адрес и модель подключения')
        if self.mcp_installed is not None:
            self.mcp_installed = list(dict.fromkeys(self.mcp_installed))
            if any(enabled and key not in self.mcp_installed for key, enabled in self.mcp.items()):
                raise ValueError('Сначала добавьте включаемый MCP-модуль')
        ids = [a.telegram_id for a in self.accounts]
        if len(set(ids)) != len(ids):
            raise ValueError('Telegram ID пользователей не должны повторяться')
        return self


class ChatAttachment(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=1, max_length=160, pattern=r'^[^\x00-\x1f/\\]+$')
    mime: Literal['image/png', 'image/jpeg', 'image/webp', 'application/pdf',
                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'text/plain']
    data: str = Field(min_length=1, max_length=2800000)


class ChatMessage(BaseModel):
    model_config = {'extra': 'forbid'}
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=8000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def user_files_only(self):
        if self.role != 'user' and self.attachments:
            raise ValueError('Файлы допустимы только в сообщениях пользователя')
        return self


class Chat(BaseModel):
    model_config = {'extra': 'forbid'}
    prompt: str = Field(default='', max_length=12000)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    telegram_id: int | None = Field(default=None, strict=True, gt=0)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')

    @model_validator(mode='after')
    def bounded_content(self):
        if not self.prompt.strip() and not self.attachments:
            raise ValueError('Введите сообщение или добавьте файл')
        if sum(len(item.data) for item in self.attachments + [file for row in self.history for file in row.attachments]) > 8400000:
            raise ValueError('Общий размер вложений превышен')
        if sum(len(item.content) for item in self.history) > 48000:
            raise ValueError('История диалога превышает лимит')
        return self

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
    provider_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    include_quota: StrictBool = False

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
    name: Literal['vps_overview', 'vps_services', 'vps_components', 'vps_metrics', 'vps_panel', 'vps_service_action', 'vps_operation', 'vps_api', 'vps_user', 'mihomo_capabilities', 'mihomo_api', 'vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics']
    service: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    action: Literal['start', 'stop', 'restart'] | None = None
    operation_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    method: Literal['GET', 'POST', 'PATCH', 'DELETE'] = 'GET'
    path: str | None = Field(default=None, max_length=256)
    body: dict = Field(default_factory=dict)
    lines: int = Field(default=60, strict=True, ge=20, le=120)
    endpoint: Literal['/overview', '/services', '/components', '/metrics', '/application', '/logs', '/network', '/security', '/diagnostics'] | None = None


class ServiceConfirmation(BaseModel):
    model_config = {'extra': 'forbid'}
    code: str = Field(pattern=r'^[0-9a-f]{8}$')
    telegram_id: int | None = Field(default=None, strict=True, gt=0, le=2**52)


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
        extensions = operations.read(directory(api) / 'ai-agent' / (revision + '.nodera.json'))
        for key in ('providers', 'active_provider'):
            if key in extensions:
                assembled[key] = extensions[key]
        options = extensions.get('accounts', {})
        if not isinstance(options, dict) or any(not isinstance(item, dict) for item in options.values()):
            raise operations.OperationConflict('Настройки доступа Nodera повреждены')
        for identity, account in accounts.items():
            for key in ACCOUNT_OPTIONS:
                if key in options.get(str(identity), {}):
                    account[key] = options[str(identity)][key]
        assembled['accounts'] = list(accounts.values())
        value = assembled
    if not value:
        return {'revision': '0' * 32, 'modules': {key: False for key in MODULES},
                'mcp': {key: False for key in MCP_MODULES}, 'mcp_installed': [],
                'provider_url': '', 'model': '', 'accounts': [], 'telegram_api_id': None}
    try:
        Settings.model_validate({key: item for key, item in value.items() if key not in ('revision', 'control_token')} | {'expected_revision': value['revision'], 'component': 'ai-agent'})
    except (ValueError, KeyError):
        raise operations.OperationConflict('Настройки MCP повреждены; автоматический сброс не выполняется') from None
    if value.get('mcp_installed') is None:
        value['mcp_installed'] = list(MCP_MODULES)
    for key, default in (('assistant_style', 'balanced'), ('assistant_character', Settings.model_fields['assistant_character'].default), ('assistant_instructions', ''), ('personalization_enabled', True), ('web_domains', []), ('web_enabled', True), ('memory_enabled', True), ('memory_days', 30)):
        value.setdefault(key, default)
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
    ai['mcp_installed'] = list(dict.fromkeys(value.get('mcp_installed', list(MCP_MODULES)) + [key for key, enabled in value['mcp'].items() if enabled]))
    ai.update({key: value.get(key, default) for key, default in (('assistant_style', 'balanced'), ('assistant_character', Settings.model_fields['assistant_character'].default), ('assistant_instructions', ''), ('personalization_enabled', True), ('web_domains', []), ('web_enabled', True), ('memory_enabled', True), ('memory_days', 30))})
    ai.update(modules={'ai-agent': value['modules']['ai-agent']}, mcp=value['mcp'],
              accounts=[{key: item for key, item in a.items() if key not in ('bot_access', *ACCOUNT_OPTIONS)} for a in value['accounts']])
    bot = {'modules': {'nodera-bot': value['modules']['nodera-bot']}, 'bot_token': value.get('bot_token', ''),
           'accounts': [{key: a.get(key, False if key == 'bot_access' else '') for key in ('telegram_id', 'name', 'bot_access')}
                        for a in value['accounts']]}
    # Keep the active connection and legacy ACL readable after an application rollback.
    extensions = {key: value[key] for key in ('providers', 'active_provider') if key in value}
    extensions['accounts'] = {str(account['telegram_id']): {key: account[key] for key in ACCOUNT_OPTIONS if key in account}
                              for account in value['accounts']}
    for component, settings in (('ai-agent', ai), ('nodera-bot', bot)):
        operations.atomic_json(directory(api) / component / (revision + '.json'), settings)
    operations.atomic_json(directory(api) / 'ai-agent' / (revision + '.nodera.json'), extensions)
    operations.atomic_json(directory(api) / 'settings.json', {'schema': 2, 'revision': revision, 'control_token': value['control_token']})
    for component in MODULES:
        for path in (directory(api) / component).glob('*.json'):
            if re.fullmatch(r'[0-9a-f]{32}(?:\.nodera)?\.json', path.name) and path.name.split('.')[0] != revision:
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
        registry = ComponentRegistry(api.DATA_DIR)
        registry.require_managed('nodera' if registry.receipt('nodera') else component)
    except RegistryError as exc:
        raise HTTPException(409, str(exc)) from None
    if not runtime_ready(component):
        raise HTTPException(409, 'Компонент не установлен или его установка неполная')
    unit = AGENT_UNIT if component == 'ai-agent' else BOT_UNIT
    if api.observe_service(unit).get('unit_present') is not True:
        raise HTTPException(409, 'Регистрация службы компонента не подтверждена')


def public(value):
    hidden = {'api_key', 'bot_token', 'telegram_api_hash', 'control_token', 'session', 'providers'}
    result = {key: item for key, item in value.items() if key not in hidden}
    for key in ('api_key', 'bot_token', 'telegram_api_hash'):
        result[key + '_configured'] = bool(value.get(key))
    result['accounts'] = [{key: item for key, item in account.items() if key != 'session'} |
                          {'session_configured': bool(account.get('session'))} for account in value['accounts']]
    result['providers'] = [{key: item for key, item in provider.items() if key != 'api_key'} |
                           {'api_key_configured': bool(provider.get('api_key'))} for provider in providers(value)]
    result['active_provider'] = active_provider(value)
    return result


def providers(value):
    if value.get('providers') is not None:
        return value['providers']
    if not value.get('provider_url'):
        return []
    url = assistant_provider.base_url(value['provider_url'])
    return [{'id': uuid.uuid5(uuid.NAMESPACE_URL, url).hex, 'name': urllib.parse.urlsplit(url).hostname,
             'provider_url': url, 'model': value.get('model', ''), 'model_mode': value.get('model_mode', 'single'),
             'api_key': value.get('api_key', '')}]


def active_provider(value):
    entries = providers(value)
    return value.get('active_provider', entries[0]['id'] if entries else None)


def apply_providers(value, old, submitted):
    previous = {entry['id']: entry for entry in providers(old)}
    entries = submitted['providers']
    for entry in entries:
        prior = previous.get(entry['id'], {})
        if entry['api_key'] is None:
            entry['api_key'] = prior.get('api_key', '') if prior.get('provider_url') == entry['provider_url'] else ''
    value['providers'] = entries
    value['active_provider'] = submitted['active_provider']
    active = next((entry for entry in entries if entry['id'] == value['active_provider']), {})
    for key in ('provider_url', 'model', 'api_key', 'model_mode'):
        value[key] = active.get(key, 'single' if key == 'model_mode' else '')


def ready(value, module):
    if module == 'ai-agent':
        return bool(value.get('provider_url') and value.get('api_key') and
                    (value.get('model_mode') == 'catalog' or value.get('model')))
    if module == 'nodera-bot':
        return bool(value.get('bot_token'))
    if module == 'telegram-mcp':
        return bool(value.get('telegram_api_id') and value.get('telegram_api_hash') and
                    any(a.get('session') and a['telegram_access'] for a in value['accounts']))
    return True


def bot_identity(api, token, username=None):
    if not token:
        return None
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    path = directory(api) / 'nodera-bot' / 'identity.json'
    if username is not None:
        if isinstance(username, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', username):
            with operations.locked(api.DATA_DIR):
                identities = operations.read(path)
                identities.pop(fingerprint, None)
                identities[fingerprint] = username
                operations.atomic_json(path, dict(list(identities.items())[-4:]))
        return None
    identity = operations.read(path).get(fingerprint)
    if isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', identity):
        return identity
    return None


def status(api):
    import component_checks, component_state
    from datetime import datetime, timezone
    value = read(api)
    observations = {key: api.observe_service(AGENT_UNIT if key == 'ai-agent' else BOT_UNIT) for key in MODULES}
    installed = {key: runtime_ready(key) and observations[key].get('unit_present') is True for key in MODULES}
    modules = []
    registry = ComponentRegistry(api.DATA_DIR)
    combined = registry.management('nodera')
    has_receipt = registry.receipt_path('nodera').exists()
    for key, name in zip(MODULES, ('Telegram', 'Nodera')):
        modules.append({'id': key, 'name': name, 'enabled': value['modules'][key],
                        'configured': ready(value, key), 'installed': installed[key],
                        'management': combined if has_receipt or combined['state'] == 'managed' else registry.management(key),
                        'execution': 'service' if key == 'nodera-bot' else 'on-demand'})
    try:
        bot_version = (runtime_directory('nodera-bot') / 'version').read_text().strip()
    except OSError:
        bot_version = ''
    try:
        agent_version = tuple(map(int, (runtime_directory() / 'version').read_text().strip().split('.')))
    except (OSError, ValueError):
        agent_version = ()
    try:
        available_version = json.loads((Path(__file__).resolve().parents[1] / 'protocol-images/nodera/manifest.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        available_version = ''
    try:
        nodera_version = (runtime_directory() / 'nodera-version').read_text().strip()
    except OSError:
        nodera_version = ''
    try:
        usage = provider_usage(api, value)
    except (OSError, ValueError, operations.OperationConflict):
        usage = {}
    try:
        bot_username = bot_identity(api, value.get('bot_token'))
    except (OSError, operations.OperationConflict):
        bot_username = None
    return {'settings': public(value), 'health': component_state.health_state(component_checks.cached(api).get('nodera'), now=datetime.now(timezone.utc)), 'modules': modules, 'runtime_installed': installed['ai-agent'],
            'bot_username': bot_username,
            'provider_usage': {entry['id']: usage[entry['id']] for entry in providers(value) if entry['id'] in usage},
            'nodera_version': nodera_version, 'nodera_available_version': available_version,
            'nodera_management': combined,
            'chat_attachments': agent_version >= (1, 1, 0),
            'agent_version': '.'.join(map(str, agent_version)),
            'chat_persona': agent_version >= (1, 2, 0),
            'web_tools': agent_version >= (1, 7, 0),
            'context_tools': agent_version >= (1, 9, 0),
            'mcp_modules': [{'id': key, 'name': name, 'enabled': value['mcp'][key], 'configured': ready(value, key),
                             'installed': key in value['mcp_installed'],
                             'execution': 'on-demand'} for key, name in zip(MCP_MODULES, ('Telegram MCP', 'mcp vps'))],
            'bot': observations['nodera-bot'], 'agent': observations['ai-agent'],
            'bot_live_access': bool(re.fullmatch(r'\d+\.\d+\.\d+', bot_version) and tuple(map(int, bot_version.split('.'))) >= (1, 1, 0)),
            'legacy_bot': api.observe_service('tg-agent-gateway.service'),
            'tools': {'web': (['web_search', 'web_read'] + (['web_research'] if agent_version >= (1, 9, 0) else [])) if agent_version >= (1, 7, 0) else [],
                      'personalization': ['context_read', 'context_remember', 'context_forget'] if agent_version >= (1, 9, 0) else [], 'telegram-mcp': ['tg_auth_status', 'tg_dialogs', 'tg_history', 'tg_search'],
                      'vps-controller-mcp': ['vps_overview', 'vps_services', 'vps_components'] +
                          (['vps_metrics', 'vps_panel'] if agent_version >= (1, 4, 0) else []) +
                          (['vps_service_action', 'vps_operation', 'vps_api'] if agent_version >= (1, 5, 0) else []) +
                          (['vps_user', 'mihomo_capabilities', 'mihomo_api'] if agent_version >= (1, 6, 0) else []) +
                          (['vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics'] if agent_version >= (1, 10, 0) else [])}}


def configure(api, payload):
    unified = payload.component == 'nodera'
    if not unified:
        require_installed(api, payload.component)
    else:
        require_installed(api, 'ai-agent' if runtime_ready('ai-agent') else 'nodera-bot')
    before = read(api)
    previous_links = {entry['telegram_id']: entry.get('mihomo_profile_id') for entry in before['accounts']}
    new_links = {entry.mihomo_profile_id for entry in payload.accounts if entry.mihomo_profile_id and entry.mihomo_profile_id != previous_links.get(entry.telegram_id)}
    if new_links:
        import assistant_mihomo
        available = {entry['id'] for entry in assistant_mihomo.request('GET', '/profiles').get('items', [])}
        if not new_links <= available:
            raise HTTPException(422, 'Привязываемая подписка Mihomo не найдена')
    bot = api.observe_service(BOT_UNIT)
    if payload.component == 'nodera-bot' and not bot.get('active') and bot.get('unit_present') is not False and bot.get('state') not in ('inactive', 'failed'):
        raise HTTPException(409, 'Состояние NoderaBot не подтверждено. Обновите раздел перед изменением настроек')
    with operations.locked(api.DATA_DIR):
        old = _read(api)
        bot_changes = (payload.modules['nodera-bot'] != old['modules']['nodera-bot']
                       or payload.bot_token is not None and payload.bot_token != old.get('bot_token', ''))
        if unified and bot_changes:
            require_installed(api, 'nodera-bot')
            if not bot.get('active') and bot.get('state') not in ('inactive', 'failed'):
                raise HTTPException(409, 'Состояние Telegram-бота не подтверждено')
        if (unified or payload.component == 'nodera-bot') and bot.get('active') and bot_changes:
            raise HTTPException(409, 'Для изменения токена или включения NoderaBot сначала остановите его. Список администраторов можно менять без остановки')
        if old['revision'] != payload.expected_revision:
            raise operations.OperationConflict('Настройки MCP изменились. Обновите раздел перед сохранением')
        submitted = payload.model_dump(exclude={'expected_revision', 'component'})
        for key, default in (('assistant_style', 'balanced'), ('assistant_character', Settings.model_fields['assistant_character'].default), ('assistant_instructions', ''), ('personalization_enabled', True), ('web_domains', []), ('web_enabled', True), ('memory_enabled', True), ('memory_days', 30)):
            if key not in payload.model_fields_set:
                submitted[key] = old.get(key, default)
        value = {**old, 'modules': dict(old['modules']), 'accounts': []}
        if submitted['mcp_installed'] is None:
            submitted['mcp_installed'] = list(dict.fromkeys(old['mcp_installed'] + [key for key, enabled in payload.mcp.items() if enabled]))
        ai_keys = ('provider_url', 'model', 'model_mode', 'api_key', 'telegram_api_id', 'telegram_api_hash', 'mcp', 'mcp_installed', 'assistant_style', 'assistant_character', 'assistant_instructions', 'personalization_enabled', 'web_domains', 'web_enabled', 'memory_enabled', 'memory_days')
        keys = ai_keys + ('bot_token',) if unified else ai_keys if payload.component == 'ai-agent' else ('bot_token',)
        value.update({key: submitted[key] for key in keys})
        if unified and submitted['providers'] is not None:
            apply_providers(value, old, submitted)
        elif payload.component == 'ai-agent' and old.get('providers'):
            if not submitted['provider_url']:
                raise HTTPException(422, 'Удалите подключение в разделе Nodera')
            active = active_provider(old)
            entries = [{**entry, **{key: submitted[key] for key in ('provider_url', 'model', 'api_key', 'model_mode')}}
                       if entry['id'] == active else dict(entry) for entry in providers(old)]
            apply_providers(value, old, {'active_provider': active, 'providers': entries})
        elif payload.component == 'ai-agent':
            value.pop('providers', None)
            value.pop('active_provider', None)
        if unified:
            value['modules'].update(submitted['modules'])
        else:
            value['modules'][payload.component] = submitted['modules'][payload.component]
        for key in ('api_key', 'bot_token', 'telegram_api_hash'):
            if value.get(key) is None:
                value[key] = old.get(key, '')
        previous = {a['telegram_id']: a for a in old['accounts']}
        for account, submitted_account in zip(submitted['accounts'], payload.accounts):
            prior = previous.pop(account['telegram_id'], {})
            for key in ACCOUNT_OPTIONS:
                if key not in submitted_account.model_fields_set:
                    account[key] = prior.get(key, account[key])
            if unified:
                if account['session'] is None:
                    account['session'] = prior.get('session', '')
                if account['bot_access']:
                    account.update(bot_chat_access=False)
            elif payload.component == 'ai-agent':
                account['bot_access'] = prior.get('bot_access', False)
                for key, default in (('bot_chat_access', False), ('mihomo_profile_id', None), ('address_gender', 'auto')):
                    if key not in submitted_account.model_fields_set:
                        account[key] = prior.get(key, default)
                if account['session'] is None: account['session'] = prior.get('session', '')
            else:
                for key, default in (('telegram_access', False), ('vps_access', False), ('session', ''), ('excluded_dialogs', []), ('address_as', ''), ('mihomo_profile_id', None), ('address_gender', 'auto')):
                    account[key] = prior.get(key, default)
            value['accounts'].append(account)
        for prior in previous.values():
            if payload.component == 'ai-agent' and (prior.get('bot_access') or prior.get('bot_chat_access')):
                value['accounts'].append({**prior, 'telegram_access': False, 'vps_access': False, 'session': '', 'excluded_dialogs': []})
            elif payload.component == 'nodera-bot' and (prior.get('session') or prior.get('telegram_access') or prior.get('vps_access')):
                value['accounts'].append({**prior, 'bot_access': False})
        value['revision'] = uuid.uuid4().hex
        value['control_token'] = old.get('control_token') or secrets.token_urlsafe(48)
        store(api, value)
    return status(api)


def provider_usage(api, value, result=None):
    path = directory(api) / 'ai-agent' / 'provider-usage.json'
    if result is None:
        return operations.read(path)
    identity = active_provider(value)
    if not identity:
        return {}
    usage = result.get('usage', {})
    with operations.locked(api.DATA_DIR):
        current_ids = {entry['id'] for entry in providers(_read(api))}
        if identity not in current_ids:
            return {}
        rows = {key: row for key, row in operations.read(path).items() if key in current_ids}
        row = rows.setdefault(identity, {'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'reported_requests': 0})
        row['requests'] += 1
        if isinstance(usage, dict) and all(type(usage.get(key)) is int and 0 <= usage[key] <= 10**9 for key in ('prompt_tokens', 'completion_tokens')):
            row['reported_requests'] += 1
            for key in ('prompt_tokens', 'completion_tokens'):
                row[key] += usage[key]
        operations.atomic_json(path, rows)
    return rows


def model_catalog(api, payload):
    require_installed(api, 'ai-agent')
    value = read(api)
    key = payload.api_key
    if key is None:
        provider = next((entry for entry in providers(value) if entry['id'] == (payload.provider_id or active_provider(value))), {})
        if provider.get('provider_url') != payload.provider_url:
            raise HTTPException(409, 'Для нового адреса провайдера введите API-ключ заново')
        key = provider.get('api_key', '')
    if not key:
        raise HTTPException(422, 'Укажите API-ключ для загрузки каталога')
    if not _runtime_slots.acquire(blocking=False):
        raise HTTPException(429, 'Два запроса к модулям уже выполняются; дождитесь завершения')
    try:
        result = {'models': assistant_provider.catalog(payload.provider_url, key)}
        if payload.include_quota:
            result['quota'] = assistant_provider.token_quota(payload.provider_url, key)
        return result
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


def chat_request(api, payload, actor='panel', channel='panel', *, settings=None):
    settings = read(api) if settings is None else settings
    files = [item.model_dump() for item in payload.attachments]
    body = {'prompt': payload.prompt, 'actor': actor, 'model': payload.model, 'attachments': files,
            'history': [item.model_dump() for item in payload.history]}
    if channel == 'bot' and settings.get('model_mode') == 'catalog' and not body['model']:
        preference = operations.read(bot_model_path(api, actor))
        if model_preference_matches(preference, settings):
            body['model'] = preference.get('model')
        if not body['model']:
            body['model'] = settings.get('model')
        if not body['model']:
            return {'ok': True, 'result': {'answer': 'Выберите модель: /models — список, /model <ID> — выбор. После этого можно написать Нодере.', 'tools_used': []}}
    identity = payload.conversation_id
    if settings.get('memory_enabled', True):
        if channel == 'bot' and not identity:
            recent = assistant_memory.listing(api, settings, actor, channel)['items']
            identity = recent[0]['id'] if recent else None
        if identity:
            body['history'] = assistant_memory.context(assistant_memory.get(api, identity, actor, channel, settings=settings), files)
        else:
            identity = uuid.uuid4().hex
    body['context_conversation'] = identity
    body['settings_revision'] = settings['revision']
    output = runtime_request(api, '--chat', body)
    import assistant_commands
    if any(tool in output['result'].get('tools_used', []) for tool in ('vps_service_action', 'mihomo_api')):
        pending = operations.read(assistant_commands.pending_path(api, actor))
        if pending and not pending.get('submitted') and pending.get('expires', 0) > time.time():
            output['pending_action'] = {key: pending[key] for key in ('service', 'action', 'code', 'expires', 'operation_id')}
            if pending.get('kind') == 'mihomo':
                import assistant_mihomo
                output['pending_action']['changes'] = assistant_mihomo.sanitize(pending['body'])
    try:
        usage = provider_usage(api, settings, output['result'])
        output['provider_usage'] = {entry['id']: usage[entry['id']] for entry in providers(settings) if entry['id'] in usage}
    except (OSError, ValueError, KeyError, TypeError, operations.OperationConflict):
        pass  # A statistics write must not discard a completed response.
    if settings.get('memory_enabled', True):
        try:
            assistant_memory.append(api, identity, actor, channel, payload.prompt, files, output['result'], body['model'] or settings.get('model', ''), settings)
            output['conversation_id'] = identity
        except (OSError, HTTPException, operations.OperationConflict):
            output['cache_error'] = 'Ответ получен, но кеш диалога сохранить не удалось'
    return output


def bot_model_path(api, actor):
    if not re.fullmatch(r'[1-9][0-9]{0,15}', str(actor)):
        raise HTTPException(403, 'Нужен Telegram ID пользователя')
    return directory(api) / 'nodera-bot' / 'model-choice' / (str(actor) + '.json')


def model_preference_matches(preference, settings):
    url = settings.get('provider_url', '')
    previous_id = preference.get('provider_id', uuid.uuid5(uuid.NAMESPACE_URL, url).hex)
    return preference.get('provider_url') == url and previous_id == active_provider(settings)


def bot_models(api, actor, model=None, page=1):
    settings = read(api)
    if settings.get('model_mode', 'single') != 'catalog':
        return 'Одна модель: ' + (settings.get('model') or 'не настроена')
    if model == 'default':
        with operations.locked(api.DATA_DIR):
            bot_model_path(api, actor).unlink(missing_ok=True)
        return 'Модель по умолчанию: ' + (settings.get('model') or 'не выбрана в панели')
    if model is None and page is None:
        preference = operations.read(bot_model_path(api, actor))
        return 'Модель: ' + ((preference.get('model') if model_preference_matches(preference, settings) else None) or settings.get('model') or 'не выбрана') + '\n/models — каталог; /model <ID> — выбрать'
    catalog = model_catalog(api, Catalog(provider_url=settings['provider_url'], provider_id=active_provider(settings)))['models']
    if model is not None:
        if model not in catalog:
            raise HTTPException(409, 'Модель недоступна вашему API-ключу. Используйте /models')
        with operations.locked(api.DATA_DIR):
            file = bot_model_path(api, actor); file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            operations.atomic_json(file, {'model': model, 'provider_url': settings['provider_url'], 'provider_id': active_provider(settings)})
        return 'Выбрана модель: ' + model
    pages = max(1, (len(catalog) + 39) // 40)
    if not 1 <= page <= pages:
        raise HTTPException(409, f'Нужна страница от 1 до {pages}: /models <страница>')
    return f'Модели · страница {page}/{pages}\n' + '\n'.join(catalog[(page-1)*40:page*40]) + '\n\n/model <ID> — выбрать'


def runtime_request(api, mode, body):
    component = 'nodera-bot' if mode == '--check' and body.get('module') == 'nodera-bot' else 'ai-agent'
    require_installed(api, component)
    if component == 'ai-agent' and not api.observe_service(AGENT_UNIT).get('active'):
        raise HTTPException(409, 'Среда AI-agent остановлена. Запустите компонент в разделе Службы')
    python = runtime_directory(component) / 'venv' / 'bin' / 'python'
    value = read(api)
    value['server_name'] = socket.gethostname()
    import assistant_contacts
    value['interlocutor'] = assistant_contacts.interlocutor(api, value, str(body.get('actor', 'panel')))
    body = dict(body)
    import assistant_personalization
    if value.get('personalization_enabled', True):
        try:
            value['personal_context'] = assistant_personalization.for_model(assistant_personalization.snapshot(api, value, str(body.get('actor', 'panel')), body.pop('context_conversation', None)), body.get('prompt', ''))
        except (OSError, HTTPException, operations.OperationConflict):
            value['personal_context'] = {'unavailable': True}
        prompt = body.get('prompt', '')
        value['context_permits'] = {}
        if re.search(r'(?iu)\b(запомни|запомните|remember)\b', prompt):
            value['context_permits']['remember'] = assistant_personalization.permit(value, str(body.get('actor', 'panel')), 'remember')
        if re.search(r'(?iu)\b(забудь|forget)\b|(?:удали|delete).{0,30}(?:памят|контекст|факт|memory)', prompt):
            value['context_permits']['forget'] = assistant_personalization.permit(value, str(body.get('actor', 'panel')), 'forget')
    body.pop('context_conversation', None)
    expected_revision = body.pop('settings_revision', None)
    if expected_revision and value['revision'] != expected_revision:
        raise HTTPException(409, 'Настройки Nodera изменились. Обновите раздел и повторите запрос.')
    if body.get('attachments') or body.get('history'):
        try:
            version = tuple(map(int, (runtime_directory() / 'version').read_text().strip().split('.')))
        except (OSError, ValueError):
            version = ()
        if version < (1, 1, 0):
            raise HTTPException(409, 'Обновите компонент AI-agent в Обзоре для файлов и истории диалога')
        if version < (1, 2, 0) and body.get('history'):
            body['history'] = list(body['history'])
            while len(body['history']) > 12 or sum(len(row['content']) for row in body['history']) > 24000:
                del body['history'][:2]
    selected_model = body.pop('model', None)
    if component == 'ai-agent' and mode == '--check' and body.get('module') == 'ai-agent' and value.get('model_mode') == 'catalog':
        result = model_catalog(api, Catalog(provider_url=value['provider_url']))
        return {'ok': True, 'result': {'models_available': len(result['models'])}}
    if mode == '--chat' and not selected_model:
        selected_model = value.get('model') or None
    if mode == '--chat' and value.get('model_mode') == 'catalog' and not selected_model:
        raise HTTPException(409, 'Выберите модель для запроса в чате')
    if mode == '--chat' and selected_model is not None:
        if not assistant_provider.model_id(selected_model):
            raise HTTPException(422, 'Некорректный ID модели')
        if value.get('model_mode', 'single') != 'catalog' and selected_model != value.get('model'):
            raise HTTPException(409, 'Выбор другой модели доступен в режиме каталога')
        value = {**value, 'model': selected_model}
    if value.get('provider_url'):
        value = {**value, 'provider_url': assistant_provider.base_url(value['provider_url'])}
    value.pop('providers', None)
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
    if component == 'nodera-bot' and mode == '--check':
        try:
            bot_identity(api, value['bot_token'], output.get('result', {}).get('username'))
        except (OSError, operations.OperationConflict):
            pass
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
    if actor != 'panel' and not any(str(a['telegram_id']) == actor and (a['vps_access'] or a.get('mihomo_profile_id') or value['modules']['nodera-bot'] and a.get('bot_access')) for a in value['accounts']):
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


def authorize_bot(api, authorization, actor, *, chat=False):
    value = authorize_bot_transport(api, authorization)
    require_bot_actor(value, actor, chat=chat)
    return value


def require_bot_actor(value, actor, *, chat=False):
    if not any(str(a['telegram_id']) == actor and (a.get('bot_access') or chat and a.get('bot_chat_access')) for a in value['accounts']):
        raise HTTPException(403, 'Доступ к этому действию в Telegram не выдан')


def control_tool(api, name, arguments=None, actor='panel'):
    import assistant_commands
    arguments = arguments or {}
    if name in ('vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics'):
        import assistant_diagnostics
        return assistant_diagnostics.call(api, name, arguments, actor)
    if name in ('vps_user', 'mihomo_capabilities', 'mihomo_api'):
        import assistant_mihomo
        if name == 'vps_user':
            import assistant_contacts
            return assistant_contacts.interlocutor(api, read(api), actor)
        if name == 'mihomo_capabilities':
            return assistant_mihomo.capabilities(api, actor)
        return assistant_mihomo.call(api, actor, arguments.get('method', 'GET'), arguments.get('path', ''), arguments.get('body', {}))
    if actor != 'panel':
        profile = next((entry for entry in read(api)['accounts'] if str(entry['telegram_id']) == actor), {})
        if not profile.get('vps_access') and not profile.get('bot_access'):
            pending = operations.read(assistant_commands.pending_path(api, actor))
            if name != 'vps_operation' or pending.get('kind') != 'mihomo' or arguments.get('operation_id') != pending.get('operation_id'):
                raise HTTPException(403, 'Доступны только собственная подписка Mihomo и информация о себе')
    if name == 'vps_service_action':
        service, action = arguments.get('service'), arguments.get('action')
        if not isinstance(service, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', service) or action not in ('start', 'stop', 'restart'):
            raise HTTPException(422, 'Укажите службу и действие start, stop или restart')
        # MCP can prepare an action but has no tool that confirms it.
        assistant_commands.execute(api, actor, f'/service {service} {action}')
        record = operations.read(assistant_commands.pending_path(api, actor))
        return {'confirmation_required': True, 'service': service, 'action': action,
                'operation_id': record['operation_id'],
                'message': 'Пользователь должен подтвердить действие кнопкой в панели или командой /confirm в Telegram.'}
    if name == 'vps_operation':
        identity = arguments.get('operation_id', '')
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise HTTPException(422, 'Укажите ID операции')
        value = api.get_application_operation(identity, None)
        return {key: value.get(key) for key in ('id', 'action', 'state')}
    if name == 'vps_api':
        endpoints = {'/overview': 'vps_overview', '/services': 'vps_services', '/components': 'vps_components',
                     '/metrics': 'vps_metrics', '/application': 'vps_panel', '/logs': 'vps_logs', '/network': 'vps_network', '/security': 'vps_security', '/diagnostics': 'vps_diagnostics'}
        tool = endpoints.get(arguments.get('endpoint'))
        if not tool:
            raise HTTPException(422, 'Этот раздел API недоступен через MCP')
        return control_tool(api, tool, actor=actor)

    if name == 'vps_overview':
        result = api.overview(None)
        return {'server': result['server'], 'resources': result['resources']}
    if name == 'vps_services':
        return {'services': [{'id': key, 'name': definition['name'], 'controls': assistant_commands.controls(key, definition), **{
            field: observation.get(field) for field in ('state', 'substate', 'active', 'enabled', 'runtime', 'service_type')
        }} for key, definition in api.managed_services().items()
            for observation in [api.observe_service(definition['unit'])]]}
    if name == 'vps_components':
        return {'components': [{key: item.get(key) for key in ('id', 'name', 'installed', 'version', 'installed_version', 'available_version', 'update_available', 'management', 'component_state')}
                               for item in api.protocol_images(None)['items']]}
    if name == 'vps_metrics':
        history = api.get_metrics_history('hour', None)
        fields = ('cpu_percent', 'memory_used_percent', 'disk_used_percent', 'rx_bps', 'tx_bps')
        points = [{key: point.get(key) for key in ('at', *fields)} for point in history['points']
                  if any(point.get(key) is not None for key in fields)]
        return {'period': 'hour', 'resolution_s': history['resolution_s'], 'points': points[-60:]}
    if name == 'vps_panel':
        return {'commit': api.installed_build_commit(), 'channel': api.expected_application_branch(),
                'access_mode': 'vpn' if os.getenv('ACCESS_MODE') == 'vpn' else 'external',
                'services': {unit: api.observe_service(unit).get('runtime', {}).get('state')
                             for unit in ('vps-control-api.service', 'vps-control-web.service', 'caddy.service')}}
    raise HTTPException(404, 'Инструмент не найден')
