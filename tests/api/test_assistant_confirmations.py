import time
import unittest
from unittest.mock import patch

from tests.api import test_assistant_commands as fixtures
from tests.api.support import api
import assistant_confirmations as confirmations
import assistant_commands as commands
import assistant_control as control


class ConfirmationRequestTests(unittest.TestCase):
    payload = fixtures.ServerCommandTests.payload
    save = fixtures.ServerCommandTests.save
    enabled_bot = fixtures.ServerCommandTests.enabled_bot
    approve = fixtures.ServerCommandTests.approve
    def setUp(self):
        fixtures.ServerCommandTests.setUp(self)
        payload = self.payload()
        payload.update(expected_revision=control.read(api)["revision"], bot_token=None)
        self.save(payload)
    call = fixtures.ServerCommandTests.call
    prepare = fixtures.ServerCommandTests.prepare

    def present(self, code, actor='11', **changes):
        record = control.operations.read(commands.pending_path(api, '11', code=code))
        payload = {'operation_id': record['operation_id'], 'title': 'Restart fixture', 'reason': 'Apply settings; connections may be interrupted', **changes}
        token = control.mcp_transport_token(control.read(api)['control_token'], 'nodera-confirmations', actor)
        return self.client.post('/api/assistant/confirmation-request', json=payload,
                                headers={'Authorization': 'Bearer ' + token, 'X-MCP-Actor': actor})

    def test_exact_operation_presentation_is_idempotent_and_does_not_execute(self):
        first, second = self.prepare(), self.prepare()
        before = control.operations.read(commands.pending_path(api, '11', code=first))
        with patch.object(control, 'require_installed'), patch.object(api, 'manage_service') as action:
            result = self.present(first)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotIn('code', result.json())
            self.assertEqual(result.json()['status'], 'awaiting_user')
            self.assertEqual(self.present(first).json(), result.json())
            self.assertEqual(self.present(first, title='Another description').status_code, 409)
            rows = {row['code']: row for row in commands.pending_actions(api, '11')}
            self.assertEqual(rows[first]['confirmation_request']['title'], 'Restart fixture')
            self.assertNotIn('confirmation_request', rows[second])
            model_context = confirmations.for_model(api, '11')
            self.assertEqual({row['operation_id'] for row in model_context}, {row['operation_id'] for row in rows.values()})
            self.assertTrue(all('code' not in row for row in model_context))
            self.assertEqual(confirmations.for_model(api, 'panel'), [])
            after = control.operations.read(commands.pending_path(api, '11', code=first))
            self.assertEqual(after['expires'], before['expires'])
            self.assertFalse(after['submitted'])
            action.assert_not_called()
            self.assertEqual(self.call('/cancel ' + first).status_code, 200)
            self.assertEqual([row['code'] for row in commands.pending_actions(api, '11')], [second])

    def test_foreign_expired_submitted_and_changed_operations_cannot_be_presented(self):
        code = self.prepare()
        with patch.object(control, 'require_installed'):
            self.assertEqual(self.present(code, actor='panel').status_code, 409)
            self.assertEqual(self.present(code, actor='22').status_code, 403)
            self.assertEqual(self.present(code, operation_id='f' * 32).status_code, 409)
            self.assertEqual(self.present(code, title='  ').status_code, 422)
            path = commands.pending_path(api, '11', code=code)
            original = control.operations.read(path)
            for changes in ({'expires': time.time()-1}, {'submitted': True}, {'revision': 'outdated'}):
                control.operations.atomic_json(path, {**original, **changes})
                self.assertEqual(self.present(code).status_code, 409)

    def test_scoped_request_token_cannot_be_used_to_approve_or_cross_actors(self):
        code = self.prepare()
        token = control.mcp_transport_token(control.read(api)['control_token'], 'nodera-confirmations', '11')
        headers = {'Authorization': 'Bearer ' + token, 'X-MCP-Actor': '11'}
        self.assertEqual(self.client.post('/api/assistant/service-confirm', json={'code': code}, headers=headers).status_code, 401)
        self.assertEqual(self.client.post('/api/assistant/confirmation-request', json={}, headers={**headers, 'X-MCP-Actor': 'panel'}).status_code, 401)

    def test_changed_action_after_presentation_cannot_be_executed(self):
        code = self.prepare()
        with patch.object(control, 'require_installed'):
            self.assertEqual(self.present(code).status_code, 200)
        path = commands.pending_path(api, '11', code=code)
        record = control.operations.read(path)
        control.operations.atomic_json(path, {**record, 'action': 'stop'})
        with patch.object(api, 'manage_service') as action:
            self.assertEqual(self.call('/confirm ' + code).status_code, 409)
            action.assert_not_called()

    def test_panel_command_history_survives_server_memory_lookup(self):
        history = [{'role': 'user', 'content': '/status'}, {'role': 'assistant', 'content': 'Observed command output'}]
        private = [{'role': 'user', 'content': '/subscription own'}, {'role': 'assistant', 'content': 'private-subscription-output'}]
        settings = control.read(api)
        stored = {'turns': [{'question': 'Earlier AI question', 'answer': 'Stored AI answer', 'files': []}]}
        with patch.object(control.assistant_memory, 'get', return_value=stored), \
                patch.object(control.assistant_memory, 'append'), \
                patch.object(control, 'provider_usage', return_value={}), \
                patch.object(control, 'runtime_request', return_value={'result': {'answer': 'Based on output', 'tools_used': []}}) as runtime:
            control._chat_request(api, control.Chat(prompt='Explain output', history=history + private, conversation_id='a'*32), 'panel', settings=settings)
        self.assertEqual([{key: row[key] for key in ('role', 'content')} for row in runtime.call_args.args[2]['history']], [{'role': 'user', 'content': 'Earlier AI question'}, {'role': 'assistant', 'content': 'Stored AI answer'}] + history)
        self.assertEqual(runtime.call_args.args[2]['context_conversation'], 'a'*32)
