"""Deterministic message presentation shared by the panel and standalone bot.

Raw command replies remain the protocol contract. Only display text is styled.
"""
import re


def diagnostic_text(value):
    """Present already scrubbed observations as bounded prose, without a model call."""
    labels = {'state': 'Состояние', 'status': 'Статус', 'service': 'Служба', 'services': 'Службы', 'name': 'Название',
              'message': 'Сообщение', 'reason': 'Причина', 'recommendation': 'Рекомендация', 'risk': 'На что обратить внимание',
              'findings': 'Проверки', 'summary': 'Итог', 'lines': 'События', 'logs': 'Журнал', 'errors': 'Ошибки',
              'warnings': 'Предупреждения', 'checks': 'Проверки', 'details': 'Подробности', 'items': 'Записи',
              'available': 'Доступно', 'enabled': 'Включено', 'active': 'Активно', 'healthy': 'Исправность',
              'server': 'Сервер', 'hostname': 'Имя сервера', 'network': 'Сеть', 'dns': 'DNS', 'domains': 'Домены',
              'listeners': 'Открытые порты', 'routes': 'Маршруты', 'endpoints': 'Точки подключения', 'address': 'Адрес',
              'port': 'Порт', 'protocol': 'Протокол', 'firewall': 'Межсетевой экран', 'ssh': 'SSH', 'tls': 'TLS',
              'application_security': 'Защита приложения', 'panel_access': 'Доступ к панели', 'consistent': 'Соответствие настройкам',
              'admin_password_strong': 'Надёжность пароля панели', 'cors_restricted': 'Ограничение CORS', 'secrets_protected': 'Защита секретов',
              'password_authentication': 'Вход по паролю', 'ssh_password': 'Защита входа SSH', 'updated_at': 'Обновлено',
              'observed_at': 'Время наблюдения', 'source': 'Источник', 'partial': 'Неполные данные', 'truncated': 'Сокращено',
              'id': 'Идентификатор', 'url': 'Адрес', 'method': 'Метод', 'transport': 'Транспорт', 'error': 'Ошибка'}
    states = {'unknown': 'неизвестно', 'unavailable': 'недоступно', 'running': 'работает', 'stopped': 'остановлено',
              'healthy': 'исправно', 'passed': 'проверка пройдена', 'failed': 'ошибка', 'risk': 'требует внимания',
              'pending': 'ожидается', 'disabled': 'отключено', 'enabled': 'включено', 'active': 'активно', 'inactive': 'неактивно'}
    rows, remaining = [], 6500
    def add(text):
        nonlocal remaining
        if remaining <= 0: return
        rows.append(text[:remaining]); remaining -= len(rows[-1]) + 1
    def scalar(item):
        if item is None: return 'нет данных'
        if type(item) is bool: return 'да' if item else 'нет'
        return states.get(item, item) if isinstance(item, str) else str(item)
    def walk(item, level=0):
        if level > 6 or remaining <= 0: return
        if isinstance(item, dict):
            if {'state', 'risk', 'recommendation'} <= item.keys():
                title = labels.get(item.get('id'), str(item.get('id', 'Проверка')).replace('_', ' '))
                add(f'• {title}: {scalar(item["state"])}')
                if item['state'] != 'passed':
                    add(('Проблема: ' if item['state'] == 'risk' else 'Возможный риск: ') + str(item['risk']))
                    add('Рекомендация: ' + str(item['recommendation']))
                return
            for key, entry in item.items():
                label = labels.get(key, str(key).replace('_', ' '))
                if isinstance(entry, (dict, list)):
                    add(label + (':' if entry else ': нет записей'))
                    walk(entry, level + 1)
                else: add(f'• {label}: {scalar(entry)}')
        elif isinstance(item, list):
            for index, entry in enumerate(item[:30]):
                if isinstance(entry, (dict, list)):
                    add(f'— Запись {index + 1} —'); walk(entry, level + 1)
                else: add('• ' + str(scalar(entry)))
            if len(item) > 30: add(f'Ещё записей: {len(item) - 30}')
        else: add(str(scalar(item)))
    walk(value)
    return '\n'.join(rows) + ('\nОтчёт сокращён.' if remaining <= 0 else '')


def code(value):
    return '`' + str(value).replace('`', "'").replace('\n', ' ') + '`'


def notice(title, body, icon='ℹ️'):
    return f'{icon} **{title}**\n\n{body}'


def labelled(lines):
    result = []
    for line in lines:
        label, separator, value = line.partition(': ')
        if separator and not line.startswith(('http:', 'https:', '/')):
            result.append(f'**{label}:** {code(value)}')
        else:
            result.append(line)
    return '\n'.join(result)


def command_list(text):
    lines = []
    for line in text.splitlines():
        syntax, separator, description = line.partition(' — ')
        if line.startswith('/') and separator:
            lines.append(f'• {code(syntax)} — {description}')
        elif line.endswith(':'):
            lines.append('**' + line.rstrip(':') + '**')
        else:
            lines.append(line)
    return '\n'.join(lines)


