"""Run in the NoderaBot-only environment; no AI/MCP dependencies installed."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch, AsyncMock

assert importlib.util.find_spec('mcp') is None
assert importlib.util.find_spec('telethon') is None
spec = importlib.util.spec_from_file_location('nodera_bot', Path(__file__).resolve().parents[1] / 'protocol-images/nodera-bot/bot.py')
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)


async def main():
    settings = {'modules': {'nodera-bot': True, 'ai-agent': False}, 'bot_token': 'isolated',
                'accounts': []}  # The running service can start before the first admin is added.
    sent = []
    calls = 0
    async def transport(settings, method, **payload):
        nonlocal calls
        if method == 'getWebhookInfo': return {'url': ''}
        if method == 'setMyCommands': return True
        if method == 'getUpdates':
            calls += 1
            if calls > 1: raise asyncio.CancelledError()
            return [
                {'update_id': 1, 'message': {'from': {'id': 11}, 'chat': {'type': 'group'}, 'text': '/status'}},
                {'update_id': 2, 'message': {'from': {'id': 22}, 'chat': {'type': 'private'}, 'text': '/status'}},
                {'update_id': 3, 'message': {'from': {'id': 11}, 'chat': {'type': 'private'}, 'text': '/status'}},
                {'update_id': 4, 'message': {'from': {'id': 23, 'username': 'visitor'}, 'chat': {'type': 'private'}, 'text': '/id'}},
                {'update_id': 5, 'message': {'from': {'id': 24}, 'chat': {'type': 'private'}, 'photo': [{}]}},
            ]
        if method == 'sendMessage': sent.append(payload); return {}
        raise AssertionError(method)
    seen = set()
    async def identify(settings, sender):
        seen.add(sender['id'])
        return sender['id'] == 11
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), patch.object(bot, 'bot_api', transport), patch.object(bot, 'identify', identify), patch.object(bot, 'server_command', new=AsyncMock(return_value='NoderaBot server status')) as server, patch.object(bot, 'agent_request', new=AsyncMock(side_effect=AssertionError('Unexpected AI request'))):
        try: await bot.bot(settings)
        except asyncio.CancelledError: pass
        assert json.loads((Path(folder) / 'offset.json').read_text())['offset'] == 6
        server.assert_awaited_once_with(settings, 11, '/status')
    assert len(sent) == 3
    assert 24 in seen
    assert 'NoderaBot' in next(row['text'] for row in sent if row['chat_id']==11)
    assert '23' in next(row['text'] for row in sent if row['chat_id']==23)
    assert 'NoderaBot' not in next(row['text'] for row in sent if row['chat_id']==22)
    print('standalone guest replies, self ID, current admin roles, private chats, persisted offset and absence of MCP/Telethon: passed')


asyncio.run(main())
