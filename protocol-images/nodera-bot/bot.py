"""Standalone NoderaBot: Bot API and optional calls to the installed AI-agent.

This runtime does not import MCP/Telethon or execute an AI tool loop.
"""
import asyncio
import html
import json
import logging
import os
import re
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
ADMIN_COMMANDS = PUBLIC_COMMANDS + [('models', 'Каталог моделей AI'), ('model', 'Выбрать модель AI')] + [
    ('status', 'Состояние сервера'), ('resources', 'CPU, память, диск и трафик'),
    ('services', 'Службы и доступные действия'), ('components', 'Установленные компоненты'),
    ('service', 'Управление: ID start|stop|restart'), ('confirm', 'Подтвердить действие: код'),
    ('cancel', 'Отменить ожидающее подтверждение'), ('operation', 'Результат операции: ID')]


async def command_menu(settings, actor=None, admin=False):
    scope = {'type': 'chat', 'chat_id': actor} if actor else {'type': 'all_private_chats'}
    await bot_api(settings, 'setMyCommands', scope=scope,
                  commands=[{'command': name, 'description': description} for name, description in (ADMIN_COMMANDS if admin else PUBLIC_COMMANDS)])


async def server_command(settings, actor, prompt):
    if len(prompt) > 256:
        return 'Команда слишком длинная. /help — формат команд.'
    async with httpx.AsyncClient(timeout=45, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-command',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(actor)},
            json={'command': prompt})
        if response.status_code != 200:
            if response.status_code in (401, 403):
                return 'Доступ администратора бота не подтверждён.'
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
                return 'AI-agent не установлен, остановлен или не принят под управление. Команды бота доступны.'
            return 'AI-agent не выполнил запрос. Проверьте настройки и доступ провайдера в панели.'
        return response.json()['result']['answer']


async def identify(settings, sender):
    """The gateway reads current permissions; credential snapshots are not ACLs."""
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/api/assistant/bot-contact',
            headers={'Authorization': 'Bearer ' + settings['control_token'], 'X-MCP-Actor': str(sender['id'])},
            json={'telegram_id': sender['id'], 'username': sender.get('username', ''),
                  'name': sender.get('first_name', '')[:80], 'is_bot': sender.get('is_bot', False)})
        if response.status_code != 200:
            return False
        access = response.json()
        return True if access.get('admin') is True else 'chat' if access.get('chat') is True else False


def public_reply(sender, command):
    if command in ('/id', '/userid'):
        tag = '\nТег: @' + sender['username'] if sender.get('username') else ''
        return f"Ваш Telegram ID: {sender['id']}{tag}\nПередайте ID владельцу бота, если вам нужен доступ администратора."
    if command in ('/start', '/help'):
        return 'Привет! Я Нодера 👋\nМогу подсказать ваш Telegram ID: /id. Доступ к серверу выдаёт владелец бота.'
    return 'Я Нодера 👋 Для получения доступа обратитесь к владельцу бота. Ваш Telegram ID можно узнать командой /id.'


async def reply(settings, sender, prompt, access):
    command = prompt.split()[0].split('@')[0].lower()
    if command in ('/id', '/userid') or not access:
        return public_reply(sender, command)
    if access == 'chat' and command in ('/help', '/start'):
        return 'Привет! Я Нодера 👋 Напишите вопрос. /models — модели, /model <ID> — выбрать модель.'
    if command in ('/start', '/help'):
        try:
            return 'Привет! Я Нодера 👋 У вас доступ администратора бота.\n' + await server_command(settings, sender['id'], '/help')
        except Exception:
            return 'Команды сервера сейчас недоступны. Проверьте панель и повторите /help.'
    if command.startswith('/'):
        if access == 'chat' and command not in ('/models', '/model'):
            return 'Вам доступен чат. /help — доступные команды.'
        try:
            return await server_command(settings, sender['id'], prompt)
        except Exception:
            return 'Не удалось получить результат команды. Проверьте панель или /operation с ID операции. Не повторяйте изменение, пока не проверите его результат.'
    try:
        return await agent_request(settings, sender['id'], prompt)
    except Exception:
        return 'AI-agent сейчас недоступен. Команды бота доступны.'


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
    while True:
        try:
            updates = await bot_api(settings, 'getUpdates', offset=offset, timeout=25, allowed_updates=['message'])
            for update in updates:
                offset = max(offset, update['update_id'] + 1)
                temporary = state_file.with_suffix('.tmp')
                temporary.write_text(json.dumps({'offset': offset})); temporary.chmod(0o600); temporary.replace(state_file)
                message = update.get('message', {})
                sender = message.get('from', {})
                actor = sender.get('id')
                if message.get('chat', {}).get('type') != 'private' or type(actor) is not int or actor <= 0 or sender.get('is_bot'): continue
                prompt = message.get('text', '').strip()
                if not prompt or time.monotonic() - last.get(actor, 0) < 3: continue
                if len(last) >= 1000:
                    last = {key: at for key, at in last.items() if time.monotonic() - at < 60}
                    if len(last) >= 1000: continue
                last[actor] = time.monotonic()
                try: access = await identify(settings, sender)
                except Exception: access = False
                admin = access is True
                if menus.get(actor) is not admin:
                    try:
                        await command_menu(settings, actor, admin)
                        if len(menus) >= 1000: menus.clear()
                        menus[actor] = admin
                    except Exception: logging.error('NoderaBot private command menu could not be updated')
                answer = await reply(settings, sender, prompt, access)
                if access:
                    # A request can outlive a permission change.
                    try: current_access = await identify(settings, sender)
                    except Exception: current_access = False
                    if current_access != access: answer = public_reply(sender, '')
                for chunk in telegram_chunks(answer):
                    await bot_api(settings, 'sendMessage', chat_id=actor, text=chunk, parse_mode='HTML')
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
