"""Bot service changes must use live admin ACLs and explicit, durable confirmation."""
import asyncio
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from tests.api import test_assistant_contacts as fixtures
from tests.api.support import api
import assistant_control as control
import assistant_commands as commands

bot = fixtures.bot


class ServerCommandTests(unittest.TestCase):
    payload = fixtures.BotAccessTests.payload
    save = fixtures.BotAccessTests.save
    enabled_bot = fixtures.BotAccessTests.enabled_bot
    approve = fixtures.BotAccessTests.approve

    def setUp(self):
        fixtures.BotAccessTests.setUp(self)
        self.headers = self.enabled_bot()
        self.approve()
        self.client = TestClient(api.app)
        self.services = self.enterContext(patch.object(api, 'managed_services', return_value={
            'fixture': {'name': 'Fixture', 'unit': 'fixture.service', 'controls': ['start', 'stop', 'restart']},
            'ssh': {'name': 'SSH', 'unit': 'ssh.service', 'controls': ['start', 'stop', 'restart']},
            'nodera-bot': {'name': 'NoderaBot', 'unit': 'vps-control-nodera-bot.service', 'controls': ['restart']}}))
        self.validation = self.enterContext(patch.object(api, 'service_action_definition', return_value={}))

    def call(self, command, actor=None):
        headers = self.headers if actor is None else {**self.headers, 'X-MCP-Actor': str(actor)}
        return self.client.post('/api/assistant/bot-command', headers=headers, json={'command': command})

    def test_direct_diagnostics_use_read_permissions_and_scrub_secrets_without_ai(self):
        import assistant_diagnostics
        secret = control.read(api)['control_token']
        with patch.object(assistant_diagnostics, 'call', return_value={'state': 'unknown', 'api_key': 'private-key', 'message': secret}) as diagnostic, patch.object(control, 'runtime_request') as model:
            for command, tool in [('/network', 'vps_network'), ('/security', 'vps_security'), ('/logs fixture', 'vps_logs'), ('/diagnostics', 'vps_diagnostics')]:
                response = self.call(command)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('неизвестно', response.json()['text'])
                self.assertNotIn('```json', response.json()['text'])
                self.assertNotIn('private-key', response.text)
                self.assertNotIn(secret, response.text)
                self.assertEqual(diagnostic.call_args.args[1], tool)
            model.assert_not_called()
        # Use real authorization, before invoking security collectors.
        with patch.object(api, 'security') as security:
            self.assertEqual(self.call('/security', actor=999).status_code, 403)
            security.assert_not_called()

    def test_mcp_prepares_but_cannot_confirm_and_checks_live_scope(self):
        value = control.read(api)
        value['mcp']['vps-controller-mcp'] = True
        control.store(api, value)
        token = {'Authorization': 'Bearer ' + value['control_token'], 'X-MCP-Actor': 'panel'}
        with patch.object(api, 'manage_service') as action:
            result = self.client.post('/api/assistant/control-tool', headers=token,
                                      json={'name': 'vps_service_action', 'service': 'fixture', 'action': 'restart'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()['confirmation_required'])
            self.assertNotIn('code', result.json())
            action.assert_not_called()
            self.assertEqual(self.client.post('/api/assistant/control-tool', headers=token,
                                              json={'name': 'vps_confirm'}).status_code, 422)
            self.assertEqual(self.client.post('/api/assistant/control-tool', headers=token,
                                              json={'name': 'vps_api', 'endpoint': '/settings'}).status_code, 422)
            denied = self.client.post('/api/assistant/control-tool', headers={**token, 'X-MCP-Actor': '999'},
                                      json={'name': 'vps_service_action', 'service': 'fixture', 'action': 'restart'})
            self.assertEqual(denied.status_code, 403)
            pending = control.operations.read(commands.pending_path(api, 'panel'))
            self.assertEqual(self.client.post('/api/assistant/service-confirm', json={'code': pending['code']}).status_code, 401)
            action.return_value = {'id': pending['operation_id'], 'state': 'queued'}
            with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
                confirmed = self.client.post('/api/assistant/service-confirm', json={'code': pending['code']})
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                action.assert_called_once()
                value['mcp']['vps-controller-mcp'] = False
                control.store(api, value)
                self.assertEqual(self.client.post('/api/assistant/service-confirm', json={'code': pending['code']}).status_code, 403)

    def test_panel_pending_is_authenticated_actor_bound_and_reject_checks_exact_code(self):
        code = self.prepare()
        self.assertEqual(self.client.get('/api/assistant/pending-action?telegram_id=11').status_code, 401)
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}), patch.object(api, 'manage_service') as action:
            self.assertIsNone(self.client.get('/api/assistant/pending-action').json()['pending_action'])
            pending = self.client.get('/api/assistant/pending-action?telegram_id=11').json()['pending_action']
            self.assertEqual(pending['code'], code)
            self.assertNotIn('revision', pending)
            rejected = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': '0' * 8, 'decision': 'reject'})
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(self.client.get('/api/assistant/pending-action?telegram_id=11').json()['pending_action']['code'], code)
            rejected = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': code, 'decision': 'reject'})
            self.assertEqual(rejected.status_code, 200, rejected.text)
            self.assertIsNone(self.client.get('/api/assistant/pending-action?telegram_id=11').json()['pending_action'])
            self.assertEqual(self.call('/confirm ' + code).status_code, 409)
            action.assert_not_called()

    def test_pending_disappears_when_expired_submitted_or_permissions_change(self):
        code = self.prepare()
        self.assertEqual(commands.pending_action(api, '11')['code'], code)
        with patch.object(commands.time, 'time', return_value=control.operations.read(commands.pending_path(api, '11'))['expires'] + 1):
            self.assertIsNone(commands.pending_action(api, '11'))
        path = commands.pending_path(api, '11')
        record = control.operations.read(path)
        control.operations.atomic_json(path, {**record, 'submitted': True})
        self.assertIsNone(commands.pending_action(api, '11'))
        control.operations.atomic_json(path, record)
        self.approve(False)
        self.assertIsNone(commands.pending_action(api, '11'))

    def test_chat_returns_pending_even_without_a_tool_name_in_the_model_response(self):
        code = self.prepare()
        identity = commands.pending_action(api, '11')['operation_id']
        settings = {**control.read(api), 'memory_enabled': False}
        with patch.object(control, 'runtime_request', return_value={'result': {'answer': 'Confirm the action', 'tools_used': [], 'prepared_operation_ids': [identity]}}), patch.object(control, 'provider_usage', return_value={}):
            result = control._chat_request(api, control.Chat(prompt='Continue'), '11', settings=settings)
        self.assertEqual(result['pending_action']['code'], code)
        self.assertEqual(result['pending_action']['service'], 'fixture')

    def test_telegram_actions_confirm_with_telegram_scope_when_vps_scope_is_disabled(self):
        import assistant_telegram
        value = control.read(api)
        value['mcp']['vps-controller-mcp'] = False
        value['mcp']['telegram-mcp'] = True
        control.store(api, value)
        record = {'kind': 'telegram', 'service': 'Telegram', 'action': 'fixture', 'code': 'abcdef12',
                  'operation_id': uuid.uuid4().hex, 'expires': commands.time.time() + 60,
                  'revision': value['revision'], 'submitted': False}
        control.operations.atomic_json(commands.pending_path(api, 'panel'), record)
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}), patch.object(assistant_telegram, 'confirm', return_value='done') as confirm:
            response = self.client.post('/api/assistant/service-confirm', json={'code': record['code']})
            self.assertEqual(response.status_code, 200, response.text)
            confirm.assert_called_once()

    def test_telegram_buttons_keep_callbacks_scoped_and_long_model_ids_out_of_payload(self):
        model = 'provider/' + 'long-model-' * 10
        actions = {}
        markup = bot.inline_keyboard('Модели · страница 1/2\n' + model + '\n\n/model <ID> — выбрать', '/models', 'chat', actions)
        self.assertTrue(all(len(button['callback_data'].encode()) <= 64 for row in markup['inline_keyboard'] for button in row))
        self.assertIn('/model ' + model, actions.values())
        self.assertIn('/models 2', actions.values())
        self.assertNotIn('Службы', str(bot.reply_keyboard('chat')))
        self.assertEqual(bot.reply_keyboard(True)['keyboard'], [[{'text':'Меню'}]])
        menu = bot.inline_keyboard('', '/menu server', True, {})
        self.assertIn('Службы', str(menu))
        self.assertEqual(bot.reply_keyboard(False), {'remove_keyboard': True})

    def test_telegram_menu_routes_model_callback_and_plain_text_with_live_permissions(self):
        async def run():
            settings = {'modules': {'nodera-bot': True}, 'bot_token': 'fixture'}
            sent, polls = [], 0
            delivered = asyncio.Event()

            async def transport(settings, method, **payload):
                nonlocal polls
                if method == 'getWebhookInfo': return {'url': ''}
                if method in ('setMyCommands', 'answerCallbackQuery'): return True
                if method == 'sendMessage':
                    sent.append(payload)
                    if 'Ответ AI' in payload.get('text', ''): delivered.set()
                    return {}
                if method == 'getUpdates':
                    # Telegram polling performs I/O; let independently dispatched
                    # messages deliver their keyboard before emitting callbacks.
                    await asyncio.sleep(0)
                    polls += 1
                    if polls == 1:
                        return [{'update_id': 1, 'message': {'from': {'id': 11}, 'chat': {'type': 'private'}, 'text': 'Модели AI'}}]
                    if polls in (2, 3):
                        markup = next(row['reply_markup'] for row in sent if 'inline_keyboard' in row.get('reply_markup', {}))
                        data = markup['inline_keyboard'][0][0]['callback_data']
                        return [{'update_id': polls, 'callback_query': {'id': str(polls), 'from': {'id': 11 if polls == 2 else 22},
                                'data': data, 'message': {'chat': {'type': 'private'}}}}]
                    if polls == 4:
                        return [{'update_id': 4, 'message': {'from': {'id': 11}, 'chat': {'type': 'private'}, 'text': 'Обычный вопрос'}}]
                    await asyncio.wait_for(delivered.wait(), 1)
                    raise asyncio.CancelledError()
                raise AssertionError(method)

            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), \
                    patch.object(bot, 'bot_api', transport), patch.object(bot, 'identify', new=AsyncMock(side_effect=lambda _, sender: sender['id'] == 11)), \
                    patch.object(bot, 'server_command', new=AsyncMock(side_effect=['Модели · страница 1/1\nselected-model\n\n/model <ID> — выбрать', 'Выбрана модель: selected-model'])) as server, \
                    patch.object(bot, 'agent_request', new=AsyncMock(return_value='Ответ AI')) as agent:
                try: await asyncio.wait_for(bot.bot(settings), 3)
                except asyncio.CancelledError: pass
                self.assertEqual([call.args[2] for call in server.await_args_list], ['/models', '/model selected-model'])
                agent.assert_awaited_once_with(settings, 11, 'Обычный вопрос')
                self.assertEqual(json.loads((Path(folder) / 'offset.json').read_text())['offset'], 5)
                self.assertTrue(any(row.get('chat_id') == 22 and 'устарело' in row.get('text', '') for row in sent))
        asyncio.run(run())

    def prepare(self):
        response = self.call('/service fixture restart')
        self.assertEqual(response.status_code, 200, response.text)
        return re.search(r'/confirm ([0-9a-f]{8})', response.json()['text'])[1]

    def test_confirm_is_actor_bound_expiring_and_live_authorized(self):
        with patch.object(api, 'manage_service') as action:
            code = self.prepare()
            action.assert_not_called()
            self.assertEqual(self.call('/confirm ' + code, 22).status_code, 403)
            self.assertEqual(self.call('/confirm неправильный').status_code, 409)
            self.approve(False)
            self.assertEqual(self.call('/confirm ' + code).status_code, 403)
            self.approve()
            self.assertEqual(self.call('/confirm ' + code).status_code, 409)
            code = self.prepare()
            with patch.object(commands.time, 'time', return_value=control.operations.read(commands.pending_path(api, '11'))['expires'] + 1):
                self.assertEqual(self.call('/confirm ' + code).status_code, 409)
            action.assert_not_called()

    def test_confirm_uses_normal_worker_and_replays_without_second_mutation(self):
        launches = []
        def start(action, command, *, operation_id, properties):
            launches.append((action, command))
            value = {'id': operation_id, 'action': action, 'state': 'succeeded', 'message': 'secret diagnostic'}
            control.operations.atomic_json(api.DATA_DIR / 'operations' / (operation_id + '.json'), value)
            return value
        with patch.object(api, 'start_application_operation', side_effect=start):
            code = self.prepare()
            first = self.call('/confirm ' + code)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(self.call('/confirm ' + code).json(), first.json())
            self.assertEqual(len(launches), 1)
            self.assertEqual(launches[0][0], 'service-action:fixture:restart')
            self.assertEqual(launches[0][1][-2:], ['fixture', 'restart'])
            self.assertNotIn('secret diagnostic', first.text)
            identity = re.search(r'/operation ([0-9a-f]{32})', first.json()['text'])[1]
            self.assertEqual(self.call('/operation ' + identity).json(), first.json())
            self.assertEqual(self.call('/cancel').status_code, 409)

    def test_poweroff_and_protected_access_are_blocked_before_service_admission(self):
        with patch.object(api, 'manage_service') as action:
            for prompt in ('/poweroff', '/shutdown', '/access-mode vpn', '/panel-access vpn'):
                self.assertEqual(self.call(prompt).status_code, 403)
            code = self.prepare()
            self.services.return_value['fixture']['unit'] = 'poweroff.target'
            self.assertEqual(self.call('/service fixture start').status_code, 409)
            self.assertEqual(self.call('/confirm ' + code).status_code, 403)
            action.assert_not_called()

    def test_cancel_multiple_requests_invalid_actions_and_admission_conflict(self):
        with patch.object(api, 'manage_service') as action:
            for prompt in ('/service ../../tmp restart', '/service fixture shell', '/service ssh stop', '/service nodera-bot restart'):
                self.assertEqual(self.call(prompt).status_code, 409)
            first = self.prepare(); second = self.prepare()
            self.assertEqual(len(commands.pending_actions(api, '11')), 2)
            self.assertEqual(self.call('/cancel').status_code, 409)
            self.assertEqual(self.call('/cancel ' + first).status_code, 200)
            self.assertEqual(self.call('/cancel ' + second).status_code, 200)
            self.assertEqual(self.call('/confirm ' + second).status_code, 409)
            action.assert_not_called()
            code = self.prepare()
            action.side_effect = api.HTTPException(409, 'busy')
            self.assertEqual(self.call('/confirm ' + code).status_code, 409)
            self.assertEqual(self.call('/cancel').status_code, 200)

    def test_guest_commands_never_read_or_modify_server_and_admin_needs_no_ai(self):
        with patch.object(api, 'overview', return_value={'server': {'name': 'private-fixture'}, 'resources': {'cpu_percent': None}}) as overview, \
             patch.object(control, 'runtime_request', side_effect=AssertionError('Commands require no AI')):
            for prompt in ('/status', '/resources', '/services', '/components', '/operation ' + uuid.uuid4().hex, '/confirm aaaaaaaa'):
                self.assertEqual(self.call(prompt, 22).status_code, 403)
            overview.assert_not_called()
            self.assertIn('private-fixture', self.call('/status').json()['text'])
            self.assertIn('нет данных', self.call('/resources').json()['text'])
            self.assertIn('fixture', self.call('/services').json()['text'])
            with patch.object(api, 'protocol_images', return_value={'items': [{'id': 'fixture', 'installed': True, 'installed_version': '1.2.3'}, {'id': 'absent', 'installed': False}]}):
                result = self.call('/components').json()['text']
                self.assertIn('1.2.3', result); self.assertNotIn('absent', result)

    def test_bot_dispatches_slash_commands_without_ai_and_uses_private_menus(self):
        async def check():
            sender = {'id': 11}
            with patch.object(bot, 'server_command', new=AsyncMock(return_value='server result')) as request, \
                 patch.object(bot, 'agent_request', new=AsyncMock(side_effect=AssertionError('Slash command reached AI'))):
                self.assertEqual(await bot.reply({}, sender, '/status@NoderaBot', True), 'server result')
                request.assert_awaited_once_with({}, 11, '/status@NoderaBot')
                request.reset_mock()
                await bot.reply({}, sender, '/service fixture stop', False)
                request.assert_not_awaited()
            with patch.object(bot, 'bot_api', new=AsyncMock()) as transport:
                await bot.command_menu({}, 11, True)
                self.assertEqual(transport.call_args.kwargs['scope'], {'type': 'chat', 'chat_id': 11})
                published = [row['command'] for row in transport.call_args.kwargs['commands']]
                self.assertIn('status', published)
                self.assertNotIn('service', published)
                self.assertNotIn('confirm', published)
                self.assertNotIn('cancel', published)
                await bot.command_menu({}, 22, False)
                self.assertEqual([row['command'] for row in transport.call_args.kwargs['commands']], ['id'])
        asyncio.run(check())
