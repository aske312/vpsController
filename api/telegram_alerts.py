"""Owner-only durable notifications, independent of the dialogue/model workers."""
import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool

import application_operation as operations
import assistant_control as control
import assistant_roles as roles


class Settings(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool = False
    owner_id: int | None = Field(default=None, strict=True, gt=0, le=2**52)
    failures: StrictBool = True
    recoveries: StrictBool = True
    boots: StrictBool = True
    expected_revision: str = Field(min_length=64, max_length=64)


def path(api): return api.DATA_DIR / 'telegram-alerts.json'


def load(api):
    value = operations.read(path(api))
    return {'settings': {'enabled': False, 'owner_id': None, 'failures': True, 'recoveries': True, 'boots': True},
            'queue': [], 'seen': [], 'boot_id': '', 'revision': '0' * 64, 'last_delivery': None, **value}


def owners(settings):
    return {row['telegram_id']: row for row in settings.get('accounts', []) if roles.role(row) == 'owner'}


def status(api):
    settings = control.read(api)
    with operations.locked(api.DATA_DIR): value = load(api)
    return {'settings': value['settings'], 'revision': value['revision'],
            'owners': [{'telegram_id': key, 'name': row.get('name', '')} for key, row in owners(settings).items()],
            'token_configured': bool(settings.get('bot_token')), 'pending': len(value['queue']),
            'last_delivery': value['last_delivery'], 'dropped': value.get('dropped', 0)}


def event_key(row):
    return hashlib.sha256(json.dumps([row.get(key) for key in ('id', 'transition', 'at')]).encode()).hexdigest()


def history(api):
    import diagnostic_history
    return diagnostic_history.load(api).get('history', [])


def configure(api, payload):
    settings = control.read(api)
    if payload.enabled and (payload.owner_id not in owners(settings) or not settings.get('bot_token')):
        raise HTTPException(422, 'Выберите сохранённого владельца и настройте токен Telegram-бота')
    rows, boot = history(api), operations.boot_id()
    with operations.locked(api.DATA_DIR):
        value = load(api)
        if value['revision'] != payload.expected_revision: raise HTTPException(409, 'Настройки алертов изменились; обновите раздел')
        new = payload.model_dump(exclude={'expected_revision'})
        # Enabling and changing recipient start a fresh baseline, never replay history.
        if not new['enabled'] or not value['settings']['enabled'] or new['owner_id'] != value['settings']['owner_id']:
            value.update(queue=[], seen=[event_key(row) for row in rows][-1000:], boot_id=boot)
        value.update(settings=new, revision=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        operations.atomic_json(path(api), value)
    return status(api)


def enqueue(value, text, key, now):
    if any(row['key'] == key for row in value['queue']): return
    if len(value['queue']) >= 100:
        value['dropped'] = value.get('dropped', 0) + 1
        return
    value['queue'].append({'key': key, 'text': text, 'at': now, 'due': now, 'state': 'pending'})


def sample(api):
    rows, boot, now = history(api), operations.boot_id(), time.time()
    with operations.locked(api.DATA_DIR):
        value = load(api)
        config = value['settings']
        if not config['enabled']: return
        seen = set(value['seen'])
        for row in rows:
            key = event_key(row)
            if key in seen: continue
            seen.add(key)
            category = 'failures' if row.get('transition') == 'firing' else 'recoveries'
            if config[category]:
                # Only fixed diagnostic metadata, never log contents or credentials.
                component = str(row.get('component', 'server'))[:80]
                rule = str(row.get('rule', 'diagnostic'))[:80]
                title = 'Сбой' if category == 'failures' else 'Восстановление'
                enqueue(value, f'VPS · {title}\n{component}: {rule}\n{row.get("at", "")}', key, now)
        if boot and value['boot_id'] and boot != value['boot_id'] and config['boots']:
            enqueue(value, 'VPS · Сервер снова запущен\nОбнаружена новая загрузка ОС.', 'boot:' + boot, now)
        value['seen'] = list(dict.fromkeys([*value['seen'], *[event_key(row) for row in rows]]))[-1000:]
        value['boot_id'] = boot
        operations.atomic_json(path(api), value)


def test(api):
    settings = control.read(api)
    with operations.locked(api.DATA_DIR):
        value = load(api)
        if not value['settings']['enabled'] or value['settings']['owner_id'] not in owners(settings):
            raise HTTPException(422, 'Сначала включите алерты для сохранённого владельца')
        now = time.time()
        if now - value.get('last_test', 0) < 60: raise HTTPException(429, 'Тест доступен раз в минуту')
        value['last_test'] = now
        enqueue(value, 'VPS · Проверка уведомлений\nДоставка владельцу настроена.', 'test:' + uuid.uuid4().hex, now)
        operations.atomic_json(path(api), value)
    return {'queued': True}


def send(token, recipient, text):
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps({'chat_id': recipient, 'text': text}).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response: result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        try: result = json.loads(exc.read(65536))
        except (ValueError, OSError): return {'state': 'failed'}
    except (OSError, ValueError):
        # The request may have reached Telegram. Never retry an ambiguous delivery.
        return {'state': 'unknown'}
    if result.get('ok'): return {'state': 'sent'}
    retry = result.get('parameters', {}).get('retry_after')
    if result.get('error_code') == 429 and type(retry) is int:
        return {'state': 'pending', 'retry_after': max(1, min(retry, 86400))}
    return {'state': 'failed'}


def deliver(api):
    if not path(api).exists(): return
    settings, now = control.read(api), time.time()
    with operations.locked(api.DATA_DIR):
        value = load(api)
        # A crash after claiming has unknown outcome and must not cause replay.
        stranded = [row for row in value['queue'] if row['state'] == 'sending']
        if stranded:
            value['last_delivery'] = {'state': 'unknown', 'at': now}
            value['queue'] = [row for row in value['queue'] if row['state'] != 'sending']
        config = value['settings']
        if not config['enabled'] or config['owner_id'] not in owners(settings):
            if not value['queue'] and not stranded: return
            value['queue'] = []
            operations.atomic_json(path(api), value)
            return
        row = next((row for row in value['queue'] if row['due'] <= now), None)
        if not row or now < value.get('next_delivery', 0):
            if stranded: operations.atomic_json(path(api), value)
            return
        if not settings.get('bot_token'):
            value['last_delivery'] = {'state': 'token_missing', 'at': now}
            operations.atomic_json(path(api), value)
            return
        row['state'] = 'sending'
        revision, recipient = value['revision'], config['owner_id']
        value['next_delivery'] = now + 10
        operations.atomic_json(path(api), value)
    result = send(settings['bot_token'], recipient, row['text'])
    with operations.locked(api.DATA_DIR):
        value = load(api)
        if revision != value['revision']: return
        current = next((item for item in value['queue'] if item['key'] == row['key']), None)
        if current is None: return
        value['last_delivery'] = {'state': result['state'], 'at': time.time()}
        if result['state'] == 'pending':
            current.update(state='pending', due=time.time() + result['retry_after'])
            value['next_delivery'] = current['due']
        else: value['queue'].remove(current)
        operations.atomic_json(path(api), value)


async def monitor(api):
    while True:
        try: await asyncio.to_thread(deliver, api)
        except Exception: api.logger.warning('Owner alert delivery unavailable')
        await asyncio.sleep(5)
