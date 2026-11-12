import copy
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import api
from tests.api import test_assistant_control as fixtures
import assistant_commands
import assistant_control as control
import assistant_telegram as telegram


class TelegramPolicyTests(unittest.TestCase):
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def setUp(self):
        fixtures.AssistantTests.setUp(self)
        payload = self.payload()
        payload['accounts'][0].update(telegram_tools=[*telegram.DEFAULT_TOOLS, 'tg_send_message'], telegram_action_targets=['42'])
        self.save(payload)
        self.client = TestClient(api.app)

    def prepare(self, module='telegram-mcp', actor='11', **body):
        settings = control.read(api)
        return self.client.post('/api/assistant/control-tool', headers={
            'Authorization': 'Bearer ' + control.mcp_transport_token(settings['control_token'], module, actor),
            'X-MCP-Module': module, 'X-MCP-Actor': actor},
            json={'name': 'telegram_prepare', 'body': body or {'tool': 'tg_send_message', 'arguments': {'group': '42', 'message_text': 'approved fixture', 'dry_run': False}}})

    def test_full_catalog_and_opt_in_policy_survive_legacy_edits(self):
        public = control.status(api)['settings']
        self.assertEqual(len(public['telegram_tool_catalog']), 54)
        self.assertTrue(all('?' not in row['title'] for row in public['telegram_tool_catalog']))
        payload = self.payload()
        payload['expected_revision'] = public['revision']
        self.save(payload)
        account = control.read(api)['accounts'][0]
        self.assertIn('tg_send_message', account['telegram_tools'])
        self.assertEqual(account['telegram_action_targets'], ['42'])
        payload['expected_revision'] = control.read(api)['revision']
        payload['accounts'][0]['telegram_tools'] = []
        self.save(payload)
        self.assertEqual(telegram.enabled(control.read(api)['accounts'][0]), [])
        self.assertEqual(self.prepare().status_code, 403)

    def test_unknown_tools_and_invalid_targets_are_rejected(self):
        payload = self.payload()
        for update in ({'telegram_tools': ['shell']}, {'telegram_tools': ['tg_auth_status'] * 2},
                       {'telegram_action_targets': ['42,99']}, {'telegram_action_targets': ['42\n99']}):
            invalid = copy.deepcopy(payload)
            invalid['accounts'][0].update(update)
            with self.assertRaises(ValueError): control.Settings.model_validate(invalid)
        for extra in ({'actor': '22'}, {'settings': {'control_token': 'override'}}, {'mode': '--telegram-action'}):
            with self.assertRaises(HTTPException) as caught:
                telegram.prepare(api, '11', {'tool': 'tg_send_message', 'arguments': {}, **extra})
            self.assertEqual(caught.exception.status_code, 422)

    def test_other_scopes_and_other_actors_cannot_prepare_telegram_actions(self):
        self.assertEqual(self.prepare(module='vps-controller-mcp').status_code, 403)
        self.assertEqual(self.prepare(module='mihomo-mcp').status_code, 403)
        self.assertEqual(self.prepare(actor='22').status_code, 403)
        self.assertEqual(self.prepare(tool='tg_get_messages', arguments={'group': '42'}).status_code, 422)

    def test_prepare_never_executes_and_human_confirmation_executes_once(self):
        with patch.object(control, 'runtime_request', return_value={'result': {'success': True}}) as runtime:
            self.assertEqual(self.prepare().status_code, 200)
            runtime.assert_not_called()
            record = control.operations.read(assistant_commands.pending_path(api, '11'))
            command = '/confirm ' + record['code']
            answer = assistant_commands.execute(api, '11', command)
            self.assertEqual(assistant_commands.execute(api, '11', command), answer)
            runtime.assert_called_once_with(api, '--telegram-action', {'actor': '11', 'tool': record['body']['tool'], 'arguments': record['body']['arguments']})

    def test_chat_response_includes_the_real_confirmation_payload(self):
        self.prepare()
        settings = control.read(api)
        settings['memory_enabled'] = False
        settings['accounts'][0]['bot_chat_access'] = True
        with patch.object(control, 'runtime_request', return_value={'ok': True, 'result': {'answer': 'Fixture', 'tools_used': ['tg_send_message']}}):
            output = control.chat_request(api, control.Chat(prompt='Fixture'), '11', settings=settings)
        self.assertEqual(output['pending_action']['changes']['arguments']['group'], '42')
        self.assertEqual(output['pending_action']['changes']['arguments']['message_text'], 'approved fixture')

    def test_revoked_settings_and_uncertain_outcomes_do_not_replay_writes(self):
        self.prepare()
        record = control.operations.read(assistant_commands.pending_path(api, '11'))
        with patch.object(control, 'runtime_request', side_effect=HTTPException(502, 'uncertain')) as runtime:
            for expected in (502, 409):
                with self.assertRaises(HTTPException) as caught:
                    assistant_commands.execute(api, '11', '/confirm ' + record['code'])
                self.assertEqual(caught.exception.status_code, expected)
            runtime.assert_called_once()
        operation = control.operations.read(api.DATA_DIR / 'operations' / (record['operation_id'] + '.json'))
        self.assertEqual(operation['state'], 'unknown')
        self.assertIn('не отменено', assistant_commands.execute(api, '11', '/cancel'))
        self.assertEqual(self.prepare().status_code, 200)

    def test_disabled_tool_is_rechecked_at_human_confirmation(self):
        self.prepare()
        record = control.operations.read(assistant_commands.pending_path(api, '11'))
        settings = control.read(api)
        settings['accounts'][0]['telegram_tools'] = []
        control.store(api, settings)
        with patch.object(control, 'runtime_request') as runtime, self.assertRaises(HTTPException):
            assistant_commands.execute(api, '11', '/confirm ' + record['code'])
        runtime.assert_not_called()
