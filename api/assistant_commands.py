"""Explicit server commands for bot administrators, independent of AI/MCP."""
import hmac
import json
import re
import secrets
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field

import assistant_control as control
import assistant_roles as roles

source_request = ContextVar('assistant_command_source_request', default=None)


class Command(BaseModel):
    model_config = {'extra': 'forbid'}
    command: str = Field(min_length=1, max_length=256)
    request_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


COMMAND_PERMISSIONS = {'/subscription': 'subscription_read', '/profile': 'subscription_read', '/profiles': 'subscription_read',
    '/devices': 'subscription_read', '/traffic': 'subscription_read', '/connect': 'subscription_read', '/apps': 'subscription_read', '/models': 'model_select',
    '/model': 'model_select', '/status': 'server_read', '/resources': 'server_read', '/services': 'server_read',
    '/components': 'server_read', '/service': 'server_manage', '/network': 'network_read', '/security': 'security_read',
    '/diagnostics': 'diagnostics', '/logs': 'logs_read', '/ai': 'chat'}


HELP = ('Команды сервера:\n'
        '/status — сервер и время работы\n'
        '/resources — CPU, память, диск и трафик\n'
        '/services — службы, их ID и доступные действия\n'
        '/components — установленные компоненты\n'
        '/network — состояние сети\n'
        '/security — состояние безопасности\n'
        '/logs [служба] — последние логи\n'
        '/diagnostics — пассивная диагностика\n'
        '/profiles — доступные профили\n'
        '/profile [ID] — параметры подписки\n'
        '/devices [ID] — устройства подписки\n'
        '/traffic [ID] — учтённый трафик подписки\n'
        '/service <ID> <start|stop|restart> — управление службой\n'
        '/confirm <код> — подтвердить действие в течение 10 минут\n'
        '/cancel [код] — отменить ожидающее подтверждение\n'
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


def pending_records(api, actor):
    if actor != 'panel' and not re.fullmatch(r'[1-9][0-9]{0,15}', actor):
        raise HTTPException(403, 'Нужен числовой ID администратора бота')
    root = control.directory(api) / 'nodera-bot' / 'commands'
    paths = [root / (actor + '.json')]
    folder = root / actor
    if folder.exists(): paths += sorted(folder.glob('????????.json'))
    records = []
    for path in paths:
        record = control.operations.read(path)
        if record and re.fullmatch(r'[0-9a-f]{8}', str(record.get('code', ''))):
            records.append((path, record))
    return sorted(records, key=lambda row: (row[1].get('created_at', 0), row[1].get('expires', 0), row[1]['code']))


def pending_path(api, actor, code=None, operation_id=None):
    records = pending_records(api, actor)
    if code is not None:
        if not re.fullmatch(r'[0-9a-f]{8}', code): raise HTTPException(409, 'Неверный код подтверждения')
        return next((path for path, record in records if record['code'] == code),
                    control.directory(api) / 'nodera-bot' / 'commands' / actor / (code + '.json'))
    if operation_id is not None:
        return next((path for path, record in records if record.get('operation_id') == operation_id),
                    control.directory(api) / 'nodera-bot' / 'commands' / (actor + '.json'))
    return records[-1][0] if records else control.directory(api) / 'nodera-bot' / 'commands' / (actor + '.json')


def enqueue(api, actor, record):
    """Called with the application lock held. Each request keeps its own identity."""
    identity = source_request.get()
    if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}', identity):
        import assistant_jobs
        job = control.operations.read(assistant_jobs.path(api, identity))
        if job.get('actor') == actor and job.get('cancel_requested'):
            raise HTTPException(409, 'Запрос ИИ остановлен; подготовка новых операций запрещена')
    rows = pending_records(api, actor)
    now = time.time()
    active = []
    for path, prior in rows:
        if not prior.get('submitted') and prior.get('expires', 0) <= now:
            path.unlink(missing_ok=True)
            continue
        if prior.get('submitted') and not prior.get('completed_text') and prior.get('kind') == record.get('kind') and prior.get('service') == record.get('service'):
            operation = control.operations.read(api.DATA_DIR / 'operations' / (prior['operation_id'] + '.json'))
            if operation.get('state') not in ('succeeded', 'failed', 'cancelled'):
                raise HTTPException(409, 'Результат предыдущего действия неизвестен; сначала проверьте его операцию')
        active.append((path, prior))
    if sum(not prior.get('submitted') for _, prior in active) >= 32:
        raise HTTPException(409, 'В очереди уже 32 подтверждения. Примите решения по ожидающим действиям')
    # Bound retained operation ownership without evicting unconfirmed requests.
    completed = []
    for path, prior in active:
        if prior.get('submitted'):
            operation = control.operations.read(api.DATA_DIR / 'operations' / (prior['operation_id'] + '.json'))
            if operation.get('state') in ('succeeded', 'failed', 'cancelled') or prior.get('completed_text') and not operation:
                completed.append((path, prior))
    removed = completed[:-64]
    for path, _ in removed: path.unlink(missing_ok=True)
    if len(active) - len(removed) >= 128:
        raise HTTPException(409, 'Слишком много сохранённых запросов; проверьте неизвестные результаты операций')
    record['created_at'] = now
    identity = source_request.get()
    if isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}', identity):
        record['source_request_id'] = identity
    record['expires'] = now + 600
    target = control.directory(api) / 'nodera-bot' / 'commands' / actor / (record['code'] + '.json')
    if any(prior['code'] == record['code'] for _, prior in rows): raise HTTPException(409, 'Повтор идентификатора подтверждения; подготовьте запрос заново')
    control.operations.atomic_json(target, record)
    return target


