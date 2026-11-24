"""Several human decisions coexist and retain their exact actor/code/operation scope."""
import json
import unittest
from unittest.mock import patch

from tests.api import test_assistant_commands as fixtures
from tests.api.support import api
import assistant_commands as commands
import assistant_control as control


class ConfirmationQueueTests(unittest.TestCase):
    payload = fixtures.ServerCommandTests.payload
    save = fixtures.ServerCommandTests.save
    enabled_bot = fixtures.ServerCommandTests.enabled_bot
    approve = fixtures.ServerCommandTests.approve
    setUp = fixtures.ServerCommandTests.setUp
    call = fixtures.ServerCommandTests.call
    prepare = fixtures.ServerCommandTests.prepare
    def test_several_requests_survive_and_can_be_confirmed_out_of_order(self):
        first = self.prepare()
        second = self.prepare()
        paths = {code: commands.pending_path(api, '11', code=code) for code in (first, second)}
        self.assertNotEqual(paths[first], paths[second])
        records = {code: control.operations.read(path) for code, path in paths.items()}
        with patch.object(api, 'manage_service', side_effect=lambda _service, _payload, _auth, identity: {'id': identity, 'state': 'queued'}) as action:
            self.assertEqual(self.call('/confirm ' + first).status_code, 200)
            self.assertEqual([row['code'] for row in commands.pending_actions(api, '11')], [second])
            self.assertEqual(self.call('/confirm ' + second).status_code, 200)
            self.assertEqual([call.args[3] for call in action.call_args_list], [records[first]['operation_id'], records[second]['operation_id']])
        self.assertFalse(commands.pending_actions(api, '11'))

    def test_rejecting_one_request_preserves_the_other_and_checks_its_exact_code(self):
        first, second = self.prepare(), self.prepare()
        self.assertEqual(self.call('/cancel ' + first).status_code, 200)
        self.assertEqual([row['code'] for row in commands.pending_actions(api, '11')], [second])
        self.assertEqual(self.call('/cancel ' + first).status_code, 409)
        self.assertEqual(self.call('/confirm ' + second, 22).status_code, 403)
        self.assertEqual(self.call('/cancel ' + second).status_code, 200)

    def test_panel_listing_excludes_client_requests_and_cannot_decide_for_them(self):
        first, second = self.prepare(), self.prepare()
        record = control.operations.read(commands.pending_path(api, '11', code=first))
        with control.operations.locked(api.DATA_DIR):
            commands.enqueue(api, 'panel', {**record, 'code': 'aabbccdd', 'operation_id': 'a' * 32})
        self.assertEqual(self.client.get('/api/assistant/pending-action?all_actors=true').status_code, 401)
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}), patch.object(api, 'manage_service') as action:
            rows = self.client.get('/api/assistant/pending-action?all_actors=true').json()['pending_actions']
            self.assertEqual({(row['actor'], row['code']) for row in rows}, {('panel', 'aabbccdd')})
            result = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': first, 'decision': 'reject'})
            self.assertEqual(result.status_code, 403, result.text)
            rows = self.client.get('/api/assistant/pending-action?all_actors=true').json()['pending_actions']
            self.assertEqual({(row['actor'], row['code']) for row in rows}, {('panel', 'aabbccdd')})
            self.assertEqual({row['code'] for row in commands.pending_actions(api, '11')}, {first, second})
            action.assert_not_called()

    def test_old_requests_remain_available_without_attaching_to_a_new_chat_reply(self):
        codes = {self.prepare(), self.prepare()}
        settings = {**control.read(api), 'memory_enabled': False}
        with patch.object(control, 'runtime_request', return_value={'result': {'answer': 'Review both actions', 'tools_used': []}}), patch.object(control, 'provider_usage', return_value={}):
            result = control._chat_request(api, control.Chat(prompt='Continue'), '11', settings=settings)
        self.assertEqual(result['pending_actions'], [])
        self.assertNotIn('pending_action', result)
        self.assertEqual({row['code'] for row in commands.pending_actions(api, '11')}, codes)
        self.assertNotIn('vps_confirm', control.ToolCall.model_fields['name'].annotation.__args__)

    def test_legacy_record_and_new_queue_item_remain_independently_addressable(self):
        code = self.prepare()
        path = commands.pending_path(api, '11', code=code)
        record = control.operations.read(path)
        legacy = path.parent.parent / '11.json'
        control.operations.atomic_json(legacy, record)
        path.unlink()
        second = self.prepare()
        self.assertEqual({row['code'] for row in commands.pending_actions(api, '11')}, {code, second})
        self.assertEqual(self.call('/cancel ' + code).status_code, 200)
        self.assertEqual(commands.pending_action(api, '11')['code'], second)

    def test_queue_capacity_does_not_overwrite_existing_requests_and_expiry_frees_space(self):
        first = self.prepare()
        record = control.operations.read(commands.pending_path(api, '11', code=first))
        with control.operations.locked(api.DATA_DIR):
            for index in range(31):
                commands.enqueue(api, '11', {**record, 'code': f'{index:08x}', 'operation_id': f'{index:032x}'})
        self.assertEqual(self.call('/service fixture restart').status_code, 409)
        self.assertEqual(len(commands.pending_actions(api, '11')), 32)
        deadline = max(row['expires'] for row in commands.pending_actions(api, '11'))
        with patch.object(commands.time, 'time', return_value=deadline + 1):
            self.assertFalse(commands.pending_actions(api, '11'))
            self.assertEqual(self.call('/service fixture restart').status_code, 200)

    def test_telegram_keyboard_has_a_distinct_confirm_and_reject_for_each_action(self):
        from tests.api.test_assistant_commands import bot
        actions = {}
        markup = bot.inline_keyboard('/confirm aabbccdd\n/confirm 11223344', 'Two actions', True, actions)
        callbacks = [button['callback_data'] for row in markup['inline_keyboard'] for button in row]
        self.assertGreaterEqual(len(callbacks), 4)
        # Resolve the callback representation through the persisted command map.
        rendered = json.dumps(actions)
        for command in ('/confirm aabbccdd', '/cancel aabbccdd', '/confirm 11223344', '/cancel 11223344'):
            self.assertIn(command, rendered)

    def test_completed_service_operations_are_pruned_before_queue_capacity_is_checked(self):
        code = self.prepare()
        original = commands.pending_path(api, '11', code=code)
        record = control.operations.read(original)
        original.unlink()
        for index in range(128):
            identity = f'{index:032x}'
            control.operations.atomic_json(original.parent / f'{index:08x}.json', {**record, 'code': f'{index:08x}', 'operation_id': identity, 'submitted': True})
            control.operations.atomic_json(api.DATA_DIR / 'operations' / (identity + '.json'), {'id': identity, 'state': 'succeeded'})
        self.assertEqual(self.call('/service fixture restart').status_code, 200)
        self.assertEqual(len(commands.pending_records(api, '11')), 65)
        self.assertEqual(len(commands.pending_actions(api, '11')), 1)

    def test_recent_submitted_operations_are_listed_by_id_for_independent_readback(self):
        first, second = self.prepare(), self.prepare()
        expected = {control.operations.read(commands.pending_path(api, '11', code=code))['operation_id'] for code in (first, second)}
        with patch.object(api, 'manage_service', side_effect=lambda _service, _payload, _auth, identity: {'id': identity, 'state': 'queued'}):
            for code in (first, second): self.assertEqual(self.call('/confirm ' + code).status_code, 200)
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
            result = self.client.get('/api/assistant/pending-action?all_actors=true').json()
        self.assertFalse(result['pending_actions'])
        self.assertTrue(expected)
        self.assertEqual(result['operations'], [])

    def test_panel_cannot_confirm_client_service_command_even_without_ai(self):
        settings = control.read(api)
        settings['modules']['ai-agent'] = False
        settings['mcp']['vps-controller-mcp'] = False
        control.store(api, settings)
        code = self.prepare()
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}), patch.object(api, 'manage_service', return_value={'id': 'a' * 32, 'state': 'queued'}) as action:
            response = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': code})
        self.assertEqual(response.status_code, 403, response.text)
        action.assert_not_called()
