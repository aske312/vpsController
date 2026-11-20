import asyncio
from contextlib import asynccontextmanager, contextmanager
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch
import uuid

import httpx
from fastapi.testclient import TestClient
from tests.api.support import api, load_module
from tests.api import test_assistant_control as fixtures
import assistant_bot_history as history
import assistant_control as control
import assistant_memory as memory
import assistant_personalization as personalization

bot = load_module('nodera_history_bot', 'protocol-images/nodera-bot/bot.py')
runner = load_module('nodera_history_runner', 'protocol-images/ai-agent/runner.py')


class BotHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)
        self.settings = {'revision': 'a' * 32, 'memory_enabled': True, 'memory_days': 30,
                         'control_token': 'c' * 40, 'bot_token': '123456:' + 'b' * 32,
                         'modules': {'ai-agent': True, 'nodera-bot': True},
                         'accounts': [{'telegram_id': 11, 'name': 'First', 'role': 'user'},
                                      {'telegram_id': 22, 'name': 'Second', 'role': 'user'}]}
        self.enterContext(patch.object(control, 'read', side_effect=lambda _: dict(self.settings)))
        self.enterContext(patch.object(control, '_read', side_effect=lambda _: dict(self.settings)))
        self.enterContext(patch.object(control, 'require_installed'))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.client = TestClient(api.app)

    def save(self, identity, text='Command response', actor='11', **kwargs):
        return history.append(self.env, self.settings, actor, history.Message(
            message_id=identity, text=text, role=kwargs.pop('role', 'assistant'), date=kwargs.pop('date', int(time.time())), **kwargs))

    def read(self, actor='11', **kwargs):
        return history.read(self.env, self.settings, actor, history.Read(**kwargs))

    def token(self, actor='11'):
        return {'Authorization': 'Bearer ' + control.mcp_transport_token(self.settings['control_token'], 'nodera-dialog', actor),
                'X-MCP-Actor': actor}

    def test_command_alert_edit_search_and_pagination_keep_message_identity(self):
        self.save(1, 'User request', role='user')
        self.save(2, 'Service stopped')
        self.save(3, 'Diagnostic alert', kind='alert', reply_to_message_id=1)
        self.save(1, 'Edited request', role='user')
        page = self.read(limit=2)
        self.assertEqual([row['message_id'] for row in page['messages']], [2, 3])
        self.assertEqual(page['next_before_id'], 2)
        self.assertEqual(self.read(before_id=2)['messages'][0]['text'], 'Edited request')
        self.assertEqual(self.read(query='ALERT')['messages'][0]['kind'], 'alert')
        self.assertFalse(page['history_complete'])

    def test_read_token_and_actor_are_scoped_and_panel_can_explicitly_select(self):
        self.save(1, 'private first')
        self.save(1, 'private second', actor='22')
        url = '/api/assistant/bot-history/read'
        self.assertEqual(self.client.post(url, json={}).status_code, 401)
        malformed = self.client.post(url, json={'limit': 'not an integer'})
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(malformed.headers.get('www-authenticate'), 'Bearer')
        self.assertEqual(self.client.post(url, headers=self.token(), json={'telegram_id': 22}).status_code, 403)
        self.assertEqual(self.client.post(url, headers={**self.token(), 'X-MCP-Actor': '22'}, json={}).status_code, 401)
        own = self.client.post(url, headers=self.token(), json={})
        self.assertEqual(own.status_code, 200, own.text)
        self.assertNotIn('private second', own.text)
        panel = self.client.post(url, headers=self.token('panel'), json={}).json()
        self.assertEqual([row['telegram_id'] for row in panel['dialogs']], [11, 22])
        self.assertNotIn('private first', json.dumps(panel))
        self.assertEqual(self.client.post(url, headers=self.token('panel'), json={'telegram_id': 22}).json()['messages'][0]['text'], 'private second')
        self.settings['memory_enabled'] = False
        self.assertEqual(self.client.post(url, headers=self.token(), json={}).status_code, 403)
        self.assertFalse(self.save(2)['stored'])

    def test_record_endpoint_requires_current_bot_identity_and_never_accepts_model_read_token(self):
        payload = history.Message(message_id=1, text='Delivered', role='assistant', date=int(time.time())).model_dump()
        headers = {'Authorization': 'Bearer ' + self.settings['control_token'], 'X-MCP-Actor': '11',
                   'X-Nodera-Bot': history.bot_key(self.settings)}
        url = '/api/assistant/bot-history/message'
        self.assertEqual(self.client.post(url, headers=self.token(), json=payload).status_code, 401)
        self.assertEqual(self.client.post(url, headers={**headers, 'X-Nodera-Bot': 'old'}, json=payload).status_code, 403)
        self.assertEqual(self.client.post(url, headers=headers, json=payload).status_code, 200)
        self.assertEqual(self.read()['messages'][0]['text'], 'Delivered')
        self.settings['accounts'][0]['role'] = 'guest'
        self.assertEqual(self.client.post('/api/assistant/bot-history/read', headers=self.token(), json={}).status_code, 403)

    def test_expiry_forget_bot_rotation_and_configured_secret_redaction(self):
        self.save(1, date=int(time.time() - 31 * 86400))
        self.save(2, self.settings['control_token'] + ' password=very-private')
        result = self.read()
        self.assertEqual(len(result['messages']), 1)
        self.assertNotIn('very-private', json.dumps(result))
        self.assertNotIn(self.settings['control_token'], json.dumps(result))
        personalization.update(self.env, self.settings, '11', personalization.MemoryAction(operation='forget'))
        self.assertEqual(self.read()['messages'], [])
        self.settings['bot_token'] = '654321:' + 'n' * 32
        self.assertEqual(self.read()['messages'], [])

    def test_bounded_unicode_output_and_legacy_ai_cache_have_honest_delivery(self):
        for identity in range(1, 9): self.save(identity, '界' * 8000)
        result = self.read(limit=50)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 24000)
        self.assertTrue(result['has_more'])
        self.assertTrue(result['messages'][-1]['truncated'])
        self.assertGreater(personalization.usage(self.env)['used_bytes'], 0)
        memory.append(self.env, uuid.uuid4().hex, '11', 'bot', 'Old question', [], {'answer': 'Old AI answer'}, 'model', self.settings)
        legacy = self.read(query='Old AI answer')
        self.assertEqual(legacy['messages'], [])
        self.assertEqual(legacy['cached_ai_turns'][0]['answer'], 'Old AI answer')
        self.assertEqual(legacy['cached_ai_turns'][0]['delivery'], 'not_verified')

    def test_quoted_bot_context_survives_even_when_original_message_is_outside_the_page(self):
        self.save(1, 'Old bot answer')
        for identity in range(2, 12): self.save(identity, 'Later message')
        self.save(12, 'Explain that', role='user', reply_to_message_id=1, reply_to_text='Old bot answer')
        recent = self.read(limit=1)
        self.assertEqual(recent['messages'][0]['reply_to_text'], 'Old bot answer')
        self.assertEqual(self.read(query='Old bot answer', limit=1)['messages'][0]['message_id'], 12)

    def test_delivered_owner_alert_enters_the_same_recipient_history(self):
        import telegram_alerts as alerts
        self.settings['accounts'][0]['role'] = 'owner'
        state = alerts.load(self.env)
        alerts.configure(self.env, alerts.Settings(enabled=True, owner_id=11, expected_revision=state['revision']))
        alerts.test(self.env)
        with patch.object(alerts, 'send', return_value={'state': 'sent', 'message_id': 91, 'date': int(time.time())}):
            alerts.deliver(self.env)
        self.assertEqual(self.read()['messages'][0]['kind'], 'alert')


