import asyncio
import unittest
import tempfile
import os
import json
import httpx
from unittest.mock import AsyncMock, patch

from tests.api.support import load_module

bot = load_module('assistant_dialog_bot', 'protocol-images/nodera-bot/bot.py')


class BotDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_and_all_screen_chunks_are_removed_but_ai_conversation_stays(self):
        sent, deleted = [], []
        steps = ('/status', '/ai Explain', 'Another question', '/confirm abcdef12', '/menu')
        polls = 0
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                if polls:
                    async with asyncio.timeout(5):
                        while len(sent) < polls + 1: await asyncio.sleep(.01)
                    await asyncio.sleep(.02)
                if polls == len(steps): raise asyncio.CancelledError()
                polls += 1
                return [{'update_id':polls, 'message':{'message_id':polls,'from':{'id':11},
                        'chat':{'id':11,'type':'private'},'text':steps[polls-1]}}]
            if method == 'sendMessage':
                sent.append(payload)
                return {'message_id':100+len(sent)}
            if method == 'deleteMessage': deleted.append(payload['message_id'])
            return True
        async def server(settings, actor, command):
            return 'Status\n' + 'x' * 4000 if command == '/status' else 'Confirmed'
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY':folder}), \
             patch.object(bot,'bot_api',side_effect=transport), patch.object(bot,'identify',new=AsyncMock(return_value=True)), \
             patch.object(bot,'remember_incoming',new=AsyncMock()), patch.object(bot,'command_menu',new=AsyncMock()), \
             patch.object(bot,'server_command',side_effect=server), patch.object(bot,'agent_request',new=AsyncMock(return_value='AI answer')):
            with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(bot.bot({'modules':{'nodera-bot':True},'bot_token':'fixture'}),15)
        self.assertTrue({1,4,5,101,102,105}.issubset(deleted), deleted)
        self.assertTrue({2,3,103,104}.isdisjoint(deleted), deleted)
        self.assertEqual(sent[2]['text'],'AI answer')
        self.assertEqual(sent[3]['text'],'AI answer')
        self.assertTrue(sent[0]['text'].startswith('<b>Status</b>\n\n'))

    async def test_agent_transport_sends_alert_reference_and_reports_expired_buttons(self):
        actual_client = httpx.AsyncClient
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(404, json={'detail': 'Кнопка алерта устарела'})
        with patch.object(bot.httpx, 'AsyncClient', side_effect=lambda **kwargs: actual_client(transport=httpx.MockTransport(transport), **kwargs)):
            answer = await bot.agent_request({'control_token': 'fixture'}, 11, '', alert={'token': 'a' * 32, 'mode': 'deep'})
        self.assertEqual(answer, 'Кнопка алерта устарела')
        self.assertEqual(requests[0]['alert'], {'token': 'a' * 32, 'mode': 'deep'})
        self.assertEqual(requests[0]['prompt'], '')

    async def test_alert_callback_survives_restart_and_passes_only_reference_to_ai(self):
        delivered = asyncio.Event()
        polls = 0
        token = 'a' * 32
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                polls += 1
                if polls == 1:
                    return [{'update_id': 1, 'callback_query': {'id': 'callback', 'from': {'id': 11},
                             'data': f'alert:{token}:deep', 'message': {'chat': {'type': 'private', 'id': 11}, 'text': 'forged text'}}}]
                await asyncio.wait_for(delivered.wait(), 2)
                raise asyncio.CancelledError()
            if method == 'sendMessage' and 'Разбор готов' in payload['text']: delivered.set()
            return True
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), \
             patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'identify', new=AsyncMock(return_value=True)), \
             patch.object(bot, 'command_menu', new=AsyncMock()), patch.object(bot, 'agent_request', new=AsyncMock(return_value='Разбор готов')) as agent:
            with self.assertRaises(asyncio.CancelledError):
                await bot.bot({'modules': {'nodera-bot': True}, 'bot_token': 'fixture'})
            agent.assert_awaited_once_with({'modules': {'nodera-bot': True}, 'bot_token': 'fixture'}, 11, '', alert={'token': token, 'mode': 'deep'})

    async def test_typical_requests_bypass_ai_including_polite_natural_language(self):
        with patch.object(bot, 'agent_request', new=AsyncMock()) as agent, patch.object(bot, 'server_command', new=AsyncMock(return_value='Данные сервера')) as server:
            for phrase, command in bot.DIRECT_COMMANDS.items():
                if command in ('/hello', '/thanks', '/help', '/connect', '/id'): continue
                with self.subTest(phrase=phrase):
                    result = await bot.reply({}, {'id': 11}, phrase, True)
                    server.assert_awaited_with({}, 11, command)
                    self.assertTrue(result)
            for text, command in [('Покажи ресурсы сервера, пожалуйста!', '/resources'),
                                  ('Перезапусти службу mihomo', '/service mihomo restart'), ('логи mihomo', '/logs mihomo')]:
                await bot.reply({}, {'id': 11}, text, True)
                server.assert_awaited_with({}, 11, command)
            for text in ('Привет!', 'Спасибо', 'как подключиться', 'что ты умеешь'):
                self.assertTrue(await bot.reply({}, {'id': 11}, text, True))
            agent.assert_not_awaited()

    async def test_analytical_and_explicit_ai_questions_keep_the_original_prompt(self):
        with patch.object(bot, 'agent_request', new=AsyncMock(return_value='AI')) as agent, patch.object(bot, 'server_command', new=AsyncMock()) as server, patch.object(bot, 'bot_api', new=AsyncMock()):
            for text in ('Почему сервер тормозит?', 'Сравни модели AI', 'Не перезапускай mihomo', 'Перезапусти mihomo и останови ssh'):
                self.assertEqual(bot.direct_prompt(text), text)
                self.assertEqual(await bot.reply({}, {'id': 11}, text, True), 'AI')
                agent.assert_awaited_with({}, 11, text)
            self.assertEqual(await bot.reply({}, {'id': 11}, '/ai статус сервера', True), 'AI')
            agent.assert_awaited_with({}, 11, 'статус сервера')
            server.assert_not_awaited()

    async def test_automatic_requests_keep_subscription_and_guest_access_boundaries(self):
        with patch.object(bot, 'agent_request', new=AsyncMock()) as agent, patch.object(bot, 'server_command', new=AsyncMock(return_value='Своя подписка')) as server:
            for phrase in ('мой профиль', 'мои устройства', 'мой трафик', 'моя подписка'):
                self.assertEqual(await bot.reply({}, {'id': 11}, phrase, 'subscription'), 'Своя подписка')
            self.assertEqual(await bot.reply({}, {'id': 11}, 'как подключиться', 'subscription'), 'Своя подписка')
            server.assert_awaited_with({}, 11, '/connect')
            server.reset_mock()
            for role in ('subscription', 'chat', False):
                for phrase in ('ресурсы', 'перезапусти mihomo', 'логи mihomo', 'безопасность'):
                    await bot.reply({}, {'id': 11}, phrase, role)
            await bot.reply({}, {'id': 11}, '/ai Привет', 'subscription')
            server.assert_not_awaited()
            agent.assert_not_awaited()

    async def test_natural_command_stays_responsive_while_explicit_ai_is_waiting(self):
        command_sent, finished = asyncio.Event(), asyncio.Event()
        polls = 0
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                polls += 1
                if polls == 1:
                    return [{'update_id': at, 'message': {'chat': {'type': 'private'}, 'from': {'id': 11}, 'text': text}}
                            for at, text in enumerate(('/ai Подумай', 'статус сервера'), 1)]
                await asyncio.wait_for(finished.wait(), 2)
                raise asyncio.CancelledError()
            if method == 'sendMessage':
                if 'Статус готов' in payload['text']: command_sent.set()
                if 'Ответ готов' in payload['text']: finished.set()
            return True
        async def answer(*_):
            await asyncio.wait_for(command_sent.wait(), 1)
            return 'Ответ готов'
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), \
                patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'identify', new=AsyncMock(return_value=True)), \
                patch.object(bot, 'command_menu', new=AsyncMock()), patch.object(bot, 'agent_request', side_effect=answer), \
                patch.object(bot, 'server_command', new=AsyncMock(return_value='Статус готов')) as server:
            with self.assertRaises(asyncio.CancelledError): await bot.bot({'modules': {'nodera-bot': True}, 'bot_token': 'fixture'})
            server.assert_awaited_once_with({'modules': {'nodera-bot': True}, 'bot_token': 'fixture'}, 11, '/status')
        self.assertTrue(command_sent.is_set() and finished.is_set())

    async def test_real_polling_loop_delivers_second_user_before_first_ai_answer_finishes(self):
        fast, slow, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        polls = 0
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                polls += 1
                if polls == 1:
                    return [{'update_id': actor, 'message': {'chat': {'type':'private'}, 'from': {'id':actor}, 'text':'fixture'}} for actor in (11,22)]
                await asyncio.wait_for(fast.wait(), 1)
                self.assertFalse(slow.is_set())
                release.set()
                await asyncio.wait_for(slow.wait(), 1)
                raise asyncio.CancelledError()
            if method == 'sendMessage':
                (slow if payload['chat_id'] == 11 else fast).set()
            return True
        async def answer(settings, sender, prompt, access):
            if sender['id'] == 11: await release.wait()
            return 'fixture reply'
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY':folder}), \
             patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'identify', new=AsyncMock(return_value='chat')), \
             patch.object(bot, 'command_menu', new=AsyncMock()), patch.object(bot, 'reply', side_effect=answer):
            with self.assertRaises(asyncio.CancelledError):
                await bot.bot({'modules':{'nodera-bot':True}, 'bot_token':'fixture', 'control_token':'fixture'})
        self.assertTrue(fast.is_set() and slow.is_set())
    async def test_dispatcher_keeps_other_users_responsive_and_preserves_each_dialog_order(self):
        dispatcher = bot.MessageDispatcher(capacity=4, per_actor=2)
        release = asyncio.Event(); first = asyncio.Event(); other = asyncio.Event(); order = []
        async def slow():
            order.append('A1-start'); first.set(); await release.wait(); order.append('A1-end')
        async def next_a(): order.append('A2')
        async def user_b(): order.append('B'); other.set()
        self.assertTrue(dispatcher.submit(11, slow))
        await first.wait()
        self.assertTrue(dispatcher.submit(11, next_a))
        self.assertFalse(dispatcher.submit(11, next_a))
        self.assertTrue(dispatcher.submit(22, user_b))
        await asyncio.wait_for(other.wait(), 1)
        self.assertNotIn('A2', order)
        release.set(); await asyncio.gather(*list(dispatcher.tasks))
        self.assertLess(order.index('B'), order.index('A1-end'))
        self.assertLess(order.index('A1-end'), order.index('A2'))
        await dispatcher.close()
        self.assertFalse(dispatcher.tasks or dispatcher.pending or dispatcher.locks)

    async def test_shutdown_cancels_active_and_queued_messages_without_replaying_them(self):
        dispatcher = bot.MessageDispatcher()
        started = asyncio.Event()
        async def slow(): started.set(); await asyncio.Event().wait()
        queued = AsyncMock()
        dispatcher.submit(11, slow); dispatcher.submit(11, queued)
        await started.wait(); await dispatcher.close()
        queued.assert_not_awaited()
        self.assertFalse(dispatcher.pending)

    def assert_typing_stopped(self):
        self.assertFalse(any(task.get_name().startswith('nodera-typing-') for task in asyncio.all_tasks()))

    async def test_typing_is_renewed_during_ai_request_and_stops_after_answer(self):
        renewed = asyncio.Event()
        actions = []

        async def transport(settings, method, **payload):
            self.assertEqual(method, 'sendChatAction')
            actions.append(payload)
            if len(actions) >= 2: renewed.set()
            return True

        async def answer(*_):
            await asyncio.wait_for(renewed.wait(), timeout=2)
            return 'Ответ Нодеры'

        with patch.object(bot, 'TYPING_INTERVAL', 0.01), patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'agent_request', side_effect=answer):
            result = await bot.reply({}, {'id': 11}, 'Подумай над вопросом', 'chat')
            self.assertEqual(result, 'Ответ Нодеры')
            self.assertGreaterEqual(len(actions), 2)
            self.assertTrue(all(action == {'chat_id': 11, 'action': 'typing'} for action in actions))
            count = len(actions)
            await asyncio.sleep(0.03)
            self.assertEqual(len(actions), count)
        self.assert_typing_stopped()

    async def test_typing_failure_does_not_break_ai_answer_and_cancellation_cleans_up(self):
        with patch.object(bot, 'bot_api', new=AsyncMock(side_effect=RuntimeError('Telegram unavailable'))), patch.object(bot, 'agent_request', new=AsyncMock(return_value='OK')):
            self.assertEqual(await bot.reply({}, {'id': 11}, 'Вопрос', True), 'OK')
        self.assert_typing_stopped()
        started = asyncio.Event()

        async def pending(*_):
            started.set()
            await asyncio.Event().wait()

        with patch.object(bot, 'bot_api', new=AsyncMock(return_value=True)), patch.object(bot, 'agent_request', side_effect=pending):
            task = asyncio.create_task(bot.reply({}, {'id': 11}, 'Долгий вопрос', True))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assert_typing_stopped()

    async def test_ai_error_is_friendly_and_also_stops_typing(self):
        with patch.object(bot, 'bot_api', new=AsyncMock(return_value=True)), patch.object(bot, 'agent_request', new=AsyncMock(side_effect=RuntimeError('provider failed'))):
            result = await bot.reply({}, {'id': 11}, 'Вопрос', True)
        self.assertIn('не получилось', result)
        self.assertNotIn('AI-agent', result)
        self.assert_typing_stopped()

    async def test_start_and_help_introduce_assistant_and_only_list_information(self):
        for role in (True, 'chat'):
            with patch.object(bot, 'agent_request', new=AsyncMock()) as agent, patch.object(bot, 'server_command', new=AsyncMock()) as server:
                for command in ('/start', '/help'):
                    result = await bot.reply({}, {'id': 11}, command, role)
                    self.assertIn('AI-ассистент', result)
                    for hidden in ('/service ', '/confirm', '/cancel', '/operation', '<ID>'):
                        self.assertNotIn(hidden, result)
                    self.assertNotIn('модуль', result)
                agent.assert_not_awaited()
                server.assert_not_awaited()
        self.assertIn('/status', bot.assistant_help(True))
        self.assertNotIn('/status', bot.assistant_help(False))

    async def test_hidden_management_commands_still_dispatch_but_service_listing_is_read_only(self):
        with patch.object(bot, 'server_command', new=AsyncMock(return_value='Подготовлено')) as server:
            result = await bot.reply({}, {'id': 11}, '/service fixture restart', True)
            self.assertEqual(result, 'Подготовлено')
            server.assert_awaited_once_with({}, 11, '/service fixture restart')
        raw = 'Службы (ID · состояние · действия):\nfixture · running · start, stop, restart\n\n/service <ID> <start|stop|restart>'
        text = bot.informational_reply('/services', raw)
        self.assertEqual(text, 'Состояние служб:\nfixture · running')
        self.assertIsNone(bot.inline_keyboard(text, '/services', True, {}))

    async def test_guest_only_id_and_subscription_only_customer_never_call_ai(self):
        with patch.object(bot, 'agent_request', new=AsyncMock()) as agent, patch.object(bot, 'server_command', new=AsyncMock(return_value='https://example.com/s/private')) as server:
            for prompt in ('/start', '/help', '/models', '/status', '/subscription', '/userid', 'Hello'):
                self.assertEqual(await bot.reply({}, {'id': 22}, prompt, False), '')
            self.assertIn('22', await bot.reply({}, {'id': 22}, '/id', False))
            self.assertIn('/subscription', await bot.reply({}, {'id': 22}, 'Hello', 'subscription'))
            self.assertEqual(await bot.reply({}, {'id': 22}, '/subscription', 'subscription'), 'https://example.com/s/private')
            server.assert_awaited_once_with({}, 22, '/subscription')
            agent.assert_not_awaited()
