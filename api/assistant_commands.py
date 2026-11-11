"""Explicit server commands for bot administrators, independent of AI/MCP."""
import hmac
import re
import secrets
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

import assistant_control as control


class Command(BaseModel):
    model_config = {'extra': 'forbid'}
    command: str = Field(min_length=1, max_length=256)


HELP = ('Команды сервера:\n'
        '/status — сервер и время работы\n'
        '/resources — CPU, память, диск и трафик\n'
        '/services — службы, их ID и доступные действия\n'
        '/components — установленные компоненты\n'
        '/service <ID> <start|stop|restart> — управление службой\n'
        '/confirm <код> — подтвердить действие в течение 60 секунд\n'
        '/cancel — отменить ожидающее подтверждение\n'
        '/operation <ID> — результат фоновой операции\n'
        '/id — ваш Telegram ID\n\n'
        'Пример: /service mihomo restart. Остановка или перезапуск прерывает соединения этой службы.')

BLOCKED_COMMANDS = {'/poweroff', '/shutdown', '/halt', '/access-mode', '/panel-access', '/protected-access'}
BLOCKED_SERVICES = {'poweroff', 'shutdown', 'halt', 'access-mode', 'panel-access', 'protected-access'}
BLOCKED_UNITS = {'poweroff.target', 'halt.target', 'shutdown.target', 'systemd-poweroff.service', 'systemd-halt.service'}


def number(value, suffix=''):
    return f'{value:g}{suffix}' if type(value) in (int, float) else 'нет данных'


def size(value):
    return number(round(value / 1024**3, 2), ' GiB') if type(value) in (int, float) else 'нет данных'


def used(total, available):
    if type(total) not in (int, float) or type(available) not in (int, float) or total <= 0:
        return 'нет данных'
    return f'{size(total - available)} / {size(total)} ({(total - available) / total:.0%})'


def controls(identity, definition):
    # Keep the bot's own transport and the SSH recovery path available.
    if identity == 'nodera-bot' or identity in BLOCKED_SERVICES or definition.get('unit') in BLOCKED_UNITS:
        return []
    return [action for action in definition.get('controls', [])
            if action in ('start', 'stop', 'restart') and not (identity == 'ssh' and action == 'stop')]


def operation_text(value):
    states = {'queued': 'в очереди', 'running': 'выполняется', 'succeeded': 'выполнено',
              'failed': 'ошибка', 'cancelled': 'отменено', 'unknown': 'состояние не подтверждено'}
    return f"Операция {value['id']}\n{states.get(value.get('state'), 'состояние не подтверждено')}\n/operation {value['id']}"


def pending_path(api, actor):
    if not re.fullmatch(r'[1-9][0-9]{0,15}', actor):
        raise HTTPException(403, 'Нужен числовой ID администратора бота')
    return control.directory(api) / 'nodera-bot' / 'commands' / (actor + '.json')


