"""Standalone NoderaBot: Bot API and optional calls to the installed AI-agent.

This runtime does not import MCP/Telethon or execute an AI tool loop.
"""
import asyncio
import html
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import sys
import tempfile
import time

import httpx

logging.basicConfig(level=logging.ERROR)
logging.getLogger('httpx').setLevel(logging.ERROR)


def telegram_html(text):
    output = []
    for part in re.split(r'(```[^\n]*\n[\s\S]*?```|`[^`\n]+`)', text):
        if part.startswith('```') and part.endswith('```'):
            code = part[3:-3].split('\n', 1)[-1]
            output.append('<pre><code>' + html.escape(code) + '</code></pre>')
        elif part.startswith('`') and part.endswith('`'):
            output.append('<code>' + html.escape(part[1:-1]) + '</code>')
        else:
            escaped = html.escape(part)
            escaped = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', escaped)
            escaped = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', escaped)
            escaped = re.sub(r'(?m)^#{1,6}\s+(.+)$', r'<b>\1</b>', escaped)
            output.append(re.sub(r'(?m)^[-*+]\s+', '• ', escaped))
    return ''.join(output)


def telegram_chunks(text):
    chunks, current, stack = [], '', []
    units = 0
    for token in re.findall(r'<[^>]+>|&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);|.', telegram_html(text[:12000]), re.DOTALL):
        tag = token.startswith('<')
        size = 0 if tag else len(html.unescape(token).encode('utf-16-le')) // 2
        if units + size > 3500:
            chunks.append(current + ''.join('</' + name + '>' for name in reversed(stack)))
            current = ''.join('<' + name + '>' for name in stack); units = 0
        current += token; units += size
        if tag:
            if token.startswith('</'):
                if stack: stack.pop()
            else: stack.append(token[1:-1])
    if units:
        chunks.append(current + ''.join('</' + name + '>' for name in reversed(stack)))
    return chunks

PUBLIC_COMMANDS = [('start', 'Приветствие'), ('help', 'Справка'), ('id', 'Мой Telegram ID')]
CHAT_COMMANDS = PUBLIC_COMMANDS + [('models', 'Каталог моделей AI'), ('model', 'Текущая модель AI')]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [('models', 'Доступные модели AI'), ('model', 'Текущая модель AI')] + [
    ('status', 'Состояние сервера'), ('resources', 'CPU, память, диск и трафик'),
    ('services', 'Состояние служб'), ('components', 'Установленные компоненты')]
TYPING_INTERVAL = 4


@asynccontextmanager
async def typing(settings, actor):
    async def keep_alive():
        while True:
            started = time.monotonic()
            try:
                await asyncio.wait_for(bot_api(settings, 'sendChatAction', chat_id=actor, action='typing'), timeout=3)
            except Exception:
                pass  # A cosmetic status must never discard or delay an AI answer.
            await asyncio.sleep(max(0.01, TYPING_INTERVAL - (time.monotonic() - started)))
    task = asyncio.create_task(keep_alive(), name=f'nodera-typing-{actor}')
    try:
        await asyncio.sleep(0)
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def command_menu(settings, actor=None, admin=False, chat=False):
    scope = {'type': 'chat', 'chat_id': actor} if actor else {'type': 'all_private_chats'}
    await bot_api(settings, 'setMyCommands', scope=scope,
                  commands=[{'command': name, 'description': description} for name, description in (ADMIN_COMMANDS if admin else CHAT_COMMANDS if chat else PUBLIC_COMMANDS)])


async def server_command(settings, actor, prompt):
    if len(prompt) > 256:
        return 'Команда слишком длинная. /help — формат команд.'
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-command',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)},
            json={'command': prompt})
        if response.status_code != 200:
            if response.status_code in (401, 403):
                return 'Недостаточно прав для этого действия.'
            if response.status_code in (404, 409):
                detail = response.json().get('detail')
                if isinstance(detail, str): return detail[:1000]
            return 'Команда не выполнена. Проверьте состояние в панели; если подтверждение уже отправлено, используйте /operation с ID операции.'
        return response.json()['text']


