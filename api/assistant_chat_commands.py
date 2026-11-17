"""Panel-authenticated shortcuts share the bot executor and live actor permissions."""
import re

from fastapi import HTTPException
from pydantic import Field

import assistant_control as control
import assistant_commands as commands
import assistant_roles as roles


class Request(commands.Command):
    telegram_id: int | None = Field(default=None, gt=0, le=2**52)
    selected_model: str | None = Field(default=None, max_length=512)


ROWS = [
    ('help', 'Справка', '', False), ('status', 'Состояние сервера', '', False),
    ('resources', 'CPU, память, диск и трафик', '', False), ('services', 'Службы и доступные действия', '', False),
    ('components', 'Установленные компоненты', '', False), ('network', 'Состояние сети', '', False),
    ('security', 'Состояние безопасности', '', False), ('diagnostics', 'Пассивная диагностика', '', False),
    ('logs', 'Последние логи', ' [служба]', False), ('profiles', 'Доступные профили Mihomo', '', False),
    ('subscription', 'Ссылка подписки', ' [ID]', False), ('profile', 'Параметры подписки', ' [ID]', False),
    ('devices', 'Устройства подписки', ' [ID]', False), ('traffic', 'Трафик подписки', ' [ID]', False),
    ('connect', 'Как подключиться', '', False), ('models', 'Каталог моделей AI', ' [страница]', False),
    ('model', 'Модель веб-чата', ' [ID|default]', False),
    ('service', 'Подготовить управление службой', ' <ID> <start|stop|restart>', True),
    ('confirm', 'Подтвердить действие', ' <код>', True), ('cancel', 'Отказать в выполнении', ' [код]', False),
    ('operation', 'Результат операции', ' <ID>', True), ('id', 'Выбранный Telegram ID', '', False),
    ('start', 'Приветствие', '', False), ('ai', 'Вопрос AI-ассистенту', ' <вопрос>', True),
]


def catalog(api, actor):
    settings = control.read(api)
    grants = roles.effective(settings, actor)
    account = next((row for row in settings.get('accounts', []) if str(row['telegram_id']) == actor), {})
    result = []
    for name, label, arguments, needs_args in ROWS:
        command = '/' + name
        permission = commands.COMMAND_PERMISSIONS.get(command)
        if permission and permission not in grants: continue
        if name in ('confirm', 'cancel', 'operation') and not grants: continue
        if name in ('subscription', 'profile', 'devices', 'traffic') and not account.get('mihomo_profile_id'):
            needs_args = True
        result.append({'command': command, 'label': label, 'syntax': command + arguments,
                       'insert': command + (' ' if needs_args else ''), 'needs_args': needs_args,
                       'kind': 'ai' if name == 'ai' else 'direct'})
    return result


def response_actions(api, name, text, entries):
    """Explicit command buttons; execution always rechecks the actor's live ACL."""
    allowed = {row['command'] for row in entries}
    actions = []
    def add(label, command):
        if command.split()[0] in allowed and len(command) <= 256:
            actions.append({'label': label, 'command': command})
    if name == '/models' and text.startswith('Модели · страница '):
        for model in text.split('\n\n', 1)[0].splitlines()[1:]:
            add(model, '/model ' + model)
        page = re.search(r'страница (\d+)/(\d+)', text)
        if page:
            current, total = map(int, page.groups())
            if current > 1: add('← Назад', f'/models {current - 1}')
            if current < total: add('Далее →', f'/models {current + 1}')
        add('AI по умолчанию', '/model default')
    if name == '/services' and '/service' in allowed:
        labels = {'start': 'Запустить', 'stop': 'Остановить', 'restart': 'Перезапустить'}
        for identity, definition in api.managed_services().items():
            for action in commands.controls(identity, definition):
                add(f'{labels[action]} · {identity}', f'/service {identity} {action}')
    for identity in dict.fromkeys(re.findall(r'/operation ([0-9a-f]{32})', text)):
        add('Проверить результат', '/operation ' + identity)
    return actions