def execute(api, actor, prompt):
    parts = prompt.strip().split()
    command = parts[0].split('@')[0].lower() if parts else ''
    args = parts[1:]
    if command in BLOCKED_COMMANDS:
        raise HTTPException(403, 'Nodera не может выключать VPS или переключать режим доступа к панели')
    if command == '/models' and (not args or len(args) == 1 and args[0].isdigit()):
        return control.bot_models(api, actor, page=int(args[0]) if args else 1)
    if command == '/model' and len(args) <= 1:
        return control.bot_models(api, actor, model=args[0] if args else None, page=None)
    if command == '/help' and not args:
        return HELP + '\n/models [страница] — модели AI\n/model <ID> — выбрать модель'
    if command in ('/status', '/resources') and not args:
        data = api.overview(None)
        server, metrics = data['server'], data['resources']
        if command == '/status':
            uptime = metrics.get('uptime_s')
            duration = f'{int(uptime // 86400)} д {int(uptime % 86400 // 3600)} ч {int(uptime % 3600 // 60)} мин' if type(uptime) in (int, float) else 'нет данных'
            return f"Сервер: {server.get('name', '—')}\nАдрес: {server.get('public_endpoint') or server.get('public_ip') or '—'}\nВремя работы: {duration}\nCPU: {number(metrics.get('cpu_percent'), '%')}\n/help — все команды"
        return '\n'.join([
            'Ресурсы сервера',
            'CPU: ' + number(metrics.get('cpu_percent'), '%') + ' · ядер: ' + number(metrics.get('cpu_count')),
            'Load 1/5/15: ' + ' / '.join(number(metrics.get('load' + str(period))) for period in (1, 5, 15)),
            'RAM: ' + used(metrics.get('memory_total'), metrics.get('memory_available')),
            'Диск: ' + used(metrics.get('disk_total'), metrics.get('disk_available')),
            'Трафик с запуска: ↓ ' + size(metrics.get('network_rx')) + ' · ↑ ' + size(metrics.get('network_tx'))])
    if command == '/services' and not args:
        rows = ['Службы (ID · состояние · действия):']
        for identity, definition in api.managed_services().items():
            observation = api.observe_service(definition['unit'])
            state = observation.get('runtime', {}).get('state') or observation.get('state') or 'unknown'
            rows.append(f"{identity} · {state} · {', '.join(controls(identity, definition)) or 'только просмотр'}")
        return '\n'.join(rows) + '\n\n/service <ID> <start|stop|restart>'
    if command == '/components' and not args:
        items = api.protocol_images(None)['items']
        return 'Установленные компоненты:\n' + ('\n'.join(f"{item['id']} · {item.get('name', item['id'])} · {item.get('installed_version') or 'версия неизвестна'}" for item in items if item.get('installed')) or 'Нет установленных компонентов')
    if command == '/operation' and len(args) == 1 and re.fullmatch(r'[0-9a-f]{32}', args[0]):
        # Send only the result, never worker output, input payloads or log paths.
        return operation_text(api.get_application_operation(args[0], None))
    if command == '/service' and len(args) == 2:
        identity, action = args
        definition = api.managed_services().get(identity)
        if not definition or action not in controls(identity, definition):
            raise HTTPException(409, 'Действие недоступно. Допустимые ID и действия: /services')
        api.service_action_definition(identity, api.ServiceAction(action=action))
        record = {'service': identity, 'action': action, 'code': secrets.token_hex(4),
                  'operation_id': uuid.uuid4().hex, 'expires': time.time() + 60,
                  'revision': control.read(api)['revision'], 'submitted': False}
        with control.operations.locked(api.DATA_DIR):
            path = pending_path(api, actor)
            previous = control.operations.read(path)
            if previous.get('submitted') and previous.get('expires', 0) > time.time() and not control.operations.read(api.DATA_DIR / 'operations' / (previous['operation_id'] + '.json')):
                raise HTTPException(409, 'Предыдущая команда отправляется. Повторите её /confirm или проверьте панель')
            control.operations.atomic_json(path, record)
        return f"Подтвердите {action} для {identity}. Действие может прервать соединения службы.\n/confirm {record['code']}\nКод действует 60 секунд и только для вас. /cancel — отмена.\nОперация: {record['operation_id']}"
    if command in ('/confirm', '/cancel') and len(args) == (1 if command == '/confirm' else 0):
        with control.operations.locked(api.DATA_DIR):
            path = pending_path(api, actor)
            record = control.operations.read(path)
            if not record:
                raise HTTPException(409, 'Нет ожидающей команды. /help — справка')
            if command == '/cancel':
                if record.get('submitted'):
                    raise HTTPException(409, 'Команда уже отправлена. /operation ' + record['operation_id'])
                path.unlink()
                return 'Ожидающая команда отменена.'
            if not re.fullmatch(r'[0-9a-f]{8}', args[0]) or not hmac.compare_digest(args[0], record['code']):
                raise HTTPException(409, 'Неверный код подтверждения')
            existing = control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'))
            if not existing and (time.time() > record['expires'] or control._read(api)['revision'] != record['revision']):
                path.unlink()
                raise HTTPException(409, 'Подтверждение истекло или права изменились. Отправьте /service заново')
            if not existing:
                definition = api.managed_services().get(record['service'], {})
                if record['action'] not in controls(record['service'], definition):
                    path.unlink()
                    raise HTTPException(403, 'Действие больше недоступно через Nodera')
            record['submitted'] = True
            control.operations.atomic_json(path, record)
        # Use the panel's durable, idempotent service worker and admission lock.
        # Never hold the file lock across admission, which acquires it itself.
        try:
            result = api.manage_service(record['service'], api.ServiceAction(action=record['action']), None, record['operation_id'])
        except HTTPException:
            with control.operations.locked(api.DATA_DIR):
                current = control.operations.read(path)
                if current.get('operation_id') == record['operation_id'] and not control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json')):
                    record['submitted'] = False
                    control.operations.atomic_json(path, record)
            raise
        return operation_text(result)
    return 'Неизвестная команда или неверные аргументы.\n' + HELP