async def bot_api(settings, method, **payload):
    async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
        response = await client.post(f"https://api.telegram.org/bot{settings['bot_token']}/{method}", json=payload)
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError('Telegram Bot API вернул HTTP ' + str(response.status_code))
        return response.json()['result']


async def agent_request(settings, actor, prompt):
    async with httpx.AsyncClient(timeout=150, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-chat',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)},
            json={'prompt': prompt})
        if response.status_code != 200:
            if response.status_code == 409:
                return 'Сейчас я не могу подготовить ответ. Информационные команды доступны; владелец может проверить настройки AI.'
            return 'Не получилось подготовить ответ. Попробуйте немного позже.'
        value = response.json()
        answer = value['result']['answer']
        pending = value.get('pending_action')
        if pending:
            if pending.get('changes'):
                answer += '\n\nИзменения:\n```json\n' + json.dumps(pending['changes'], ensure_ascii=False, indent=2)[:4000] + '\n```'
            answer += f"\n\nПодтвердите {pending['action']} для {pending['service']}:\n/confirm {pending['code']}"
        return answer


_profile_cache = {}


async def observed_profile(settings, sender):
    key = (hashlib.sha256(settings['bot_token'].encode()).hexdigest(), sender['id'])
    previous = _profile_cache.get(key)
    if previous and time.monotonic() - previous[0] < 3600:
        return previous[1]
    details = {}
    try:
        chat = await bot_api(settings, 'getChat', chat_id=sender['id'])
        if chat.get('id') == sender['id'] and chat.get('type') == 'private':
            details = {'bio': str(chat.get('bio', ''))[:1024], 'birthdate': chat.get('birthdate')}
    except Exception:
        return previous[1] if previous else {}
    if len(_profile_cache) >= 1000: _profile_cache.clear()
    _profile_cache[key] = (time.monotonic(), details)
    return details


async def identify(settings, sender):
    """The gateway reads current permissions; credential snapshots are not ACLs."""
    details = await observed_profile(settings, sender)
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-contact',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(sender['id'])},
            json={'telegram_id': sender['id'], 'username': sender.get('username', ''),
                  'name': sender.get('first_name', '')[:80], 'last_name': sender.get('last_name', '')[:80],
                  'language_code': sender.get('language_code', '')[:35], 'is_premium': sender.get('is_premium'),
                  **details, 'is_bot': sender.get('is_bot', False)})
        if response.status_code != 200:
            return False
        access = response.json()
        return True if access.get('admin') is True else 'chat' if access.get('chat') is True else False


def public_reply(sender, command):
    if command in ('/id', '/userid'):
        tag = '\nТег: @' + sender['username'] if sender.get('username') else ''
        return f"Ваш Telegram ID: {sender['id']}{tag}\nДля доступа к ассистенту передайте этот ID владельцу."
    if command in ('/start', '/help'):
        return 'Привет! Я Нодера, ваш AI-ассистент 👋\nПомогаю с вопросами и доступными вам настройками. Чтобы начать общение, попросите владельца открыть доступ.\n/id — ваш Telegram ID, /help — справка.'
    return 'Я Нодера, ваш AI-ассистент 👋 Чтобы начать общение, обратитесь к владельцу. Ваш Telegram ID: /id.'


def assistant_help(admin=False):
    text = ('Привет! Я Нодера, ваш AI-ассистент 👋\n'
            'Отвечаю на вопросы, помогаю разобраться с подпиской и доступными настройками. '
            'Просто напишите, чем помочь — команды помнить не нужно.\n\n'
            'Информация:\n/id — ваш Telegram ID\n/models — доступные модели\n/model — текущая модель')
    if admin:
        text += '\n/status — состояние сервера\n/resources — ресурсы\n/services — состояние служб\n/components — компоненты'
    return text


