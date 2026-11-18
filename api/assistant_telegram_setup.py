"""Owner-confirmed Telegram MCP credentials submitted from the bot's Mini App."""
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import assistant_contacts as contacts
import assistant_control as control
import assistant_roles as roles

TTL = 600


class Credentials(BaseModel):
    model_config = {'extra': 'forbid'}
    setup: str = Field(pattern=r'^[a-f0-9]{32}$')
    api_id: int = Field(strict=True, gt=0, le=2_147_483_647)
    api_hash: str = Field(pattern=r'^[a-fA-F0-9]{32}$')


def require_owner(api, settings, actor):
    roles.require(settings, actor, 'roles_manage')
    contacts.require_unblocked(api, settings, actor)


def issue(api, settings, actor, state):
    require_owner(api, settings, actor)
    domain = getattr(api, 'PUBLIC_DOMAIN', '')
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', domain) or '.' not in domain:
        raise HTTPException(409, 'Для формы настройки из Telegram задайте HTTPS-домен панели')
    identity = uuid.uuid4().hex
    state['telegram_setup'] = {'id': identity, 'revision': settings['revision'], 'expires': time.time() + TTL}
    return f'https://{domain}/api/assistant/telegram-setup?setup={identity}'


def signed_actor(raw, bot_token):
    unauthorized = HTTPException(401, 'Откройте форму в личном чате с ботом',
                                 headers={'WWW-Authenticate': 'Telegram'})
    if not bot_token or not raw or len(raw) > 8192:
        raise unauthorized
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32, errors='strict')
        fields = dict(pairs)
        signature = fields.pop('hash')
        if (len(fields) + 1 != len(pairs) or not re.fullmatch(r'[0-9a-f]{64}', signature)
                or any(not re.fullmatch(r'[a-z_]+', name) or '\n' in value or '\r' in value for name, value in fields.items())):
            raise unauthorized
        message = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
        key = hmac.digest(b'WebAppData', bot_token.encode(), 'sha256')
        if not hmac.compare_digest(hmac.new(key, message.encode(), hashlib.sha256).hexdigest(), signature):
            raise unauthorized
        age = time.time() - int(fields['auth_date'])
        user = json.loads(fields['user'])
        if not isinstance(user, dict) or not -30 <= age <= TTL or type(user.get('id')) is not int:
            raise unauthorized
        actor = str(user['id'])
        if not re.fullmatch(r'[1-9][0-9]{0,15}', actor) or user.get('is_bot'):
            raise unauthorized
        return actor
    except (ValueError, KeyError, RecursionError):
        raise unauthorized from None


def authorize(api, raw):
    settings = control.read(api)
    actor = signed_actor(raw, settings.get('bot_token'))
    require_owner(api, settings, actor)
    return actor


def save(api, raw, actor, payload):
    with control.operations.locked(api.DATA_DIR):
        settings = control._read(api)
        if signed_actor(raw, settings.get('bot_token')) != actor:
            raise HTTPException(403, 'Личность пользователя изменилась')
        require_owner(api, settings, actor)
        ticket = contacts.profile(api, settings, actor).get('telegram_setup', {})
        if (ticket.get('id') != payload.setup or ticket.get('revision') != settings['revision']
                or ticket.get('expires', 0) <= time.time()):
            raise HTTPException(409, 'Запрос устарел или уже использован. Откройте новую форму из бота.')
        settings.update(telegram_api_id=payload.api_id, telegram_api_hash=payload.api_hash.lower(),
                        revision=uuid.uuid4().hex)
        control.store(api, settings)
    return {'saved': True}


def page():
    nonce = secrets.token_urlsafe(18)
    html = (Path(__file__).parent / 'telegram_setup.html').read_text(encoding='utf-8').replace('__NONCE__', nonce)
    return HTMLResponse(html, headers={
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': ("default-src 'none'; base-uri 'none'; form-action 'self'; connect-src 'self'; "
                                    "frame-ancestors https://web.telegram.org; "
                                    f"script-src https://telegram.org 'nonce-{nonce}'; style-src 'nonce-{nonce}'"),
    })
