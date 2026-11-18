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
import assistant_roles as roles

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


class ProfileEdit(BaseModel):
    model_config = {'extra': 'forbid'}
    display_name: str | None = Field(default=None, max_length=80)
    birthdate: Birthdate | None = None
    blocked: StrictBool | None = None

    @model_validator(mode='after')
    def past_birthdate(self):
        if self.birthdate and self.birthdate.year and datetime.date(self.birthdate.year, self.birthdate.month, self.birthdate.day) > datetime.date.today():
            raise ValueError('Дата рождения не может быть в будущем')
        return self


class ProfileDialog(BaseModel):
    model_config = {'extra': 'forbid'}
    text: str = Field(max_length=12000)
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')


def profiles_path(api, settings):
    # Telegram bot ID stays stable when its secret token is rotated.
    identity = settings.get('bot_token', '').split(':', 1)[0]
    bot = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return control.directory(api) / 'nodera-bot' / 'contacts' / 'profiles' / bot


def profiles(api, settings):
    # Only the owner list enumerates profiles; receipts stay out of that list.
    return {file.stem: {key:value for key,value in control.operations.read(file).items() if key not in ('receipts','admin_receipts','telegram_receipts')}
            for file in profiles_path(api, settings).glob('*.json') if re.fullmatch(r'[1-9][0-9]{0,15}', file.stem)}


def profile(api, settings, actor):
    if not re.fullmatch(r'[1-9][0-9]{0,15}', str(actor)): return {}
    return control.operations.read(profiles_path(api, settings) / (str(actor) + '.json'))


def require_unblocked(api, settings, actor):
    if profile(api, settings, actor).get('blocked'):
        raise HTTPException(403, 'Пользователь заблокирован в Nodera')


def save_profile(api, settings, actor, value):
    if not re.fullmatch(r'[1-9][0-9]{0,15}', actor): raise HTTPException(422, 'Неверный Telegram ID')
    folder = profiles_path(api, settings)
    file = folder / (actor + '.json')
    if not file.exists() and sum(1 for _ in folder.glob('*.json')) >= 10000:
        raise HTTPException(429, 'Хранилище профилей заполнено')
    control.operations.atomic_json(file, value)


def merged_contact(contact, value):
    result = {**value.get('snapshot', {}), **contact}
    if 'birthdate' in value: result['birthdate'] = value['birthdate']
    result.update(profile_name=value.get('display_name', ''), blocked=bool(value.get('blocked')))
    return result


def edit_profile(api, identity, edit=None, *, delete=False):
    actor = str(identity)
    with control.operations.locked(api.DATA_DIR):
        settings = control._read(api)  # Storage lock is already held.
        if not settings.get('bot_token'): raise HTTPException(409, 'Бот не настроен')
        account = next((row for row in settings['accounts'] if row['telegram_id'] == identity), {})
        known = observed(api, settings)
        previous = profile(api, settings, actor)
        contact = next((row for row in known if row['telegram_id'] == identity), previous.get('snapshot', {}))
        if not contact and not account: raise HTTPException(404, 'Пользователь не найден')
        if delete:
            control.operations.atomic_json(path(api, settings), {'items': [row for row in known if row['telegram_id'] != identity]})
            (profiles_path(api, settings) / (actor + '.json')).unlink(missing_ok=True)
            return {'deleted': True}
        value = {**previous, 'snapshot': contact or {'telegram_id': identity, 'username':'', 'name': account.get('name',''), 'seen_at':0}}
        value.update(edit.model_dump(exclude_unset=True))
        if 'birthdate' in edit.model_fields_set:
            value['birthdate'] = edit.birthdate.model_dump() if edit.birthdate else None
        save_profile(api, settings, actor, value)
        return merged_contact(contact, value)


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
        known = {str(row['telegram_id']): row for row in observed(api, settings)}
        saved = profiles(api, settings)
        rows = [merged_contact(known.get(actor, {}), saved.get(actor, {})) for actor in known.keys() | saved.keys()]
        rows = sorted(rows, key=lambda row: row.get('seen_at', 0), reverse=True)
    return rows


def interlocutor(api, settings, actor):
    user = next((entry for entry in settings['accounts'] if str(entry['telegram_id']) == actor), {})
    with control.operations.locked(api.DATA_DIR):
        contact = next((row for row in observed(api, settings) if str(row['telegram_id']) == actor), {}) if actor != 'panel' and settings.get('bot_token') else {}
        telegram = contact
        saved = profile(api, settings, actor) if actor != 'panel' and settings.get('bot_token') else {}
        contact = merged_contact(contact, saved)
    return {'role': 'owner' if actor == 'panel' else roles.role(user), 'permissions': sorted(roles.effective(settings, actor)), 'products': ['assistant', 'mihomo'] if actor == 'panel' else sorted(roles.products(user)),
            'telegram_id': user.get('telegram_id'), 'name': user.get('address_as') or contact.get('profile_name') or contact.get('name') or user.get('name', ''),
            'address_gender': user.get('address_gender', 'auto'), 'mihomo_profile_id': user.get('mihomo_profile_id') if roles.allows(settings, actor, 'subscription_read') else None,
            'profile': {key: saved[key] for key in ('display_name', 'birthdate') if key in saved},
            'telegram': {key: telegram[key] for key in ('username', 'name', 'last_name', 'language_code', 'is_premium', 'bio', 'birthdate') if key in telegram}}