def informational_reply(command, answer):
    if answer.startswith('Неизвестная команда'):
        return 'Не узнала команду. Напишите, что хотите сделать, или откройте /help.'
    if command == '/services':
        rows = [line.split(' · ') for line in answer.splitlines()]
        states = [' · '.join(parts[:2]) for parts in rows if len(parts) == 3 and re.fullmatch(r'[a-zA-Z0-9_.:@-]{1,80}', parts[0])]
        return 'Состояние служб:\n' + '\n'.join(states) if states else 'Не удалось получить состояние служб. Попробуйте немного позже.'
    return answer


async def reply(settings, sender, prompt, access):
    command = prompt.split()[0].split('@')[0].lower()
    if command in ('/id', '/userid') or not access:
        return public_reply(sender, command)
    if command in ('/start', '/help'):
        return assistant_help(access is True)
    if command.startswith('/'):
        if access == 'chat' and command not in ('/models', '/model', '/confirm', '/cancel', '/operation'):
            return 'Вам доступен чат. /help — доступные команды.'
        try:
            return informational_reply(command, await server_command(settings, sender['id'], prompt))
        except Exception:
            return 'Не удалось получить результат команды. Проверьте панель или /operation с ID операции. Не повторяйте изменение, пока не проверите его результат.'
    try:
        async with typing(settings, sender['id']):
            return await agent_request(settings, sender['id'], prompt)
    except Exception:
        return 'Сейчас не получилось подготовить ответ. Попробуйте немного позже.'


BUTTON_COMMANDS = {'Сервер': '/status', 'Ресурсы': '/resources', 'Службы': '/services',
                   'Компоненты': '/components', 'Модели AI': '/models',
                   'Текущая модель': '/model', 'AI по умолчанию': '/model default', 'Помощь': '/help'}


def reply_keyboard(access):
    rows = [['Модели AI', 'Текущая модель'], ['Помощь']]
    if access is True:
        rows = [['Сервер', 'Ресурсы'], ['Службы', 'Компоненты']] + rows
    if not access:
        return {'remove_keyboard': True}
    return {'keyboard': [[{'text': text} for text in row] for row in rows],
            'resize_keyboard': True, 'is_persistent': True, 'input_field_placeholder': 'Напишите вопрос Нодере…'}


def inline_keyboard(answer, prompt, access, actions):
    """Opaque callbacks are scoped to one actor; long model IDs stay server-side."""
    rows = []
    def button(text, command):
        key = secrets.token_hex(8)
        actions[key] = command
        return {'text': text[:60], 'callback_data': 'nd:' + key}
    command = prompt.split()[0].split('@')[0].lower()
    if access and command == '/models' and answer.startswith('Модели · страница '):
        for model in answer.split('\n\n', 1)[0].splitlines()[1:]:
            rows.append([button(model, '/model ' + model)])
        page = re.search(r'страница (\d+)/(\d+)', answer)
        if page:
            current, total = map(int, page.groups())
            navigation = []
            if current > 1: navigation.append(button('← Назад', f'/models {current - 1}'))
            if current < total: navigation.append(button('Далее →', f'/models {current + 1}'))
            if navigation: rows.append(navigation)
        rows.append([button('AI по умолчанию', '/model default')])
    confirmation = re.search(r'/confirm ([0-9a-f]{8})', answer)
    if access and confirmation:
        rows.append([button('Подтвердить действие', '/confirm ' + confirmation[1]), button('Отмена', '/cancel')])
    operation = re.search(r'/operation ([0-9a-f]{32})', answer)
    if access and operation:
        rows.append([button('Проверить результат', '/operation ' + operation[1])])
    return {'inline_keyboard': rows} if rows else None