def execute(api, payload):
    actor = str(payload.telegram_id) if payload.telegram_id else 'panel'
    parts = payload.command.strip().split()
    original_name = parts[0].split('@')[0].lower() if parts else ''
    name = original_name
    aliases = {'/userid': '/id', '/hello': '/start', '/thanks': '/start'}
    name = aliases.get(name, name)
    entries = catalog(api, actor)
    if name not in {row['command'] for row in entries}:
        if name in commands.BLOCKED_COMMANDS: raise HTTPException(403, 'Эта команда недоступна через чат')
        if name in {('/' + row[0]) for row in ROWS}: raise HTTPException(403, 'Роль не разрешает эту команду')
        raise HTTPException(422, 'Неизвестная команда. Откройте меню «Команды» или /help')
    args = parts[1:]
    maximum = {'/service': 2, '/help': 0, '/start': 0, '/status': 0, '/resources': 0, '/services': 0,
               '/components': 0, '/network': 0, '/security': 0, '/diagnostics': 0, '/profiles': 0, '/connect': 0, '/id': 0}
    if name != '/ai' and len(args) > maximum.get(name, 1): raise HTTPException(422, 'Неверный формат команды. Используйте /help')
    settings = control.read(api)
    result = {'context_safe': name not in ('/subscription', '/id')}
    if original_name == '/thanks' and not args:
        result['text'] = 'Пожалуйста!'
    elif name in ('/start', '/help') and not args:
        result['text'] = ('Команды веб-чата:\n' if name == '/help' else 'Здравствуйте! Команды сервера и подписки работают без обращения к модели.\n\n') + '\n'.join(row['syntax'] + ' — ' + row['label'] for row in entries)
    elif name == '/ai':
        raise HTTPException(422, 'Отправьте вопрос AI через обычный чат')
    elif name == '/models':
        if args and not re.fullmatch(r'[0-9]+', args[0]): raise HTTPException(422, 'Формат: /models [страница]')
        if settings.get('model_mode', 'single') == 'catalog' and not settings.get('provider_url'):
            raise HTTPException(409, 'Сначала настройте подключение AI')
        result['text'] = control.bot_models(api, actor, page=int(args[0]) if args else 1)
    elif name == '/model':
        if len(args) > 1: raise HTTPException(422, 'Формат: /model [ID|default]')
        roles.require(settings, actor, 'model_select')
        if not args: result['text'] = 'Модель веб-чата: ' + (payload.selected_model or settings.get('model') or 'не выбрана')
        else:
            model = (settings.get('model') or '') if args[0] == 'default' else args[0]
            if settings.get('model_mode', 'single') == 'catalog' and model:
                if not settings.get('provider_url'): raise HTTPException(409, 'Сначала настройте подключение AI')
                available = control.model_catalog(api, control.Catalog(provider_url=settings['provider_url'], provider_id=control.active_provider(settings)))['models']
                if model not in available: raise HTTPException(409, 'Модель недоступна. Используйте /models')
            elif settings.get('model_mode', 'single') != 'catalog' and model != settings.get('model', ''):
                raise HTTPException(409, 'Включён режим одной модели. Измените подключение AI в настройках')
            result.update(model=model, text='Модель веб-чата: ' + (model or 'не выбрана; выберите в меню'))
    elif name in ('/confirm', '/cancel'):
        if len(args) > 1 or name == '/confirm' and len(args) != 1: raise HTTPException(422, 'Укажите код нужного действия')
        if not args:
            pending = commands.pending_actions(api, actor)
            if len(pending) != 1: raise HTTPException(409, 'Выберите карточку действия или укажите /cancel <код>')
            args = [pending[0]['code']]
        if not re.fullmatch(r'[0-9a-f]{8}', args[0]): raise HTTPException(422, 'Неверный формат кода')
        result.update(api.assistant_service_confirm(control.ServiceConfirmation(telegram_id=payload.telegram_id, code=args[0], decision='confirm' if name == '/confirm' else 'reject'), None))
    else:
        result['text'] = commands.execute(api, actor, ' '.join([name, *args]), source='panel')
        if result['text'].startswith('Неизвестная команда или неверные аргументы.'):
            raise HTTPException(422, 'Неверный формат команды. Используйте /help')
    result['pending_actions'] = commands.pending_actions(api, actor)
    result['actions'] = response_actions(api, name, result.get('text', ''), entries)
    return result
