"""Owner-only durable notifications, independent of the dialogue/model workers."""
import asyncio
import hashlib
import json
import logging
import math
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

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
            'queue': [], 'seen': [], 'analysis': {}, 'boot_id': '', 'revision': '0' * 64, 'last_delivery': None, **value}


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
            value.update(queue=[], analysis={}, seen=[event_key(row) for row in rows][-1000:], boot_id=boot)
        value.update(settings=new, revision=hashlib.sha256(uuid.uuid4().bytes).hexdigest())
        operations.atomic_json(path(api), value)
    return status(api)


def enqueue(value, text, key, now, *, analyze=False):
    if any(row['key'] == key for row in value['queue']): return
    if len(value['queue']) >= 100:
        value['dropped'] = value.get('dropped', 0) + 1
        return
    row = {'key': key, 'text': text, 'at': now, 'due': now, 'state': 'pending'}
    if analyze:
        records = {key: item for key, item in value['analysis'].items() if item['expires'] > now}
        while len(records) >= 100: records.pop(next(iter(records)))
        token = uuid.uuid4().hex
        records[token] = {'text': text, 'owner_id': value['settings']['owner_id'], 'expires': now + 7 * 86400}
        value['analysis'] = records
        row['analysis_token'] = token
    value['queue'].append(row)


def analysis_keyboard(settings, row):
    if not row.get('analysis_token') or not all(settings.get('modules', {}).get(key) for key in ('nodera-bot', 'ai-agent')):
        return None
    token = row['analysis_token']
    return {'inline_keyboard': [[{'text': 'Разобрать с ИИ', 'callback_data': f'alert:{token}:brief'},
                                {'text': 'Глубокий разбор', 'callback_data': f'alert:{token}:deep'}]]}


def analysis_prompt(api, settings, actor, token, mode):
    """Resolve an opaque button against durable owner-scoped context, never Telegram message text."""
    if mode not in ('brief', 'deep') or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise HTTPException(422, 'Некорректная кнопка алерта')
    roles.require(settings, actor, 'chat')
    if str(actor) not in {str(key) for key in owners(settings)}:
        raise HTTPException(403, 'Разбор алерта доступен его владельцу')
    with operations.locked(api.DATA_DIR):
        value = load(api)
        record = value['analysis'].get(token)
        if (not value['settings']['enabled'] or str(value['settings']['owner_id']) != str(actor)
                or not record or str(record['owner_id']) != str(actor) or record['expires'] <= time.time()):
            raise HTTPException(404, 'Алерт недоступен или кнопка устарела. Откройте актуальные алерты в панели.')
        if time.time() - record.get('last_' + mode, 0) < 60:
            raise HTTPException(429, 'Разбор этого алерта уже запрошен. Дождитесь ответа; повтор доступен через минуту.')
        record['last_' + mode] = time.time()
        operations.atomic_json(path(api), value)
        text = record['text']
    task = ('Дай краткий разбор: что означает алерт, насколько срочно реагировать и первые 2–3 шага.' if mode == 'brief' else
            'Выполни глубокий разбор: собери доступные пассивные данные диагностики, HTTP-ошибок, состояния служб и изменений; '
            'сопоставь время и признаки, раздели подтверждённые факты и гипотезы, оцени влияние, предложи порядок проверки и исправления.')
    return (task + '\nЭто запрос анализа конкретного алерта владельцем. Не меняй настройки и не перезапускай службы; '
            'для исправлений потребуется отдельный запрос пользователя. Используй только чтение и разрешённые инструменты. '
            'Не запускай платные или активные проверки интеграций. Если данных или инструментов недостаточно, прямо скажи об этом. '
            'Событие ниже описывает прошлое состояние: проверь актуальность и возможное восстановление, не подменяй исторические показатели текущими.\n\n'
            'Контекст алерта:\n' + text)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def event_time(value):
    try:
        if not number(value): return 'время не сохранено'
        return datetime.fromtimestamp(value, timezone.utc).strftime('%d.%m.%Y %H:%M:%S UTC')
    except (ValueError, OverflowError, OSError): return 'время не сохранено'


