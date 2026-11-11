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

    def test_telegram_buttons_keep_callbacks_scoped_and_long_model_ids_out_of_payload(self):
        model = 'provider/' + 'long-model-' * 10
        actions = {}
        markup = bot.inline_keyboard('Модели · страница 1/2\n' + model + '\n\n/model <ID> — выбрать', '/models', 'chat', actions)
        self.assertTrue(all(len(button['callback_data'].encode()) <= 64 for row in markup['inline_keyboard'] for button in row))
        self.assertIn('/model ' + model, actions.values())
        self.assertIn('/models 2', actions.values())
        self.assertNotIn('Службы', str(bot.reply_keyboard('chat')))
        self.assertIn('Службы', str(bot.reply_keyboard(True)))
        self.assertEqual(bot.reply_keyboard(False), {'remove_keyboard': True})

    def test_telegram_menu_routes_model_callback_and_plain_text_with_live_permissions(self):
        async def run():
            settings = {'modules': {'nodera-bot': True}, 'bot_token': 'fixture'}
            sent, polls = [], 0

            async def transport(settings, method, **payload):
                nonlocal polls
                if method == 'getWebhookInfo': return {'url': ''}
                if method in ('setMyCommands', 'answerCallbackQuery'): return True
                if method == 'sendMessage': sent.append(payload); return {}
                if method == 'getUpdates':
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
                    raise asyncio.CancelledError()
                raise AssertionError(method)

            ticks = iter(range(100, 10000, 4))
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), \
                    patch.object(bot, 'bot_api', transport), patch.object(bot, 'identify', new=AsyncMock(side_effect=lambda _, sender: sender['id'] == 11)), \
                    patch.object(bot.time, 'monotonic', side_effect=lambda: next(ticks)), \
                    patch.object(bot, 'server_command', new=AsyncMock(side_effect=['Модели · страница 1/1\nselected-model\n\n/model <ID> — выбрать', 'Выбрана модель: selected-model'])) as server, \
                    patch.object(bot, 'agent_request', new=AsyncMock(return_value='Ответ AI')) as agent:
                try: await bot.bot(settings)
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
            with patch.object(commands.time, 'time', return_value=commands.time.time() + 61):
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

    def test_cancel_replacement_invalid_actions_and_admission_conflict(self):
        with patch.object(api, 'manage_service') as action:
            for prompt in ('/service ../../tmp restart', '/service fixture shell', '/service ssh stop', '/service nodera-bot restart'):
                self.assertEqual(self.call(prompt).status_code, 409)
            first = self.prepare(); second = self.prepare()
            self.assertEqual(self.call('/confirm ' + first).status_code, 409)
            self.assertEqual(self.call('/cancel').status_code, 200)
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
                self.assertIn('service', [row['command'] for row in transport.call_args.kwargs['commands']])
                await bot.command_menu({}, 22, False)
                self.assertEqual([row['command'] for row in transport.call_args.kwargs['commands']], ['start', 'help', 'id'])
        asyncio.run(check())
