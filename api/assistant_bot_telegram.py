"""Own Telegram connection and narrower tool preferences, without a model call."""
import hashlib
import time
import uuid

from fastapi import HTTPException
from pydantic import ValidationError

import assistant_contacts as contacts
import assistant_control as control
import assistant_roles as roles
import assistant_telegram as telegram
import assistant_telegram_setup as setup


def result(text, buttons=None):
    return {'text': text, 'buttons': buttons or []}


def button(text, command):
    return {'text': text, 'command': command}


def setup_guide(api, settings, selected):
    if roles.role(selected) == 'owner':
        text = ('1. Откройте my.telegram.org и войдите в свой аккаунт. В разделе API development tools '
                'зарегистрируйте приложение или откройте существующее.\n'
                '2. Вернитесь в бот, откройте «Ввести API ID и hash» и сохраните параметры.')
        links = [[{'text': 'API development tools', 'url': 'https://my.telegram.org/apps'}]]
        if getattr(api, 'PUBLIC_DOMAIN', ''):
            links.append([button('Ввести API ID и hash', '/telegram configure')])
        else:
            text += '\nДля формы из бота нужен HTTPS-домен панели. Также параметры можно сохранить в Nodera → Настройки AI → MCP.'
    else:
        text = ('Попросите владельца панели включить Telegram MCP и заполнить API ID и API hash. '
                'Затем вернитесь сюда и нажмите «Проверить настройки».')
        links = []
    if not settings.get('mcp', {}).get('telegram-mcp'):
        status = 'Telegram MCP выключен.'
    else:
        missing = [label for key, label in (('telegram_api_id', 'API ID'), ('telegram_api_hash', 'API hash')) if not settings.get(key)]
        status = 'В панели не заполнены: ' + ', '.join(missing) + '.'
    return result('Telegram API · настройка\n' + status + '\n\n' + text +
                  '\n\nВведите API hash в форме настройки. Коды входа и пароль вводите только в Telegram.',
                  links + [[button('Проверить настройки', '/telegram session'), button('← Telegram API', '/telegram')]])


def account(settings, actor):
    roles.require(settings, actor, 'telegram_read')
    selected = next((row for row in settings['accounts'] if str(row['telegram_id']) == actor), None)
    if not selected: raise HTTPException(403, 'Настройки доступны только своему аккаунту')
    return selected


