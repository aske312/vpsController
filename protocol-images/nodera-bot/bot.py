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


def account(settings, actor):
    return next((a for a in settings['accounts'] if a['telegram_id'] == actor and a.get('bot_access')), None)


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


async def bot(settings):
    if not settings['modules']['nodera-bot'] or not settings.get('bot_token') or not any(a.get('bot_access') for a in settings['accounts']):
        raise ValueError('Нужны настройки и разрешённые пользователи NoderaBot')
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
                actor = message.get('from', {}).get('id')
                if message.get('chat', {}).get('type') != 'private' or not account(settings, actor): continue
                prompt = message.get('text', '').strip()
                if not prompt or time.monotonic() - last.get(actor, 0) < 3: continue
                last[actor] = time.monotonic()
                command = prompt.split()[0].split('@')[0]
                if command in ('/start', '/help'):
                    answer = 'NoderaBot. /status — состояние бота. Для вопросов AI отдельно установите и настройте AI-agent в панели.'
                elif command == '/status':
                    answer = 'NoderaBot принимает ваши команды. AI-agent — отдельный устанавливаемый компонент приложения.'
                else:
                    try: answer = await agent_request(settings, actor, prompt)
                    except Exception: answer = 'AI-agent сейчас недоступен. Команды бота доступны.'
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