def remember(api, settings, contact, actor):
    if str(contact.telegram_id) != actor or contact.is_bot:
        raise HTTPException(403, 'Контакт не соответствует отправителю')
    user = next((a for a in settings['accounts'] if a['telegram_id'] == contact.telegram_id), {})
    access = {'admin': bool(roles.effective(settings, actor) & {'server_read', 'server_manage', 'diagnostics', 'logs_read', 'network_read', 'security_read', 'subscriptions_admin', 'roles_manage'}), 'chat': roles.allows(settings, actor, 'chat'), 'subscription': roles.allows(settings, actor, 'subscription_read')}
    import assistant_bot_views
    access.update(assistant_bot_views.capabilities(api, settings, actor))
    with control.operations.locked(api.DATA_DIR):
        saved = profile(api, settings, actor)
        if saved.get('blocked'): return {'admin':False, 'chat':False, 'subscription':False, 'blocked':True}
        access['onboarding'] = saved.get('onboarding', 'new')
        access['admin_dialog'] = bool(saved.get('admin_dialog')) and roles.allows(settings,actor,'subscriptions_admin')
        access['telegram_dialog'] = bool(saved.get('telegram_dialog', {}).get('expires', 0) > time.time()) and roles.allows(settings,actor,'telegram_read')
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


def profile_dialog(api, settings, actor, payload):
    if not re.fullmatch(r'[1-9][0-9]{0,15}', actor): raise HTTPException(403, 'Нужен отправитель Telegram')
    with control.operations.locked(api.DATA_DIR):
        require_unblocked(api, settings, actor)
        value = profile(api, settings, actor)
        receipt = value.get('receipts', {}).get(payload.request_id)
        fingerprint = hashlib.sha256(payload.text.encode()).hexdigest()
        if receipt:
            if receipt['fingerprint'] != fingerprint: raise HTTPException(409, 'ID сообщения уже использован')
            return receipt['output']
        known = next((row for row in observed(api, settings) if str(row['telegram_id']) == actor), {})
        if not known and not value: raise HTTPException(404, 'Сначала зарегистрируйте контакт бота')
        value = {**value, 'snapshot':known or value.get('snapshot', {})}
        text = payload.text.strip()
        command = text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
        step = value.get('onboarding', 'new')
        output = None
        if command == '/myprofile':
            contact = merged_contact(known, value)
            date = contact.get('birthdate')
            date_text = f"{date['day']:02}.{date['month']:02}" + (f".{date['year']}" if date.get('year') else '') if date else 'не указана'
            output = {'text': 'Мой профиль\nИмя: ' + (value.get('display_name') or contact.get('name') or 'не указано') + '\nДата рождения: ' + date_text}
        elif step in ('name','birthdate') and command.startswith('/') and command not in ('/start','/setup','/skip','/skipall'):
            value['onboarding']='done'
            save_profile(api,settings,actor,value)
            return {'text':None}
        elif command == '/setup' or command == '/start' and step in ('new', 'name', 'birthdate'):
            if command == '/setup' or step == 'new': step = 'name'
        elif command == '/start':
            return {'text':None} if roles.effective(settings, actor) else {'text':'Nodera\nВаш помощник в Telegram: управление подпиской и общение с ИИ при наличии доступа. Новые возможности появятся позже.', 'buttons':[[{'text':'Мой профиль','command':'/myprofile'},{'text':'Мой ID','command':'/id'}]]}
        elif command == '/skipall' and step in ('name', 'birthdate'):
            step = 'done'
        elif step == 'name' and (command == '/skip' or text and not command.startswith('/')):
            if command != '/skip':
                if len(text) > 80: output = {'text':'Имя — до 80 символов. Напишите короче или /skip.'}
                else: value['display_name'] = text
            if not output: step = 'birthdate'
        elif step == 'birthdate' and (command == '/skip' or text and not command.startswith('/')):
            if command != '/skip':
                match = re.fullmatch(r'(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?', text)
                try:
                    if not match: raise ValueError()
                    date = Birthdate(day=int(match[1]), month=int(match[2]), year=int(match[3]) if match[3] else None)
                    if date.year and datetime.date(date.year, date.month, date.day) > datetime.date.today(): raise ValueError()
                    value['birthdate'] = date.model_dump()
                except ValueError:
                    output = {'text':'Укажите существующую дату: ДД.ММ.ГГГГ или ДД.ММ без года.'}
            if not output: step = 'done'
        else:
            return {'text': None}
        value['onboarding'] = step
        if not output:
            output = {'text': ('Профиль Nodera · знакомство\nКак к вам обращаться? Напишите имя.\n' if step == 'name' else
                              'Профиль Nodera · дата рождения\nНапишите ДД.ММ.ГГГГ или ДД.ММ без года.\n' if step == 'birthdate' else
                              'Профиль Nodera сохранён.')}
            if command == '/start' and step == 'name':
                output['text'] = 'Nodera\nВаш помощник в Telegram: управление подпиской и общение с ИИ при наличии доступа. Новые возможности появятся позже.\n\nКак к вам обращаться? Напишите имя или нажмите «Пропустить».'
        output['buttons'] = [[{'text':'Изменить профиль','command':'/setup'}]] if command=='/myprofile' else [[{'text':'Пропустить','command':'/skip'}]] if step in ('name','birthdate') else [[{'text':'Мой профиль','command':'/myprofile'}]]
        receipts = dict(value.get('receipts', {})); receipts[payload.request_id] = {'fingerprint':fingerprint, 'output':output}
        value['receipts'] = dict(list(receipts.items())[-32:])
        save_profile(api, settings, actor, value)
        return output


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