def server_label():
    try: host = socket.gethostname()
    except OSError: return 'VPS'
    return 'VPS · ' + host if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', host) else 'VPS'


def format_alert(row):
    """Fixed actionable descriptions; no model calls, logs, URLs or arbitrary payload text."""
    recovered = row.get('transition') == 'recovered'
    rule = row.get('rule')
    component = row.get('component', 'server')
    if not isinstance(component, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', component): component = 'server'
    names = {'application': 'Панель управления / API', 'system': 'Система', 'server': 'Сервер',
             'api': 'API панели', 'web': 'Веб-интерфейс', 'gateway': 'Caddy', 'updates': 'Автообновление ОС',
             'ssh': 'SSH', 'fail2ban': 'Fail2ban', 'mihomo': 'Mihomo', 'ai-agent': 'ИИ-агент', 'nodera-bot': 'Telegram-бот'}
    details = row.get('details') if isinstance(row.get('details'), dict) else {}
    titles = {'http_error_rate': ('Много HTTP-ошибок', 'Доля HTTP-ошибок снизилась'),
              'disk_pressure': ('Заканчивается место на диске', 'Заполнение диска снизилось'),
              'service_error': ('Служба остановлена или завершилась с ошибкой', 'Служба снова в рабочем состоянии'),
              'local_health': ('Проверка компонента выявила проблему', 'Проверка компонента снова успешна')}
    title = titles.get(rule, ('Обнаружена проблема', 'Условие алерта больше не выполняется'))[int(recovered)]
    urgency = 'Информационно: срочных действий по этому алерту не требуется.' if recovered else (
        'Предупреждение: проверьте ошибки; это ещё не означает недоступность VPS.' if rule == 'http_error_rate'
        else 'Высокая: проверьте сейчас, возможны перебои в работе.' if rule in titles else 'Требует проверки в панели диагностики.')
    lines = [f'{server_label()} · {"Восстановление" if recovered else "Проблема"}', title,
             f'Где: {names.get(component, component)}', f'Время: {event_time(row.get("at"))}', f'Важность: {urgency}']
    if rule == 'http_error_rate':
        errors, requests, window, threshold = (details.get(key) for key in ('errors', 'requests', 'window_minutes', 'threshold_percent'))
        if all(number(value) for value in (errors, requests, window, threshold)) and requests > 0:
            lines.append(f'За {window:g} мин.: {errors:g} из {requests:g} запросов с HTTP 4xx/5xx ({errors / requests * 100:.1f}%). Порог: {threshold:g}%.')
        else: lines.append('Доля ответов HTTP 4xx/5xx ' + ('опустилась ниже настроенного порога.' if recovered else 'достигла настроенного порога. Точные показатели в этом событии не сохранены.'))
        codes = details.get('error_statuses')
        codes = {str(code): count for code, count in codes.items() if re.fullmatch(r'[45][0-9]{2}', str(code)) and type(count) is int and count > 0} if isinstance(codes, dict) else {}
        if codes:
            lines.append('Частые ответы: ' + ', '.join(f'{code} × {count}' for code, count in sorted(codes.items(), key=lambda item: -item[1])[:5]) + '.')
            if '422' in codes: lines.append('422: запрос не прошёл проверку формата или обязательных полей; проверьте отправителя запроса.')
        if not recovered:
            lines.append('Что делать: откройте историю HTTP-ошибок в панели, проверьте коды и повторяющиеся запросы. 4xx — отклонённые запросы; 5xx — ошибки обработки на сервере. Если панель или подписки недоступны — реагируйте сразу, проверьте службы и журналы.')
    elif rule == 'disk_pressure':
        used, threshold = details.get('used_percent'), details.get('threshold_percent')
        if number(used) and number(threshold): lines.append(f'Корневой диск занят на {used:g}%. Порог: {threshold:g}%.')
        else: lines.append('Занятое место на корневом диске ' + ('ниже настроенного порога.' if recovered else 'достигло настроенного порога.'))
        if not recovered: lines.append('Что делать: проверьте свободное место и крупные журналы/резервные копии. Освободите место, проверив назначение файлов: заполнение диска мешает записи конфигураций и работе служб.')
    elif rule == 'service_error':
        state = {'error': 'ошибка выполнения', 'stopped': 'остановлена', 'running': 'работает', 'applied': 'настройки применены',
                 'succeeded': 'задача выполнена', 'listening': 'ожидает подключения', 'waiting': 'ожидает запуска'}.get(details.get('state'))
        if state: lines.append('Состояние: ' + state + '.')
        if not recovered: lines.append('Что делать: в разделе служб проверьте состояние и последние журналы компонента. Если остановка не планировалась — устраните причину ошибки и запустите службу.')
    elif rule == 'local_health':
        lines.append('Локальная проверка компонента ' + ('теперь проходит.' if recovered else 'вернула критический статус. Точная причина требует просмотра результатов проверки.'))
        if not recovered: lines.append('Что делать: откройте проверки компонента в панели, посмотрите неуспешные пункты и состояние его служб. Если функция недоступна пользователям — реагируйте сразу.')
    elif not recovered: lines.append('Что делать: откройте диагностические алерты и состояние служб в панели; причина по этому сообщению не установлена.')
    samples = details.get('confirmation_samples')
    if type(samples) is int and samples > 0: lines.append(f'Подтверждено последовательными проверками: {samples}.')
    duration = row.get('observed_duration_s')
    if recovered and number(duration) and duration >= 0:
        lines.append(f'Наблюдаемая длительность проблемы: {int(duration) // 60} мин. {int(duration) % 60} сек.')
    if recovered: lines.append('Что делать: наблюдайте. Восстановление относится к этой проверке; если симптомы остались или алерт повторяется — проверьте журналы.')
    if isinstance(rule, str) and re.fullmatch(r'[a-z_]{1,64}', rule): lines.append(f'Код события: {rule}')
    return '\n'.join(lines)


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
                enqueue(value, format_alert(row), key, now, analyze=True)
        if boot and value['boot_id'] and boot != value['boot_id'] and config['boots']:
            enqueue(value, f'{server_label()} · Сервер снова запущен\nВремя обнаружения: {event_time(now)}\n'
                    'Обнаружена новая загрузка ОС.\nВажность: информационно, если перезагрузка планировалась.\n'
                    'Что делать: если перезагрузка неожиданная — проверьте журнал ОС и доступность служб. Это уведомление не подтверждает готовность всех компонентов.', 'boot:' + boot, now, analyze=True)
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
        enqueue(value, f'{server_label()} · Проверка уведомлений\nВремя: {event_time(now)}\n'
                'Тестовое сообщение. Реагировать не нужно: доставка уведомлений владельцу работает.', 'test:' + uuid.uuid4().hex, now)
        operations.atomic_json(path(api), value)
    return {'queued': True}


def send(token, recipient, text, reply_markup=None):
    payload = {'chat_id': recipient, 'text': text}
    if reply_markup: payload['reply_markup'] = reply_markup
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response: result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        try: result = json.loads(exc.read(65536))
        except (ValueError, OSError): return {'state': 'failed'}
    except (OSError, ValueError):
        # The request may have reached Telegram. Never retry an ambiguous delivery.
        return {'state': 'unknown'}
    if result.get('ok'):
        message = result.get('result') or {}
        return {'state': 'sent', 'message_id': message.get('message_id'), 'date': message.get('date')}
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
    markup = analysis_keyboard(settings, row) if roles.allows(settings, str(recipient), 'chat') else None
    result = send(settings['bot_token'], recipient, row['text'], reply_markup=markup)
    if result['state'] == 'sent' and type(result.get('message_id')) is int:
        try:
            import assistant_bot_history
            assistant_bot_history.append(api, settings, str(recipient), assistant_bot_history.Message(
                message_id=result['message_id'], date=result.get('date') or int(time.time()),
                role='assistant', text=row['text'], kind='alert'))
        except Exception: logging.warning('Owner alert history could not be saved')
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