def profile_reply(api, settings, actor, command, args):
    import assistant_mihomo
    # This bridge filters profiles by the actor's current permissions before
    # formatting. Never return credentials, HWIDs or a foreign subscriber's data.
    items = assistant_mihomo.profiles(api, actor)['items']
    if command == '/profiles':
        rows = [f"{item['id']} · {str(item.get('name') or item['id'])[:100]}" for item in items[:40]]
        return 'Доступные профили:\n' + ('\n'.join(rows) or 'Нет доступных профилей') + '\n/profile <ID> — параметры профиля'
    linked = assistant_mihomo.profile_for(settings, actor).get('mihomo_profile_id')
    identity = args[0] if args else linked
    item = next((row for row in items if row['id'] == identity), None)
    if not item:
        return 'Профиль не найден или недоступен. /profiles — доступные профили; /profile <ID> — параметры. Если профиль не привязан, обратитесь к владельцу.'
    name = str(item.get('name') or item['id'])[:100]
    if command == '/devices':
        devices = item.get('devices', [])
        rows = [f"{str(device.get('name') or device['id'])[:100]} · {device.get('os') or 'ОС не определена'}" for device in devices[:40] if device.get('scope') != 'common' and device.get('id') != item.get('common_device_id')]
        return f'Устройства · {name}:\n' + ('\n'.join(rows) or 'Отдельные устройства ещё не зарегистрированы. Общие подключения доступны по подписке.')
    status, limits = item.get('limits_status') or {}, item.get('limits') or {}
    states = {'active': 'активна', 'expired': 'срок истёк', 'exhausted': 'трафик исчерпан', 'unknown': 'состояние ограничений не подтверждено'}
    used_bytes = status.get('used_bytes')
    traffic = f'{used_bytes / 1000000000:.2f} GB' if type(used_bytes) in (int, float) else 'нет данных'
    quota = number(limits['traffic_gb'], ' GB') if limits.get('traffic_gb') is not None else 'без ограничения'
    rows = [f'Подписка · {name}', 'Состояние: ' + states.get(status.get('state'), 'нет данных'), f'Учтённый трафик: {traffic} / {quota}']
    if command == '/profile':
        expires = status.get('expires_at')
        expiry = datetime.fromtimestamp(expires, timezone.utc).strftime('%d.%m.%Y %H:%M UTC') if type(expires) in (int, float) else 'не определён' if limits.get('days') is not None else 'без ограничения'
        rows += ['Срок: ' + expiry, 'Лимит скорости: ' + (number(limits['speed_mbps'], ' Мбит/с') if limits.get('speed_mbps') is not None else 'без ограничения'),
                 f"Подключений: {len(item.get('connections', []))}", '/devices — устройства · /subscription — ссылка']
    return '\n'.join(rows)


def pending_actions(api, actor, *, settings=None):
    settings = control.read(api) if settings is None else settings
    result = []
    for _, record in pending_records(api, actor):
        if record.get('submitted') or record.get('expires', 0) <= time.time() or record.get('revision') != settings['revision']: continue
        row = {key: record[key] for key in ('service', 'action', 'code', 'expires', 'operation_id')}
        row.update(actor=actor, actor_name='Администратор' if actor == 'panel' else next((a.get('name') or actor for a in settings.get('accounts', []) if str(a['telegram_id']) == actor), actor))
        if record.get('source_request_id'):
            row['source_request_id'] = record['source_request_id']
            row['created_at'] = record['created_at']
        if 'body' in record:
            import assistant_mihomo
            row['changes'] = assistant_mihomo.sanitize(record['body'])
        if record.get('confirmation_request'):
            row['confirmation_request'] = {key: record['confirmation_request'][key] for key in ('title', 'reason')}
        row['has_changes'] = 'body' in record
        result.append(row)
    import assistant_confirmations
    return assistant_confirmations.annotate(api, actor, result, settings)


