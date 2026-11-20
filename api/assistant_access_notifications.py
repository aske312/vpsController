"""Private, durable access-change notices; recipients need no remaining AI rights."""
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid

import application_operation as operations
import assistant_roles as roles

ROLE_NAMES = {'owner': 'Владелец', 'administrator': 'Администратор', 'moderator': 'Модератор',
              'user': 'Пользователь', 'guest': 'Гость'}
PERMISSION_NAMES = {
    'chat': 'Общение с ИИ', 'model_select': 'Выбор модели ИИ', 'web': 'Поиск в интернете',
    'memory': 'Личная память и персонализация', 'telegram_read': 'Работа с подключённым аккаунтом Telegram',
    'server_read': 'Просмотр сервера и служб', 'server_manage': 'Управление службами',
    'diagnostics': 'Диагностика приложения', 'logs_read': 'Чтение журналов', 'network_read': 'Просмотр сети',
    'security_read': 'Просмотр безопасности', 'terminal_read': 'Терминал в режиме чтения',
    'subscription_read': 'Получение своей подписки', 'subscription_manage': 'Редактирование своей подписки',
    'subscription_name': 'Изменение названия своей подписки', 'subscription_connections': 'Подключения своей подписки',
    'subscription_devices': 'Устройства своей подписки', 'subscription_routing': 'Маршрутизация своей подписки',
    'subscription_quota': 'Изменение квоты трафика', 'subscription_speed': 'Изменение скорости',
    'subscription_expiry': 'Изменение срока подписки', 'subscriptions_admin': 'Доступ ко всем подпискам',
    'subscriptions_create': 'Создание подписок', 'subscriptions_delete': 'Удаление подписок',
    'modules_manage': 'Управление модулями Mihomo', 'routing_manage': 'Общая маршрутизация Mihomo',
    'dns_manage': 'Общие настройки DNS', 'recovery': 'Восстановление Mihomo', 'roles_manage': 'Управление ролями и правами',
}
RETENTION = 7 * 86400
_delivery_guard = threading.Lock()


def path(api):
    return api.DATA_DIR / 'assistant' / 'access-notifications.json'


def bot_scope(settings):
    identity = str(settings.get('bot_token') or '').split(':', 1)[0]
    return hashlib.sha256(identity.encode()).hexdigest()[:24] if identity else ''


def snapshot(settings, identity):
    import assistant_telegram
    account = next((row for row in settings.get('accounts', []) if str(row['telegram_id']) == str(identity)), {})
    grants = roles.effective(settings, str(identity))
    telegram = 'telegram_read' in grants
    scope = {'targets': sorted(account.get('telegram_action_targets', [])),
             'excluded': sorted(account.get('excluded_dialogs', []))} if telegram else {}
    return {'role': roles.role(account), 'permissions': sorted(grants),
            'profile': account.get('mihomo_profile_id') if 'subscription_read' in grants else None,
            'telegram_tools': sorted(assistant_telegram.enabled(account)) if telegram else [],
            'telegram_scope': hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest() if telegram else None}


