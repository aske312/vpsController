import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from tests.api.support import load_module

bot = load_module('assistant_dialog_bot', 'protocol-images/nodera-bot/bot.py')


class BotDialogTests(unittest.IsolatedAsyncioTestCase):
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
        for role in (True, 'chat', False):
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