def pending_action(api, actor):
    rows = pending_actions(api, actor)
    return rows[-1] if rows else None


def execute(api, actor, prompt, *, source='bot'):
    parts = prompt.strip().split()
    command = parts[0].split('@')[0].lower() if parts else ''
    args = parts[1:]
    settings = control.read(api)
    permission = COMMAND_PERMISSIONS.get(command)
    if permission: roles.require(settings, actor, permission)
    if command in ('/id', '/userid') and not args:
        return 'Веб-чат администратора. Telegram ID не выбран.' if actor == 'panel' else 'Telegram ID: ' + actor
    if command == '/apps' and len(args) <= 1:
        import connection_apps
        return connection_apps.guide(args[0].lower() if args else None)
    if command == '/connect' and not args:
        return 'Подключение:\n1. Выберите приложение: /apps. Для Windows рекомендуем наш Nodera: https://github.com/aske312/NoderaClient.\n2. Получите ссылку: /subscription.\n3. Добавьте подписку по URL в приложении (в Nodera нажмите «+»).\n4. Выберите сервер и включите подключение.\nПосле изменения настроек обновите подписку в клиенте.'
    if command in ('/confirm', '/operation'):
        pending = control.operations.read(pending_path(api, actor, code=args[0] if command == '/confirm' and args else None,
                                                       operation_id=args[0] if command == '/operation' and args else None))
        if command == '/operation' and not roles.allows(settings, actor, 'server_read') and args != [pending.get('operation_id')]:
            raise HTTPException(403, 'Доступен только результат собственной операции')
        if pending.get('kind') == 'roles': roles.require(settings, actor, 'roles_manage')
        elif pending.get('kind') == 'telegram': roles.require(settings, actor, 'telegram_read')
        elif pending.get('kind') == 'mihomo':
            if command == '/confirm':
                import assistant_mihomo
                assistant_mihomo.authorize(settings,actor,pending['method'],pending['path'],pending['body'])
            else: roles.require(settings, actor, 'subscription_read')
        else: roles.require(settings, actor, 'server_manage' if command == '/confirm' else 'server_read')
    if command == '/subscription' and len(args) <= 1:
        import assistant_mihomo
        linked = args[0] if args else assistant_mihomo.profile_for(settings, actor).get('mihomo_profile_id')
        if not linked: return 'К учётной записи не привязана подписка. Обратитесь к владельцу.'
        if not re.fullmatch(assistant_mihomo.ID, linked): raise HTTPException(422, 'Укажите ID профиля из /profiles')
        result = assistant_mihomo.call(api, actor, 'GET', '/profiles/' + linked + '/subscription', {})
        return 'Ваша подписка:\n' + result['url'] if result.get('url') else 'Подписка привязана, но публичная ссылка ещё не настроена. Обратитесь к владельцу.'
    if command in ('/profile', '/devices', '/traffic', '/profiles') and len(args) <= (0 if command == '/profiles' else 1):
        return profile_reply(api, settings, actor, command, args)
    if command in ('/network', '/security', '/diagnostics', '/logs') and (not args or command == '/logs' and len(args) == 1):
        import assistant_diagnostics
        arguments = {'service': args[0] if args else 'application', 'lines': 40} if command == '/logs' else {}
        result = assistant_diagnostics.call(api, {'/network': 'vps_network', '/security': 'vps_security', '/diagnostics': 'vps_diagnostics', '/logs': 'vps_logs'}[command], arguments, actor)
        titles = {'/network': 'Состояние сети', '/security': 'Состояние безопасности', '/diagnostics': 'Пассивная диагностика', '/logs': 'Последние логи'}
        # Reuse bounded, secret-scrubbed observations without a model roundtrip.
        safe = assistant_diagnostics.sanitize(api, result, max_chars=7000)
        from assistant_messages import diagnostic_text
        return titles[command] + '\n\n' + diagnostic_text(safe)
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
                  'revision': control.read(api)['revision'], 'submitted': False, 'source': source}
        with control.operations.locked(api.DATA_DIR):
            enqueue(api, actor, record)
        return f"Подтвердите {action} для {identity}. Действие может прервать соединения службы.\n/confirm {record['code']}\nКод действует 10 минут и только для вас. /cancel {record['code']} — отмена.\nОперация: {record['operation_id']}"
    if command in ('/confirm', '/cancel') and (len(args) == 1 if command == '/confirm' else len(args) in (0, 1)):
        with control.operations.locked(api.DATA_DIR):
            if command == '/cancel' and not args and len(pending_actions(api, actor, settings=settings)) > 1:
                raise HTTPException(409, 'Ожидают несколько действий. Укажите /cancel <код> нужного действия')
            path = pending_path(api, actor, code=args[0] if args else None)
            record = control.operations.read(path)
            if not record:
                raise HTTPException(409, 'Нет ожидающей команды. /help — справка')
            if args and (not re.fullmatch(r'[0-9a-f]{8}', args[0]) or not hmac.compare_digest(args[0], record['code'])):
                raise HTTPException(409, 'Неверный код подтверждения')
            if command == '/cancel':
                if record.get('submitted'):
                    operation = control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'))
                    if record.get('kind') == 'diagnostic_probe' and control.operations.diagnostic_state(operation).get('state') == 'unknown':
                        path.unlink()
                        return 'Ожидание проверки снято. Результат запроса неизвестен; автоматический повтор не выполнялся.'
                    if record.get('kind') == 'telegram' and operation.get('state') == 'unknown':
                        path.unlink()
                        return 'Ожидание результата снято. Действие Telegram не отменено: его результат неизвестен. Проверьте чат перед повтором.'
                    raise HTTPException(409, 'Команда уже отправлена. /operation ' + record['operation_id'])
                path.unlink()
                return 'Ожидающая команда отменена.'
            if not re.fullmatch(r'[0-9a-f]{8}', args[0]) or not hmac.compare_digest(args[0], record['code']):
                raise HTTPException(409, 'Неверный код подтверждения')
            if record.get('completed_text'):
                return record['completed_text']
            if record.get('kind') == 'mihomo' and record.get('source') != 'bot-command' and not settings.get('mcp', {}).get('mihomo-mcp', settings.get('mcp', {}).get('vps-controller-mcp', False)):
                raise HTTPException(403, 'Контур MCP выключен; изменение не отправлено')
            if record.get('kind') == 'relay':
                import assistant_relay
                assistant_relay.require(settings, actor, manage=True)
            if record.get('kind') in ('mihomo', 'telegram', 'relay') and record.get('submitted'):
                raise HTTPException(409, 'Запрос уже отправлен. Проверьте результат: /operation ' + record['operation_id'])
            existing = control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'))
            if not existing and (time.time() > record['expires'] or control._read(api)['revision'] != record['revision']):
                path.unlink()
                raise HTTPException(409, 'Подтверждение истекло или права изменились. Отправьте /service заново')
            if not existing and record.get('kind') not in ('mihomo', 'roles', 'observability', 'telegram', 'diagnostic_rules', 'diagnostic_probe', 'relay'):
                definition = api.managed_services().get(record['service'], {})
                if record['action'] not in controls(record['service'], definition):
                    path.unlink()
                    raise HTTPException(403, 'Действие больше недоступно через Nodera')
            import assistant_confirmations
            assistant_confirmations.validate(record, api, actor)
            record['submitted'] = True
            control.operations.atomic_json(path, record)
        # Use the panel's durable, idempotent service worker and admission lock.
        # Never hold the file lock across admission, which acquires it itself.
        try:
            if record.get('kind') == 'relay':
                import assistant_relay
                return assistant_relay.confirm(api, actor, record)
            if record.get('kind') == 'telegram':
                import assistant_telegram
                return assistant_telegram.confirm(api, actor, record)
            if record.get('kind') == 'observability':
                import observability_control
                return observability_control.confirm(api, actor, record)
            if record.get('kind') == 'diagnostic_rules':
                import diagnostic_rules
                return diagnostic_rules.confirm(api, actor, record)
            if record.get('kind') == 'diagnostic_probe':
                import diagnostic_execution
                return diagnostic_execution.confirm(api, actor, record)
            if record.get('kind') == 'roles':
                import assistant_role_actions
                return assistant_role_actions.confirm(api, actor, record)
            if record.get('kind') == 'mihomo':
                import assistant_mihomo
                return assistant_mihomo.confirm(api, actor, record)
            result = api.manage_service(record['service'], api.ServiceAction(action=record['action']), None, record['operation_id'])
        except HTTPException as exc:
            with control.operations.locked(api.DATA_DIR):
                current = control.operations.read(path)
                definite_rejection = record.get('kind') not in ('mihomo', 'telegram', 'relay') or exc.status_code in (403, 404, 409, 422)
                if definite_rejection and current.get('operation_id') == record['operation_id'] and not control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json')):
                    record['submitted'] = False
                    control.operations.atomic_json(path, record)
            raise
        return operation_text(result)
    return 'Неизвестная команда или неверные аргументы.\n' + HELP
