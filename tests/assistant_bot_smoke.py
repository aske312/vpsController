"""Run in the NoderaBot-only environment; no AI/MCP dependencies installed."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

assert importlib.util.find_spec('mcp') is None
assert importlib.util.find_spec('telethon') is None
spec = importlib.util.spec_from_file_location('nodera_bot', Path(__file__).resolve().parents[1] / 'protocol-images/nodera-bot/bot.py')
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)


async def main():
    settings = {'modules': {'nodera-bot': True, 'ai-agent': False}, 'bot_token': 'isolated',
                'accounts': [{'telegram_id': 11, 'bot_access': True}]}
    sent = []
    calls = 0
    async def transport(settings, method, **payload):
        nonlocal calls
        if method == 'getWebhookInfo': return {'url': ''}
        if method == 'getUpdates':
            calls += 1
            if calls > 1: raise asyncio.CancelledError()
            return [
                {'update_id': 1, 'message': {'from': {'id': 11}, 'chat': {'type': 'group'}, 'text': '/status'}},
                {'update_id': 2, 'message': {'from': {'id': 22}, 'chat': {'type': 'private'}, 'text': '/status'}},
                {'update_id': 3, 'message': {'from': {'id': 11}, 'chat': {'type': 'private'}, 'text': '/status'}},
            ]
        if method == 'sendMessage': sent.append(payload); return {}
        raise AssertionError(method)
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), patch.object(bot, 'bot_api', transport):
        try: await bot.bot(settings)
        except asyncio.CancelledError: pass
        assert json.loads((Path(folder) / 'offset.json').read_text())['offset'] == 4
    assert len(sent) == 1 and sent[0]['chat_id'] == 11
    assert 'NoderaBot' in sent[0]['text']
    print('standalone bot commands, private allowlist, persisted offset and absence of MCP/Telethon: passed')


asyncio.run(main())
