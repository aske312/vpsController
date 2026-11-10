"""Standalone NoderaBot: Bot API and optional calls to the installed AI-agent.

This runtime does not import MCP/Telethon or execute an AI tool loop.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

import httpx

logging.basicConfig(level=logging.ERROR)
logging.getLogger('httpx').setLevel(logging.ERROR)


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
        return response.json().get('admin') is True


def public_reply(sender, command):
    if command in ('/id', '/userid'):
        tag = '\nТег: @' + sender['username'] if sender.get('username') else ''
        return f"Ваш Telegram ID: {sender['id']}{tag}\nПередайте ID владельцу бота, если вам нужен доступ администратора."
    if command in ('/start', '/help'):
        return 'Привет! Я Нодера 👋\nМогу подсказать ваш Telegram ID: /id. Доступ к серверу выдаёт владелец бота.'
    return 'Я Нодера 👋 Для получения доступа обратитесь к владельцу бота. Ваш Telegram ID можно узнать командой /id.'


async def reply(settings, sender, prompt, admin):
    command = prompt.split()[0].split('@')[0].lower()
    if command in ('/id', '/userid') or not admin:
        return public_reply(sender, command)
    if command in ('/start', '/help'):
        return 'Привет! Я Нодера 👋\nУ вас доступ администратора бота. Задайте вопрос о сервере.\n/id — ваш Telegram ID\n/status — состояние бота'
    if command == '/status':
        return 'NoderaBot принимает ваши команды. Серверные инструменты доступны через настроенный AI-agent и MCP.'
    try:
        return await agent_request(settings, sender['id'], prompt)
    except Exception:
        return 'AI-agent сейчас недоступен. Команды бота доступны.'


async def bot(settings):
    if not settings['modules']['nodera-bot'] or not settings.get('bot_token'):
        raise ValueError('Нужен токен включённого NoderaBot')
    if (await bot_api(settings, 'getWebhookInfo')).get('url'):
        raise ValueError('У бота установлен webhook; long polling не запущен')
    state_root = Path(os.environ.get('STATE_DIRECTORY', tempfile.gettempdir()))
    state_file = state_root / 'offset.json'
    offset = json.loads(state_file.read_text()).get('offset', 0) if state_file.exists() else 0
    last = {}
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
                try: admin = await identify(settings, sender)
                except Exception: admin = False
                answer = await reply(settings, sender, prompt, admin)
                if admin:
                    # An AI request may outlive an administrator revocation.
                    try: still_admin = await identify(settings, sender)
                    except Exception: still_admin = False
                    if not still_admin: answer = public_reply(sender, '')
                for start in range(0, min(len(answer), 12000), 3500):
                    await bot_api(settings, 'sendMessage', chat_id=actor, text=answer[start:start+3500])
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
