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
        merged = runtime.call_args.args[2]['history']
        self.assertIn('/status', merged[0]['content'])
        self.assertIn('время относительно диалога не подтверждено', merged[0]['content'])
        self.assertEqual(merged[1]['content'], 'Observed command output')
        self.assertEqual([{key: row[key] for key in ('role', 'content')} for row in merged[2:]], [{'role': 'user', 'content': 'Earlier AI question'}, {'role': 'assistant', 'content': 'Stored AI answer'}])
        self.assertEqual(runtime.call_args.args[2]['context_conversation'], 'a'*32)

    def test_chat_only_associates_current_request_confirmations(self):
        old, new = {'operation_id': 'a'*32}, {'operation_id': 'b'*32}
        for result, pending in (({'answer': 'Hello', 'prepared_operation_ids': []}, []),
                ({'answer': 'Prepared', 'prepared_operation_ids': ['b'*32]}, [new]),
                ({'answer': 'Legacy'}, [new])):
            with self.subTest(result=result), \
                    patch.object(commands, 'pending_actions', side_effect=[[old], [old, new]]), \
                    patch.object(control.assistant_memory, 'append'), \
                    patch.object(control, 'provider_usage', return_value={}), \
                    patch.object(control, 'runtime_request', return_value={'result': result}):
                output = control._chat_request(api, control.Chat(prompt='New question'), settings=control.read(api))
            self.assertEqual(output['pending_actions'], pending)
            self.assertEqual(output.get('pending_action'), pending[-1] if pending else None)

    def test_expired_confirmation_is_not_presented_as_awaiting(self):
        with patch.object(commands, 'pending_actions', return_value=[]), \
                patch.object(control.assistant_memory, 'append'), \
                patch.object(control, 'provider_usage', return_value={}), \
                patch.object(control, 'runtime_request', return_value={'result': {
                    'answer': 'Prepared', 'status': 'awaiting_confirmation', 'prepared_operation_ids': ['a'*32]}}):
            output = control._chat_request(api, control.Chat(prompt='New question'), settings=control.read(api))
        self.assertEqual(output['result']['status'], 'incomplete')
        self.assertEqual(output['pending_actions'], [])

    def group(self, codes, actor='11', **changes):
        ids = [control.operations.read(commands.pending_path(api, '11', code=code))['operation_id'] for code in codes]
        token = control.mcp_transport_token(control.read(api)['control_token'], 'nodera-confirmations', actor)
        return self.client.post('/api/assistant/confirmation-request', json={
            'operation_ids': ids, 'title': 'Repair connections', 'reason': 'Apply the prepared actions in order', **changes},
            headers={'Authorization': 'Bearer ' + token, 'X-MCP-Actor': actor})

    def test_one_call_presents_five_exact_operations_atomically_and_preserves_order(self):
        codes = [self.prepare() for _ in range(5)]
        records = [control.operations.read(commands.pending_path(api, '11', code=code)) for code in codes]
        with patch.object(control, 'require_installed'), patch.object(api, 'manage_service') as mutate:
            result = self.group(codes)
            self.assertEqual(result.status_code, 200, result.text)
            value = result.json()
            self.assertEqual(value['operation_ids'], [row['operation_id'] for row in records])
            self.assertEqual(value['expires'], min(row['expires'] for row in records))
            self.assertNotIn('code', str(value))
            self.assertEqual(self.group(codes).json(), value)
            self.assertEqual(len(confirmations.plans(api, '11')), 1)
            self.assertEqual(self.group(codes, title='Different plan').status_code, 409)
            self.assertEqual(self.group(list(reversed(codes))).status_code, 409)
            self.assertEqual(self.group(codes[:2]).status_code, 409)
            rows = commands.pending_actions(api, '11')
            self.assertEqual({row['confirmation_request']['request_id'] for row in rows}, {value['request_id']})
            self.assertEqual(confirmations.for_model(api, 'panel'), [])
            mutate.assert_not_called()
            self.assertEqual(self.call('/cancel ' + codes[1]).status_code, 200)
            remaining = commands.pending_actions(api, '11')
            self.assertEqual(len(remaining), 4)
            self.assertEqual({row['confirmation_request']['request_id'] for row in remaining}, {value['request_id']})

    def test_invalid_group_never_creates_partial_presentations_or_foreign_members(self):
        codes = [self.prepare() for _ in range(5)]
        with patch.object(control, 'require_installed'):
            self.assertEqual(self.group(codes, actor='panel').status_code, 409)
            self.assertEqual(self.group(codes, operation_ids=['f'*32]).status_code, 409)
            identity = control.operations.read(commands.pending_path(api, '11', code=codes[0]))['operation_id']
            for changes in ({'operation_ids': []}, {'operation_ids': [identity]*2}, {'operation_ids': [identity]*33},
                            {'operation_id': identity}, {'operation_ids': ['../bad']}):
                self.assertEqual(self.group(codes, **changes).status_code, 422)
            path = commands.pending_path(api, '11', code=codes[-1])
            record = control.operations.read(path)
            control.operations.atomic_json(path, {**record, 'expires': time.time()-1})
            self.assertEqual(self.group(codes).status_code, 409)
            self.assertEqual(confirmations.plans(api, '11'), [])
            self.assertTrue(all('confirmation_request' not in row for row in commands.pending_actions(api, '11')))

    def test_group_binding_blocks_a_changed_operation_before_any_mutation(self):
        codes = [self.prepare() for _ in range(4)]
        with patch.object(control, 'require_installed'):
            self.assertEqual(self.group(codes).status_code, 200)
        path = commands.pending_path(api, '11', code=codes[2])
        row = control.operations.read(path)
        control.operations.atomic_json(path, {**row, 'action': 'stop'})
        with patch.object(api, 'manage_service') as mutate:
            self.assertEqual(self.call('/confirm ' + codes[2]).status_code, 409)
            mutate.assert_not_called()
