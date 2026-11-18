import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.api.support import load_module

bot = load_module('durable_inbox_bot', 'protocol-images/nodera-bot/bot.py')


class DurableInboxTests(unittest.IsolatedAsyncioTestCase):
    def test_switching_bot_uses_its_own_offset_and_verified_legacy_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            legacy = bot.DurableInbox(base)
            (base / 'offset.json').write_text('{"offset": 1000}')
            for identity, token in enumerate(('123:old', '456:new'), 1):
                key = f'message-{identity}'
                legacy.put(key, {'order': identity, 'sender': {'id': 11}, 'request_id': bot.bot_request_id(token, 11, key)})
            new = bot.bot_state_directory(base, '456:new')
            self.assertFalse((new / 'offset.json').exists())
            self.assertEqual([path.stem for path in (new / 'inbox').glob('*.json')], ['message-2'])
            self.assertEqual([path.stem for path in legacy.root.glob('*.json')], ['message-1'])
            self.assertEqual(bot.bot_state_directory(base, '456:rotated'), new)
            old = bot.bot_state_directory(base, '123:old')
            self.assertNotEqual(old, new)
            self.assertEqual([path.stem for path in (old / 'inbox').glob('*.json')], ['message-1'])

    async def test_permanent_telegram_rejection_does_not_block_next_message(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = bot.DurableInbox(Path(directory))
            for index in (1, 2):
                inbox.put(f'message-{index}', {'order': index, 'sender': {'id': 11}})
            inbox.acknowledge(3)
            dispatcher = bot.MessageDispatcher()
            seen = []

            async def deliver(row, key):
                seen.append(row['order'])
                if row['order'] == 1:
                    raise bot.TelegramAPIError('sendMessage', 403)

            for _ in range(2):
                inbox.pump(dispatcher, deliver)
                await asyncio.gather(*list(dispatcher.tasks))
            self.assertEqual(seen, [1, 2])
            self.assertFalse(list(inbox.root.glob('*.json')))
            await dispatcher.close()

    async def test_transient_permission_lookup_keeps_the_message_queued(self):
        import httpx
        actual = httpx.AsyncClient
        def transport(request): return httpx.Response(503, json={'detail':'temporarily unavailable'})
        with patch.object(bot, 'observed_profile', return_value={}), \
             patch.object(bot.httpx, 'AsyncClient', side_effect=lambda **kwargs: actual(transport=httpx.MockTransport(transport), **kwargs)):
            with self.assertRaises(bot.DeliveryPending):
                await bot.identify({'control_token':'fixture'}, {'id':11})

    async def test_failed_delivery_keeps_fifo_and_restarts_keep_accepted_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = bot.DurableInbox(Path(directory))
            for index in range(5):
                self.assertTrue(inbox.put('message-'+str(index), {'order':index,'sender':{'id':11},'prompt':str(index),'request_id':str(index)*32}))
            inbox.acknowledge(10)
            dispatcher = bot.MessageDispatcher()
            seen = []
            async def fail(row, key): seen.append(row['order']); raise OSError('delivery offline')
            inbox.pump(dispatcher, fail)
            await asyncio.gather(*list(dispatcher.tasks))
            self.assertEqual(seen, [0])
            inbox.pump(dispatcher, fail)
            await asyncio.sleep(0)
            self.assertEqual(seen, [0])
            await dispatcher.close()
            restored = bot.DurableInbox(Path(directory)); restored.acknowledge(10)
            seen.clear(); dispatcher = bot.MessageDispatcher()
            async def deliver(row, key): seen.append(row['order'])
            for _ in range(5):
                restored.pump(dispatcher, deliver)
                await asyncio.gather(*list(dispatcher.tasks)); await asyncio.sleep(0)
            self.assertEqual(seen, list(range(5)))
            self.assertFalse(list(restored.root.glob('*.json')))
            await dispatcher.close()

    async def test_delivery_checkpoint_prevents_duplicate_replay_before_offset_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = bot.DurableInbox(Path(directory))
            row = {'order':1,'sender':{'id':11},'prompt':'question','request_id':'a'*32}
            inbox.put('message-1', row)
            dispatcher = bot.MessageDispatcher(); seen=[]
            async def deliver(row,key): seen.append(row['prompt'])
            inbox.pump(dispatcher,deliver); await asyncio.gather(*list(dispatcher.tasks))
            restored = bot.DurableInbox(Path(directory))
            restored.put('message-1',row); restored.pump(dispatcher,deliver)
            await asyncio.sleep(0)
            self.assertEqual(seen,['question'])
            restored.acknowledge(2)
            self.assertFalse(list(restored.root.glob('*.json')))
            await dispatcher.close()

    def test_compact_menu_keeps_commands_in_scoped_sections(self):
        self.assertEqual(bot.reply_keyboard(True)['keyboard'], [[{'text':'Меню'}]])
        actions={}; main=bot.inline_keyboard('', '/menu', 'subscription', actions)
        self.assertLessEqual(sum(map(len,main['inline_keyboard'])),4)
        self.assertNotIn('/menu server',actions.values())
        actions={}; apps=bot.inline_keyboard('', '/apps', 'subscription', actions)
        self.assertIn('/apps windows',actions.values())
        self.assertEqual(sum(map(len,apps['inline_keyboard'])),5)

    async def test_bot_polls_the_same_durable_request_and_reports_waiting_once(self):
        import httpx
        actual = httpx.AsyncClient
        calls, notices = [], []
        identity = 'b' * 32
        reads = 0
        def transport(request):
            nonlocal reads
            calls.append(request)
            if request.method == 'POST': return httpx.Response(202, json={'id':identity,'state':'queued'})
            reads += 1
            if reads == 1: return httpx.Response(200, json={'id':identity,'state':'queued'})
            return httpx.Response(200, json={'id':identity,'state':'done','output':{'result':{'answer':'ready'}}})
        async def notice(*args, **kwargs): notices.append(kwargs)
        clock=iter([1,4])
        with patch.object(bot.httpx, 'AsyncClient', side_effect=lambda **kwargs: actual(transport=httpx.MockTransport(transport), **kwargs)), \
             patch.object(bot,'bot_api',side_effect=notice), patch.object(bot,'time',SimpleNamespace(monotonic=lambda:next(clock))):
            token=bot.incoming_request.set(identity)
            try: answer=await bot.agent_request({'control_token':'fixture'},11,'question')
            finally: bot.incoming_request.reset(token)
        self.assertEqual(answer,'ready')
        self.assertEqual(sum(row.method=='POST' for row in calls),1)
        self.assertTrue(all(row.url.path.endswith(identity) for row in calls if row.method=='GET'))
        self.assertEqual(len(notices),1)