def styled_message(command, text):
    if not text: return text
    command = (command.split() or [''])[0].split('@')[0].lower()
    if text in ('Изменения сохранены.', 'Изменения Mihomo сохранены.'):
        return notice('Изменения сохранены', 'Настройки обновлены.\n\nЕсли меняли конфигурацию подключения, обновите подписку в клиенте.', '✅')
    if text == 'Ожидающая команда отменена.':
        return notice('Действие отклонено', 'Запрос закрыт до отправки. Изменения по нему не выполнялись.', '⏸')
    operation = re.match(r'^Операция ([0-9a-f]{32})\n([^\n]+)\n/operation \1$', text)
    if operation:
        identity, state = operation.groups()
        headings = {'в очереди': ('Операция принята', '⏳'), 'выполняется': ('Операция выполняется', '⏳'),
                    'выполнено': ('Операция завершена', '✅'), 'ошибка': ('Операция завершилась ошибкой', '⚠️'),
                    'отменено': ('Операция отменена', '⏸')}
        title, icon = headings.get(state, ('Результат пока не подтверждён', '❔'))
        return notice(title, f'**Состояние:** {state}\n**ID:** {code(identity)}\n\nПроверить результат:\n{code("/operation " + identity)}', icon)
    if command == '/service' and text.startswith('Подтвердите '):
        match = re.match(r'Подтвердите (start|stop|restart) для ([A-Za-z0-9_.:@-]+)\.', text)
        confirm = re.search(r'/confirm ([0-9a-f]{8})', text)
        if match and confirm:
            action, service = match.groups()
            label = {'start': 'Запуск', 'stop': 'Остановка', 'restart': 'Перезапуск'}[action]
            return notice('Нужно ваше решение', f'**Служба:** {code(service)}\n**Действие:** {label}\n\nДействие может прервать соединения службы. Выполнение ещё не началось.\n\n{code("/confirm " + confirm[1])} — подтвердить\n{code("/cancel " + confirm[1])} — отклонить\n\nКод действует 10 минут и только для вас.', '✋')
    if command == '/models' and text.startswith('Модели · страница '):
        head, _, tail = text.partition('\n\n')
        lines = head.splitlines()
        page = lines[0].removeprefix('Модели · страница ')
        return notice('Модели AI', f'Страница **{page}**\n\n' + '\n'.join('• ' + code(model) for model in lines[1:]) + ('\n\n' + tail if tail else ''), '🧠')
    if command in ('/model', '/models') and text.startswith(('Модель', 'Одна модель')):
        return notice('Модель AI', labelled(text.splitlines()), '🧠')
    if command in ('/status', '/resources') and text.startswith(('Сервер:', 'Ресурсы сервера')):
        lines = text.splitlines()
        if command == '/resources': lines = lines[1:]
        return notice('Сервер' if command == '/status' else 'Ресурсы сервера', labelled(lines), '🖥️' if command == '/status' else '📊')
    if command == '/services' and text.startswith(('Службы (', 'Состояние служб:')):
        states = {'running': 'Работает', 'stopped': 'Остановлена', 'error': 'Ошибка', 'unknown': 'Состояние неизвестно'}
        rows, other = [], []
        for line in text.splitlines()[1:]:
            parts = line.split(' · ')
            if len(parts) >= 2:
                row = '• ' + code(parts[0]) + ' — ' + states.get(parts[1], code(parts[1]))
                if len(parts) > 2:
                    controls = parts[2].replace('restart', 'перезапуск').replace('start', 'запуск').replace('stop', 'остановка')
                    row += '\n  Доступно: ' + controls
                rows.append(row)
            elif line.strip(): other.append(line)
        return notice('Службы сервера', '\n\n'.join(rows) + ('\n\n' + '\n'.join(other) if other else ''), '⚙️')
    if command in ('/profile', '/traffic', '/devices') and text.startswith(('Подписка · ', 'Устройства · ')):
        lines = text.splitlines()
        return notice('Ваша подписка' if command != '/devices' else 'Устройства подписки', '**Профиль:** ' + code(lines[0].split(' · ', 1)[1].rstrip(':')) + '\n\n' + labelled(lines[1:]), '📋')
    if command == '/subscription' and text.startswith('Ваша подписка:\n'):
        return notice('Ваша подписка', text.split('\n', 1)[1] + '\n\nСкопируйте ссылку в клиент → «Импорт подписки по URL».\nСсылка даёт доступ к подключению — храните её у себя.', '🔗')
    titles = {'/components': ('Компоненты', '🧩'), '/profiles': ('Профили', '📋'), '/connect': ('Подключение', '🔗'),
              '/help': ('Навигатор Nodera', '🧭'), '/start': ('Nodera на связи', '👋'), '/id': ('Ваш Telegram ID', '🪪'),
              '/network': ('Сеть', '🌐'), '/security': ('Безопасность', '🛡️'), '/diagnostics': ('Диагностика', '🔎'), '/logs': ('Последние события', '📑')}
    if command in titles:
        title, icon = titles[command]
        return notice(title, command_list(text), icon)
    return text