async def bot(settings):
    if not settings['modules']['nodera-bot'] or not settings.get('bot_token'):
        raise ValueError('Нужен токен включённого NoderaBot')
    if (await bot_api(settings, 'getWebhookInfo')).get('url'):
        raise ValueError('У бота установлен webhook; long polling не запущен')
    try: await command_menu(settings)
    except Exception: logging.error('NoderaBot command menu could not be updated')
    state_root = Path(os.environ.get('STATE_DIRECTORY', tempfile.gettempdir()))
    state_file = state_root / 'offset.json'
    offset = json.loads(state_file.read_text()).get('offset', 0) if state_file.exists() else 0
    last = {}
    menus = {}
    callbacks = {}
    while True:
        try:
            updates = await bot_api(settings, 'getUpdates', offset=offset, timeout=25, allowed_updates=['message', 'callback_query'])
            for update in updates:
                offset = max(offset, update['update_id'] + 1)
                temporary = state_file.with_suffix('.tmp')
                temporary.write_text(json.dumps({'offset': offset})); temporary.chmod(0o600); temporary.replace(state_file)
                callback = update.get('callback_query')
                message = callback.get('message', {}) if callback else update.get('message', {})
                sender = callback.get('from', {}) if callback else message.get('from', {})
                actor = sender.get('id')
                if message.get('chat', {}).get('type') != 'private' or type(actor) is not int or actor <= 0 or sender.get('is_bot'): continue
                if callback:
                    await bot_api(settings, 'answerCallbackQuery', callback_query_id=callback['id'])
                    key = callback.get('data', '')
                    prompt = callbacks.get(actor, {}).get(key[3:], '') if key.startswith('nd:') else ''
                    if not prompt:
                        await bot_api(settings, 'sendMessage', chat_id=actor, text='Это меню устарело. Откройте раздел кнопкой ниже.')
                        continue
                else:
                    prompt = message.get('text', '').strip()
                    prompt = BUTTON_COMMANDS.get(prompt, prompt)
                if not prompt.startswith('/') and time.monotonic() - last.get(actor, 0) < 3: continue
                if len(last) >= 1000:
                    last = {key: at for key, at in last.items() if time.monotonic() - at < 60}
                    if len(last) >= 1000: continue
                if not prompt.startswith('/'):
                    last[actor] = time.monotonic()
                try: access = await identify(settings, sender)
                except Exception: access = False
                if not prompt: continue
                admin = access is True
                role = 'admin' if admin else 'chat' if access == 'chat' else 'guest'
                if menus.get(actor) != role:
                    try:
                        await command_menu(settings, actor, admin, access == 'chat')
                        if len(menus) >= 1000: menus.clear()
                        menus[actor] = role
                    except Exception: logging.error('NoderaBot private command menu could not be updated')
                answer = await reply(settings, sender, prompt, access)
                if access:
                    # A request can outlive a permission change.
                    try: current_access = await identify(settings, sender)
                    except Exception: current_access = False
                    if current_access != access:
                        access = False
                        answer = public_reply(sender, '')
                if len(callbacks) >= 1000: callbacks.clear()
                actions = {}
                markup = inline_keyboard(answer, prompt, access, actions)
                if actions: callbacks[actor] = actions
                for chunk in telegram_chunks(answer):
                    await bot_api(settings, 'sendMessage', chat_id=actor, text=chunk, parse_mode='HTML', reply_markup=reply_keyboard(access))
                if markup:
                    await bot_api(settings, 'sendMessage', chat_id=actor, text='Выберите действие:', reply_markup=markup)
        except Exception:
            logging.error('NoderaBot polling or delivery failed')
            await asyncio.sleep(5)


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--self-check':
            print('NoderaBot runtime ready')
        elif sys.argv[1] == '--bot':
            path = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'settings'
            asyncio.run(bot(json.loads(path.read_text())))
        elif sys.argv[1] == '--check':
            payload = json.loads(sys.stdin.read(1024 * 1024)); settings = payload['settings']
            if not settings['modules']['nodera-bot']: raise ValueError('Bot disabled')
            result = asyncio.run(bot_api(settings, 'getMe'))
            print(json.dumps({'ok': True, 'result': {'username': result['username']}}))
        else: raise ValueError('Unknown command')
    except Exception:
        if sys.argv[1] == '--check': print(json.dumps({'ok': False, 'error': 'Не удалось проверить NoderaBot. Проверьте токен и доступ к Telegram.'}, ensure_ascii=False))
        else: logging.error('NoderaBot stopped')
        sys.exit(1)