class BotHistoryRuntimeTests(unittest.TestCase):
    setUp = fixtures.AssistantTests.setUp
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def test_history_does_not_reenter_the_linux_application_lock(self):
        payload = self.payload()
        payload['accounts'] = [{'telegram_id': 11, 'role': 'user'}]
        self.save(payload)
        settings = control.read(api)
        settings['bot_token'] = '123456:' + 'f' * 32
        control.store(api, settings)
        original, held = history.operations.locked, False
        @contextmanager
        def non_reentrant(root):
            nonlocal held
            self.assertFalse(held, 'Linux flock must not be acquired twice in one call')
            held = True
            try:
                with original(root): yield
            finally: held = False
        with patch.object(history.operations, 'locked', side_effect=non_reentrant):
            history.append(api, settings, '11', history.Message(message_id=1, role='assistant', text='Delivered', date=int(time.time())))
            result = history.read(api, settings, '11', history.Read())
        self.assertEqual(result['messages'][0]['text'], 'Delivered')

    def test_recent_bot_messages_reach_the_provider_settings_and_memory_off_removes_access(self):
        payload = self.payload()
        payload['accounts'] = [{'telegram_id': 11, 'role': 'user'}]
        self.save(payload)
        self.observe.return_value['active'] = True
        value = control.read(api)
        value['bot_token'] = '123456:' + 'f' * 32
        control.store(api, value)
        history.append(api, value, '11', history.Message(message_id=1, role='assistant', text='Actual command response', date=int(time.time())))
        with self.assertRaises(HTTPException) as obsolete:
            control.runtime_request(api, '--chat', {'prompt': 'Hello', 'actor': '11'})
        self.assertEqual(obsolete.exception.status_code, 409)
        with patch.object(control.subprocess, 'Popen') as factory, \
             patch.object(control, 'version_at_least', side_effect=lambda file, minimum: minimum == (1, 51, 0)):
            process = factory.return_value.__enter__.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps({'ok': True, 'result': {'answer': 'OK'}}), '')
            control.runtime_request(api, '--chat', {'prompt': 'What did you send?', 'actor': '11'})
            sent = json.loads(process.communicate.call_args.args[0])['settings']
            self.assertEqual(sent['bot_dialog_context']['messages'][0]['text'], 'Actual command response')
            self.assertEqual(sent['bot_dialog_token'], control.mcp_transport_token(value['control_token'], 'nodera-dialog', '11'))
            value['memory_enabled'] = False
            control.store(api, value)
            control.runtime_request(api, '--chat', {'prompt': 'What did you send?', 'actor': '11'})
            disabled = json.loads(process.communicate.call_args.args[0])['settings']
            self.assertNotIn('bot_dialog_token', disabled)
            self.assertNotIn('bot_dialog_context', disabled)


class BotHistoryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_requests_exact_confirmation_and_waits_for_the_user(self):
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example', 'model': 'model', 'api_key': 'fixture',
                    'mcp': {}, 'accounts': [], 'web_enabled': False, 'personalization_enabled': False,
                    'confirmation_request_token': 'scoped-confirmation'}
        identity = 'a' * 32
        @asynccontextmanager
        async def connections(*_): yield runner.native_connections(settings, 'panel')
        calls, seen = [], []
        async def provider(_settings, messages, tools):
            seen.append(messages)
            if len(seen) == 1:
                self.assertIn('request_confirmation', [row['function']['name'] for row in tools])
                return {'tool_calls': [{'id': 'present', 'type': 'function', 'function': {'name': 'request_confirmation',
                    'arguments': json.dumps({'operation_ids': [identity, 'b'*32, 'c'*32, 'd'*32, 'e'*32], 'title': 'Apply routing', 'reason': 'Reconnect clients'})}}]}
            self.assertIn('awaiting_user', messages[-1]['content'])
            return {'content': 'Waiting for your decision'}
        def transport(request):
            calls.append(request)
            return httpx.Response(200, json={'operation_id': identity, 'status': 'awaiting_user', 'confirmation_required': True})
        client = httpx.AsyncClient
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider), \
                patch.object(runner.httpx, 'AsyncClient', side_effect=lambda **kw: client(**kw, transport=httpx.MockTransport(transport))):
            result = await runner.chat(settings, 'panel', 'Request confirmation for the prepared action')
        self.assertEqual(result['tools_used'], ['request_confirmation'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, '/api/assistant/confirmation-request')
        self.assertEqual(calls[0].headers['Authorization'], 'Bearer scoped-confirmation')
        self.assertEqual(calls[0].headers['X-MCP-Actor'], 'panel')
        self.assertEqual(json.loads(calls[0].content)['operation_ids'], [identity, 'b'*32, 'c'*32, 'd'*32, 'e'*32])
        self.assertNotIn('request_confirmation', runner.native_connections({**settings, 'confirmation_request_token': ''}, 'panel'))

    async def test_successful_send_is_recorded_once_and_history_failure_never_replays_delivery(self):
        requests = []
        async def transport(request):
            requests.append(request)
            if request.url.host == 'api.telegram.org':
                return httpx.Response(200, json={'ok': True, 'result': {'message_id': 8, 'date': 1234, 'chat': {'id': 11}, 'text': 'Command response'}})
            return httpx.Response(503, json={})
        client = httpx.AsyncClient
        factory = lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(transport))
        settings = {'bot_token': '123456:fixture', 'control_token': 'fixture-control'}
        with patch.object(bot.httpx, 'AsyncClient', side_effect=factory), self.assertLogs(level='ERROR'):
            result = await bot.bot_api(settings, 'sendMessage', chat_id=11, text='Command response')
        self.assertEqual(result['message_id'], 8)
        self.assertEqual([request.url.host for request in requests], ['api.telegram.org', '127.0.0.1'])
        self.assertEqual(json.loads(requests[1].content)['role'], 'assistant')

    async def test_only_a_reply_from_this_bot_and_this_private_chat_is_imported(self):
        settings = {'bot_token': '123456:fixture'}
        quoted = {'from': {'id': 123456, 'is_bot': True}, 'message_id': 1, 'chat': {'id': 11}, 'text': 'Old alert'}
        message = {'from': {'id': 11}, 'message_id': 2, 'chat': {'id': 11}, 'reply_to_message': quoted, 'text': 'Explain'}
        with patch.object(bot, 'remember_message', new=AsyncMock()) as remember:
            await bot.remember_incoming(settings, message)
            self.assertEqual([call.args[2] for call in remember.call_args_list], ['user', 'assistant'])
            for changed in ({'from': {'id': 999, 'is_bot': True}}, {'chat': {'id': 22}}):
                remember.reset_mock()
                await bot.remember_incoming(settings, {**message, 'reply_to_message': {**quoted, **changed}})
                remember.assert_awaited_once_with(settings, {**message, 'reply_to_message': {**quoted, **changed}}, 'user')

    async def test_native_read_uses_scoped_transport_without_telegram_session_or_vps_tools(self):
        settings = {'mcp': {}, 'accounts': [], 'web_enabled': False, 'personalization_enabled': False,
                    'bot_dialog_token': 'scoped-history-token'}
        tool = runner.native_connections(settings, '11')['nodera_dialog_read']
        client = httpx.AsyncClient
        requests = []
        async def transport(request):
            requests.append(request)
            return httpx.Response(200, json={'messages': [{'text': 'Bot command response'}]})
        with patch.object(runner.httpx, 'AsyncClient', side_effect=lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(transport))):
            result = await tool[0].call_tool('nodera_dialog_read', {'query': 'response'})
        self.assertIn('Bot command response', result.content[0].text)
        self.assertEqual(requests[0].headers['Authorization'], 'Bearer scoped-history-token')
        self.assertEqual(requests[0].headers['X-MCP-Actor'], '11')
        self.assertNotIn('nodera_dialog_read', runner.native_connections({**settings, 'bot_dialog_token': ''}, '11'))

    async def test_model_can_call_history_and_use_its_result_in_the_answer(self):
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example', 'model': 'model', 'api_key': 'fixture',
                    'mcp': {}, 'accounts': [], 'web_enabled': False, 'personalization_enabled': False, 'bot_dialog_token': 'scoped',
                    'bot_dialog_context': {'messages': [{'role': 'assistant', 'text': 'Recent command response'}]}}
        @asynccontextmanager
        async def connections(*_): yield runner.native_connections(settings, 'panel')
        seen = []
        async def provider(_settings, messages, tools):
            seen.append(json.loads(json.dumps(messages)))
            if len(seen) == 1:
                self.assertIn('Recent command response', messages[0]['content'])
                self.assertIn('nodera_dialog_read', [row['function']['name'] for row in tools])
                return {'tool_calls': [{'id': 'read', 'type': 'function', 'function': {'name': 'nodera_dialog_read', 'arguments': '{"telegram_id":11,"query":"alert"}'}}]}
            self.assertIn('Saved bot alert', messages[-1]['content'])
            return {'content': 'Based on the saved alert'}
        client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'messages': [{'text': 'Saved bot alert'}]}))
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider), \
                patch.object(runner.httpx, 'AsyncClient', side_effect=lambda **kwargs: client(**kwargs, transport=transport)):
            result = await runner.chat(settings, 'panel', 'Read the bot alert')
        self.assertEqual(result['tools_used'], ['nodera_dialog_read'])

    async def test_edit_records_context_without_repeating_a_command(self):
        recorded = asyncio.Event()
        edited = {'message_id': 5, 'from': {'id': 11}, 'chat': {'id': 11, 'type': 'private'}, 'text': '/service fixture restart'}
        polls = 0
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                polls += 1
                if polls == 1: return [{'update_id': 1, 'edited_message': edited}]
                await asyncio.wait_for(recorded.wait(), 1)
                raise asyncio.CancelledError()
            return True
        async def remember(*_): recorded.set()
        with tempfile.TemporaryDirectory() as folder, patch.dict(bot.os.environ, {'STATE_DIRECTORY': folder}), \
                patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'remember_incoming', side_effect=remember) as save, \
                patch.object(bot, 'server_command', new=AsyncMock()) as command:
            with self.assertRaises(asyncio.CancelledError):
                await bot.bot({'modules': {'nodera-bot': True}, 'bot_token': '123456:fixture'})
            save.assert_awaited_once()
            command.assert_not_awaited()
