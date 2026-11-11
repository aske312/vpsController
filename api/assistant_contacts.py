"""Observed private bot contacts. Username lookup never grants permissions."""
import hashlib
import datetime
import json
import re
import time
import urllib.request

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

import assistant_control as control

MAX_CONTACTS = 500
RETENTION = 30 * 86400


def username(value):
    value = value.strip().removeprefix('@').lower()
    if not re.fullmatch(r'[a-z0-9_]{1,64}', value):
        raise ValueError('Укажите Telegram username, например @aske312')
    return value


class Birthdate(BaseModel):
    model_config = {'extra': 'forbid'}
    day: int = Field(strict=True, ge=1, le=31)
    month: int = Field(strict=True, ge=1, le=12)
    year: int | None = Field(default=None, strict=True, ge=1, le=9999)

    @model_validator(mode='after')
    def valid_date(self):
        datetime.date(self.year or 2000, self.month, self.day)
        return self


class Contact(BaseModel):
    model_config = {'extra': 'forbid'}
    telegram_id: int = Field(strict=True, gt=0, le=2**52)
    username: str = Field(default='', max_length=64)
    name: str = Field(default='', max_length=80)
    last_name: str = Field(default='', max_length=80)
    language_code: str = Field(default='', max_length=35, pattern=r'^[a-zA-Z0-9_-]*$')
    is_premium: StrictBool | None = None
    bio: str = Field(default='', max_length=1024)
    birthdate: Birthdate | None = None
    is_bot: StrictBool = False

    @field_validator('username')
    @classmethod
    def valid_username(cls, value):
        return username(value) if value else ''


class Lookup(BaseModel):
    model_config = {'extra': 'forbid'}
    username: str = Field(min_length=1, max_length=65)

    @field_validator('username')
    @classmethod
    def valid_username(cls, value):
        return username(value)


def path(api, settings):
    bot = hashlib.sha256(settings.get('bot_token', '').encode()).hexdigest()[:24]
    return control.directory(api) / 'nodera-bot' / 'contacts' / (bot + '.json')


def observed(api, settings):
    rows = control.operations.read(path(api, settings)).get('items', [])
    if not isinstance(rows, list):
        raise HTTPException(503, 'Список собеседников бота повреждён')
    return [row for row in rows if isinstance(row, dict) and type(row.get('telegram_id')) is int
            and isinstance(row.get('username'), str) and isinstance(row.get('name'), str)
            and isinstance(row.get('seen_at'), (int, float)) and time.time() - row['seen_at'] < RETENTION]


def list_recent(api):
    settings = control.read(api)
    if not settings.get('bot_token'):
        return []
    with control.operations.locked(api.DATA_DIR):
        rows = sorted(observed(api, settings), key=lambda row: row['seen_at'], reverse=True)
    return rows


def interlocutor(api, settings, actor):
    user = next((entry for entry in settings['accounts'] if str(entry['telegram_id']) == actor), {})
    with control.operations.locked(api.DATA_DIR):
        contact = next((row for row in observed(api, settings) if str(row['telegram_id']) == actor), {}) if actor != 'panel' and settings.get('bot_token') else {}
    return {'role': 'administrator' if actor == 'panel' or user.get('bot_access') else 'user',
            'telegram_id': user.get('telegram_id'), 'name': user.get('address_as') or contact.get('name') or user.get('name', ''),
            'address_gender': user.get('address_gender', 'auto'), 'mihomo_profile_id': user.get('mihomo_profile_id'),
            'telegram': {key: contact[key] for key in ('username', 'name', 'last_name', 'language_code', 'is_premium', 'bio', 'birthdate') if key in contact}}


def remember(api, settings, contact, actor):
    if str(contact.telegram_id) != actor or contact.is_bot:
        raise HTTPException(403, 'Контакт не соответствует отправителю')
    user = next((a for a in settings['accounts'] if a['telegram_id'] == contact.telegram_id), {})
    access = {'admin': bool(user.get('bot_access')), 'chat': bool(user.get('bot_access') or user.get('bot_chat_access'))}
    with control.operations.locked(api.DATA_DIR):
        known = observed(api, settings)
        previous = next((row for row in known if row['telegram_id'] == contact.telegram_id), None)
        details = contact.model_dump(exclude={'is_bot'})
        if previous and all(previous.get(key) == value for key, value in details.items()) and time.time() - previous['seen_at'] < 3600:
            return access
        rows = [row for row in known if row['telegram_id'] != contact.telegram_id
                and (not contact.username or row['username'] != contact.username)]
        rows.append({**details, 'seen_at': int(time.time())})
        control.operations.atomic_json(path(api, settings), {'items': rows[-MAX_CONTACTS:]})
    return access


def telegram_chat(token, identity):
    # Fixed Telegram origin, no environment proxies, no token in exception text.
    try:
        request = urllib.request.Request('https://api.telegram.org/bot' + token + '/getChat',
                                         data=json.dumps({'chat_id': identity}).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=15) as response:
            value = json.load(response)
        if value.get('ok') and isinstance(value.get('result'), dict):
            return value['result']
    except (OSError, ValueError):
        pass
    raise HTTPException(502, 'Не удалось подтвердить пользователя в Telegram. Попросите его отправить боту /id')


def lookup(api, tag):
    control.require_installed(api, 'nodera-bot')
    settings = control.read(api)
    if not settings.get('bot_token'):
        raise HTTPException(409, 'Сначала сохраните токен NoderaBot')
    with control.operations.locked(api.DATA_DIR):
        matches = [row for row in observed(api, settings) if row['username'] == tag]
    if len(matches) != 1:
        raise HTTPException(404, 'Пользователь ещё не найден. Попросите его написать этому боту /start или /id, затем повторите поиск')
    identity = matches[0]['telegram_id']
    chat = telegram_chat(settings['bot_token'], identity)
    if chat.get('type') != 'private' or chat.get('id') != identity or str(chat.get('username', '')).lower() != tag:
        raise HTTPException(409, 'Тег изменился или не подтверждён. Попросите пользователя отправить /id и повторите поиск')
    return {'telegram_id': identity, 'username': tag, 'name': str(chat.get('first_name', ''))[:80]}