def view(api, actor, prompt, request_id):
    parts = prompt.split(maxsplit=2)
    action = parts[1] if len(parts) > 1 else ''
    argument = parts[2] if len(parts) > 2 else ''
    settings = control.read(api)
    selected = account(settings, actor)
    ready = bool(settings.get('telegram_api_id') and settings.get('telegram_api_hash') and settings.get('mcp', {}).get('telegram-mcp'))
    fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
    with control.operations.locked(api.DATA_DIR):
        state = contacts.profile(api, settings, actor)
        receipt = state.get('telegram_receipts', {}).get(request_id)
        if receipt:
            if receipt['fingerprint'] != fingerprint: raise HTTPException(409, 'ID запроса уже использован')
            return receipt['output']
        dialog = state.get('telegram_dialog', {})

    changes = {}
    if action == 'session':
        if not ready:
            return setup_guide(api, settings, selected)
        dialog = {'expires': time.time() + 600}
        output = result('Telegram API · подключение\nСоздайте StringSession своего аккаунта в доверенной локальной копии Telethon '
                        'с API ID и API hash, настроенными в Nodera. Отправьте только строку StringSession отдельным сообщением. '
                        'Бот сначала удалит сообщение, затем проверит и сохранит сессию.\n\n'
                        'Пароли и коды входа сюда отправлять не нужно. Ожидание: 10 минут.',
                        [[{'text': 'Как создать StringSession', 'url': 'https://docs.telethon.dev/en/stable/concepts/sessions.html#string-sessions'}],
                         [button('Отмена', '/telegram cancel')]])
    elif action == 'input':
        if dialog.get('expires', 0) <= time.time():
            return result('Telegram API\nВремя подключения истекло. Начните заново.', [[button('Подключить', '/telegram session')]])
        if not ready:
            return setup_guide(api, settings, selected)
        try: session = control.Account(telegram_id=int(actor), name='', session=argument).session
        except (ValueError, ValidationError):
            return result('Telegram API · подключение\nНужна строка Telethon StringSession. Проверьте формат или отмените подключение.', [[button('Отмена', '/telegram cancel')]])
        if not session: raise HTTPException(422, 'Строка сессии пуста')
        try: checked = control.runtime_request(api, '--telegram-session-check', {'actor': actor, 'session': session})
        except HTTPException:
            # A rejected or unavailable check is a terminal UI result, not a
            # reason for the inbox to poll Telegram indefinitely with a key.
            output = result('Telegram API · подключение\nНе удалось подтвердить сессию. Сохранённое подключение не изменено. Проверьте свою StringSession и доступность MCP Telegram.', [[button('Повторить подключение', '/telegram session')]])
        else:
            if checked.get('result', {}).get('authorized') is not True or str(checked['result'].get('telegram_id')) != actor:
                raise HTTPException(422, 'Сессия не подтверждена для вашего Telegram ID')
            changes['session'] = session
            dialog = {}
            output = result('Telegram API\nВаш аккаунт подключён. Настройте, какие разрешённые инструменты может использовать ИИ.', [[button('Права ИИ', '/telegram tools')]])
    elif action == 'disconnect':
        if argument != 'yes':
            return result('Telegram API · отключение\nУдалить сохранённую сессию из Nodera?', [[button('Отключить', '/telegram disconnect yes'), button('Отмена', '/telegram')]])
        changes['session'] = ''
        dialog = {}
        output = result('Telegram API\nСессия удалена из Nodera.', [[button('Подключить', '/telegram session')]])
    elif action in ('tools', 'tool'):
        if action == 'tool':
            values = argument.split()
            if len(values) != 4 or values[0] not in telegram.TOOLS or values[1] not in ('on', 'off'):
                raise HTTPException(422, 'Выберите инструмент кнопкой')
            name, switch, revision, page = values
            allowed = selected.get('telegram_tools')
            if name not in (telegram.DEFAULT_TOOLS if allowed is None else allowed):
                raise HTTPException(403, 'Инструмент запрещён администратором')
            if revision != settings['revision']: raise HTTPException(409, 'Настройки изменились. Откройте Telegram API заново')
            disabled = set(selected.get('telegram_disabled_tools', []))
            if switch == 'on': disabled.discard(name)
            else: disabled.add(name)
            changes['telegram_disabled_tools'] = sorted(disabled)
        else: page = argument or '1'
        if not page.isdigit(): raise HTTPException(422, 'Выберите страницу кнопкой')
        allowed = selected.get('telegram_tools')
        allowed = set(telegram.DEFAULT_TOOLS if allowed is None else allowed)
        items = [row for row in telegram.CATALOG if row['name'] in allowed]
        pages = max(1, (len(items) + 5) // 6)
        page = max(1, min(int(page), pages))
        # The revision in toggle callbacks is assigned after the settings commit.
        effective = set(telegram.enabled({**selected, **changes}))
        rows = [[button(('✓ ' if row['name'] in effective else '○ ') + row['title'],
                        f"/telegram tool {row['name']} {'off' if row['name'] in effective else 'on'} REVISION {page}")]
                for row in items[(page-1)*6:page*6]]
        navigation = []
        if page > 1: navigation.append(button('← Назад', '/telegram tools ' + str(page-1)))
        if page < pages: navigation.append(button('Далее →', '/telegram tools ' + str(page+1)))
        if navigation: rows.append(navigation)
        output = result(f'Telegram API · права ИИ\nСтраница {page}/{pages} · включено {len(effective)}/{len(items)}\n\nНажмите инструмент, чтобы включить или отключить его. Доступ ограничен разрешениями администратора; действия требуют подтверждения.', rows)
    elif action == 'configure':
        dialog = {}
        output = result('Telegram API · параметры приложения\nОткройте форму и подтвердите сохранение API ID и API hash.')
    elif action in ('', 'cancel'):
        dialog = {}
        output = result('Telegram API\nПодключение: ' + ('сессия сохранена' if selected.get('session') else 'не подключено') +
                        '\nMCP Telegram: ' + ('настроен' if ready else 'требует настройки владельцем') +
                        f'\nИнструментов ИИ включено: {len(telegram.enabled(selected))}\n\nНастройки относятся только к вашему аккаунту.',
                        [[button(('Заменить подключение' if selected.get('session') else 'Подключить') if ready else 'Как настроить', '/telegram session')],
                         [button('Права ИИ', '/telegram tools')]] +
                        ([[button('Изменить API ID и hash', '/telegram configure')]] if ready and roles.role(selected) == 'owner' and getattr(api, 'PUBLIC_DOMAIN', '') else []) +
                        ([[button('Отключить', '/telegram disconnect')]] if selected.get('session') else []))
    else: raise HTTPException(422, 'Выберите действие в разделе Telegram API')

    if action not in ('', 'cancel', 'session') and not any(item.get('command') == '/telegram' for row in output['buttons'] for item in row):
        output['buttons'].append([button('← Telegram API', '/telegram')])

    with control.operations.locked(api.DATA_DIR):
        current = control._read(api)
        current_account = account(current, actor)
        if current['revision'] != settings['revision']: raise HTTPException(409, 'Настройки изменились. Откройте Telegram API заново')
        # Reconcile simultaneous deliveries before publishing another generation.
        state = contacts.profile(api, current, actor)
        receipt = state.get('telegram_receipts', {}).get(request_id)
        if receipt:
            if receipt['fingerprint'] != fingerprint: raise HTTPException(409, 'ID запроса уже использован')
            return receipt['output']
        if action == 'configure':
            output['buttons'].insert(0, [{'text': 'Открыть форму', 'web_app': setup.issue(api, current, actor, state)}])
        if changes:
            current_account.update(changes)
            current['revision'] = uuid.uuid4().hex
            control.store(api, current)
        for row in output['buttons']:
            for item in row:
                if 'command' in item:
                    item['command'] = item['command'].replace('REVISION', current['revision'])
        state['telegram_dialog'] = dialog
        state.pop('admin_dialog', None)
        if request_id:
            receipts = dict(state.get('telegram_receipts', {}))
            receipts[request_id] = {'fingerprint': fingerprint, 'output': output}
            state['telegram_receipts'] = dict(list(receipts.items())[-32:])
        contacts.save_profile(api, current, actor, state)
    return output