def load(api):
    file = path(api)
    if file.is_symlink() or file.exists() and file.stat().st_size > 8 * 1024 * 1024:
        raise operations.OperationConflict('Очередь уведомлений о правах недоступна')
    value = operations.read(path(api)) or {'queue': [], 'deliveries': []}
    if not isinstance(value.get('queue'), list) or not isinstance(value.get('deliveries'), list):
        raise operations.OperationConflict('Очередь уведомлений о правах повреждена')
    if len(value['queue']) > 1000 or any(not isinstance(row, dict)
        or not isinstance(row.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', row['id'])
        or type(row.get('recipient')) is not int or not 0 < row['recipient'] <= 2**52
        or row.get('state') not in ('pending', 'sending')
        or not isinstance(row.get('before'), dict) or not isinstance(row.get('after'), dict)
        for row in value['queue']):
        raise operations.OperationConflict('Запись уведомления о правах повреждена')
    return value


def enqueue_changes(api, before, after):
    """Called under the settings lock, only after configuration/restart succeeded."""
    identities = {row['telegram_id'] for row in [*before.get('accounts', []), *after.get('accounts', [])]}
    changes = [(identity, snapshot(before, identity), snapshot(after, identity)) for identity in sorted(identities)]
    changes = [row for row in changes if row[1] != row[2]]
    if not changes: return {'state': 'unchanged', 'queued': 0}
    scope = bot_scope(after)
    if not scope: return {'state': 'bot_not_configured', 'queued': 0}
    now = time.time()
    value = load(api)
    value['queue'] = [row for row in value['queue'] if now - row['created_at'] < RETENTION and row['bot'] == scope]
    queued = 0
    for identity, old, new in changes:
        pending = next((row for row in value['queue'] if row['recipient'] == identity and row['state'] == 'pending'), None)
        original = pending['before'] if pending else old
        if pending: value['queue'].remove(pending)
        if original == new: continue
        value['queue'].append({'id': uuid.uuid4().hex, 'recipient': identity, 'bot': scope,
            'before': original, 'after': new, 'state': 'pending', 'created_at': now, 'due': now})
        queued += 1
    if len(value['queue']) > 1000: raise operations.OperationConflict('Очередь уведомлений о правах заполнена')
    operations.atomic_json(path(api), value)
    return {'state': 'queued' if queued else 'unchanged', 'queued': queued}


def message(before, after, *, blocked=False):
    old, new = set(before['permissions']), set(after['permissions'])
    lines = ['Ваши права в Nodera изменены.']
    if before['role'] != after['role']:
        lines.append('Роль: ' + ROLE_NAMES[before['role']] + ' → ' + ROLE_NAMES[after['role']] + '.')
    else: lines.append('Роль: ' + ROLE_NAMES[after['role']] + '.')
    for title, grants in (('Добавлены разрешения', new - old), ('Сняты разрешения', old - new)):
        if grants: lines += ['', title + ':', *['• ' + PERMISSION_NAMES[key] for key in roles.PERMISSIONS if key in grants]]
    if before['profile'] != after['profile']:
        lines += ['', 'Назначенная подписка Mihomo: /subscription.' if after['profile'] else
                  'Подписка Mihomo больше не назначена.' if 'subscription_read' in new else 'Доступ к своей подписке Mihomo отозван.']
    if 'telegram_read' in new:
        import assistant_telegram
        for title, tools in (('Telegram — добавлены инструменты', set(after['telegram_tools']) - set(before['telegram_tools'])),
                             ('Telegram — сняты инструменты', set(before['telegram_tools']) - set(after['telegram_tools']))):
            if tools:
                labels = [assistant_telegram.TOOLS[name]['title'] for name in sorted(tools) if name in assistant_telegram.TOOLS]
                lines += ['', title + ': ' + '; '.join(labels[:5]) + (f'; ещё {len(labels) - 5}' if len(labels) > 5 else '') + '.']
        if before['telegram_scope'] != after['telegram_scope']:
            lines += ['', 'Изменён список разрешённых диалогов подключённого аккаунта Telegram.']
    if blocked:
        lines += ['', 'Доступ к боту заблокирован администратором. Команды пока недоступны.']
    elif new:
        lines += ['', 'Теперь вам разрешено:', *['• ' + PERMISSION_NAMES[key] for key in roles.PERMISSIONS if key in new],
                  '', '/myprofile — ваш профиль; /id — ваш Telegram ID.']
    else:
        lines += ['', 'Административные команды, чат ИИ и управление подписками недоступны.',
                  'Остаются /myprofile — ваш профиль и /id — ваш Telegram ID.']
    text = '\n'.join(lines)
    return text if len(text) <= 4000 else text[:3998] + '…'


def record_delivery(value, row, state):
    value['deliveries'] = [*value['deliveries'], {'id': row['id'], 'recipient': row['recipient'],
                                               'state': state, 'at': time.time()}][-100:]


def _deliver(api):
    import assistant_control as control
    import assistant_contacts as contacts
    import telegram_alerts
    with operations.locked(api.DATA_DIR):
        value = load(api)
        if not value['queue']: return
        settings = control._read(api)
        now = time.time()
        # The dedicated delivery lease proves no other sender still owns these claims.
        retained = []
        for row in value['queue']:
            if row['state'] == 'sending': record_delivery(value, row, 'unknown')
            elif row['bot'] != bot_scope(settings) or now - row['created_at'] >= RETENTION: record_delivery(value, row, 'expired')
            else: retained.append(row)
        changed = len(retained) != len(value['queue'])
        value['queue'] = retained
        if changed: operations.atomic_json(path(api), value)
        if not settings.get('bot_token') or not settings.get('modules', {}).get('nodera-bot') or now < value.get('next_delivery', 0): return
        known = {row['telegram_id'] for row in contacts.observed(api, settings)}
        def contacted(recipient):
            if recipient in known: return True
            profile = contacts.profile(api, settings, str(recipient))
            seen_at = profile.get('snapshot', {}).get('seen_at')
            return type(seen_at) in (int, float) and seen_at > 0
        row = next((row for row in retained if row['due'] <= now and contacted(row['recipient'])), None)
        if not row: return  # Telegram cannot initiate a private conversation; wait for /start.
        row['after'] = snapshot(settings, row['recipient'])
        if row['before'] == row['after']:
            value['queue'].remove(row)
            operations.atomic_json(path(api), value)
            return
        profile = contacts.profile(api, settings, str(row['recipient']))
        text = message(row['before'], row['after'], blocked=bool(profile.get('blocked')))
        row['state'] = 'sending'
        value['next_delivery'] = now + 3
        operations.atomic_json(path(api), value)
    # No ACL check here: the notice must also reach a removed account or a guest.
    result = telegram_alerts.send(settings['bot_token'], row['recipient'], text)
    with operations.locked(api.DATA_DIR):
        value = load(api)
        current = next((item for item in value['queue'] if item['id'] == row['id']), None)
        if current:
            if result['state'] == 'pending':
                due = time.time() + result['retry_after']
                newer = next((item for item in value['queue'] if item['id'] != current['id']
                              and item['recipient'] == current['recipient'] and item['state'] == 'pending'), None)
                if newer:
                    newer.update(before=current['before'], due=due)
                    value['queue'].remove(current)
                    if newer['before'] == newer['after']: value['queue'].remove(newer)
                else: current.update(state='pending', due=due)
                value['next_delivery'] = due
            else:
                record_delivery(value, current, result['state'])
                value['queue'].remove(current)
            operations.atomic_json(path(api), value)


def deliver(api):
    if not path(api).exists() or not _delivery_guard.acquire(blocking=False): return
    try:
        lease = path(api).with_suffix('.delivery.lock')
        with lease.open('a+b') as lock:
            os.chmod(lease, 0o600)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            _deliver(api)  # Closing the descriptor releases the cross-process lease.
    finally:
        _delivery_guard.release()


async def monitor(api):
    while True:
        delay = 3
        try: await asyncio.to_thread(deliver, api)
        except Exception:
            logging.getLogger(__name__).warning('Access-change notice delivery unavailable')
            delay = 30
        await asyncio.sleep(delay)
