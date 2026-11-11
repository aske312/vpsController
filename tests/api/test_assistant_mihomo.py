"""Nodera can operate an assigned subscription without acquiring global Mihomo rights."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api import test_assistant_control as fixtures
from tests.api.support import api
import assistant_commands as commands
import assistant_control as control
import assistant_mihomo as bridge


class MihomoOperatorTests(unittest.TestCase):
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def setUp(self):
        fixtures.AssistantTests.setUp(self)
        self.rows = [
            {'id': 'own', 'name': 'Own subscription', 'revision': 'a' * 64, 'limits': {'traffic_gb': 10},
             'connections': [{'id': 'conn', 'component': 'transport-wg', 'credential': {'private_key': 'secret-wireguard'}}],
             'devices': [{'id': 'phone', 'name': 'Phone', 'hwid_hash': 'b' * 64, 'manual': False, 'os': 'android'}]},
            {'id': 'other', 'name': 'Private foreign subscription', 'revision': 'c' * 64},
        ]
        self.calls = []
        self.enterContext(patch.object(bridge, 'request', side_effect=self.transport))
        payload = self.payload()
        payload['accounts'] = [{'telegram_id': 11, 'name': 'Александр', 'bot_chat_access': True,
                                'mihomo_profile_id': 'own', 'address_gender': 'neutral'}]
        self.save(payload)
        self.client = TestClient(api.app)
        value = control.read(api)
        self.headers = {'Authorization': 'Bearer ' + value['control_token'], 'X-MCP-Actor': '11'}

    def transport(self, method, path, body=None, operation_id=None):
        self.calls.append((method, path, copy.deepcopy(body), operation_id))
        if method == 'GET' and path == '/profiles': return {'items': copy.deepcopy(self.rows)}
        if method == 'GET': return {'ok': True}
        return {'id': operation_id, 'state': 'queued'}

    def tool(self, **payload):
        return self.client.post('/api/assistant/control-tool', headers=self.headers, json=payload)

    def prepare(self, body=None):
        return self.tool(name='mihomo_api', method='PATCH', path='/profiles/own',
                         body=body or {'name': 'Renamed', 'expected_revision': 'a' * 64})

    def test_linked_subscriber_reads_only_own_profile_and_identity(self):
        result = self.tool(name='mihomo_api', path='/profiles')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([entry['id'] for entry in result.json()['items']], ['own'])
        self.assertNotIn('secret-wireguard', result.text)
        identity = self.tool(name='vps_user').json()
        self.assertEqual(identity['role'], 'user')
        self.assertEqual(identity['mihomo_profile_id'], 'own')
        self.assertEqual(identity['name'], 'Александр')
        self.assertEqual(self.tool(name='vps_services').status_code, 403)

    def test_foreign_profiles_global_settings_limits_and_path_traversal_are_denied(self):
        for method, path, body in [
            ('GET', '/profiles/other/subscription', {}), ('DELETE', '/profiles/other/devices/phone', {}),
            ('PATCH', '/profiles/own', {'limits': {'traffic_gb': 0}}),
            ('PATCH', '/dns/settings', {'values': {}}), ('POST', '/modules/transport-wg/install', {}),
            ('GET', '//example.com/profiles', {}), ('GET', '/profiles/own/../other/config', {}),
            ('GET', '/profiles/own%2f..%2fother/config', {}), ('GET', '/profiles/own/subscription?device_id=../other', {}),
        ]:
            result = self.tool(name='mihomo_api', method=method, path=path, body=body)
            self.assertIn(result.status_code, (403, 422), (path, result.text))
        self.assertFalse(any(method != 'GET' for method, *_ in self.calls))

    def test_subscriber_can_discover_connection_schema_without_global_settings_or_personal_rules(self):
        with patch.object(bridge, 'request', return_value={'items': [{'id': 'transport-wg', 'installed': True, 'connection_settings': [{'key': 'name'}], 'settings_values': {'private_key': 'global-secret'}}]}):
            result = self.tool(name='mihomo_api', path='/modules')
            self.assertEqual(result.status_code, 200)
            self.assertIn('connection_settings', result.text)
            self.assertNotIn('global-secret', result.text)
        with patch.object(bridge, 'request', return_value={'schema': [{'key': 'mode'}], 'personal_rules': [{'title': 'Foreign rule'}], 'values': {'secret': 'global-secret'}}):
            result = self.tool(name='mihomo_api', path='/routing/schema')
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('Foreign rule', result.text)
            self.assertNotIn('global-secret', result.text)

    def test_profile_change_requires_human_confirmation_and_preserves_device_identity(self):
        self.assertEqual(self.prepare({'devices': [{'id': 'phone', 'name': 'My phone'}], 'expected_revision': 'a' * 64}).status_code, 200)
        self.assertFalse(any(method != 'GET' for method, *_ in self.calls))
        record = control.operations.read(commands.pending_path(api, '11'))
        self.assertNotIn('code', self.prepare().json())
        # Prepare the device change once more after checking the tool response shape.
        self.prepare({'devices': [{'id': 'phone', 'name': 'My phone'}], 'expected_revision': 'a' * 64})
        record = control.operations.read(commands.pending_path(api, '11'))
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
            first = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': record['code']})
            second = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': record['code']})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json(), first.json())
        writes = [entry for entry in self.calls if entry[0] != 'GET']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2]['devices'][0]['hwid_hash'], 'b' * 64)
        self.assertEqual(writes[0][2]['devices'][0]['os'], 'android')
        self.assertFalse(writes[0][2]['devices'][0]['manual'])
        with patch.object(api, 'get_application_operation', return_value={'id': record['operation_id'], 'action': 'mihomo-profile-update', 'state': 'succeeded'}):
            self.assertEqual(self.tool(name='vps_operation', operation_id=record['operation_id']).json()['state'], 'succeeded')
            self.assertEqual(self.tool(name='vps_operation', operation_id='f' * 32).status_code, 403)

    def test_revocation_and_stale_revision_block_prepared_actions(self):
        self.assertEqual(self.prepare({'name': 'Renamed'}).status_code, 409)
        self.assertEqual(self.prepare().status_code, 200)
        record = control.operations.read(commands.pending_path(api, '11'))
        settings = control.read(api)
        settings['accounts'][0]['mihomo_profile_id'] = None
        settings['revision'] = uuid.uuid4().hex
        control.store(api, settings)
        with self.assertRaises(api.HTTPException):
            commands.execute(api, '11', '/confirm ' + record['code'])
        self.assertFalse(any(method != 'GET' for method, *_ in self.calls))

    def test_subscriber_can_confirm_in_telegram_but_cannot_use_global_commands(self):
        settings = control.read(api)
        settings['modules']['nodera-bot'] = True
        settings['bot_token'] = self.payload()['bot_token']
        control.store(api, settings)
        self.prepare()
        record = control.operations.read(commands.pending_path(api, '11'))
        result = self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/confirm ' + record['code']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/service mihomo stop'}).status_code, 403)
        self.assertEqual(self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/operation ' + 'f' * 32}).status_code, 403)

    def test_definite_rejection_can_be_corrected_but_unknown_writes_are_not_replayed(self):
        self.prepare()
        record = control.operations.read(commands.pending_path(api, '11'))
        with patch.object(bridge, 'request', side_effect=api.HTTPException(409, 'stale')):
            with self.assertRaises(api.HTTPException): commands.execute(api, '11', '/confirm ' + record['code'])
        self.assertFalse(control.operations.read(commands.pending_path(api, '11'))['submitted'])
        self.prepare()
        record = control.operations.read(commands.pending_path(api, '11'))
        with patch.object(bridge, 'request', side_effect=api.HTTPException(503, 'unknown')) as transport:
            with self.assertRaises(api.HTTPException): commands.execute(api, '11', '/confirm ' + record['code'])
            with self.assertRaises(api.HTTPException): commands.execute(api, '11', '/confirm ' + record['code'])
            transport.assert_called_once()

    def test_administrator_has_module_dns_routing_and_subscription_control(self):
        settings = control.read(api)
        settings['accounts'][0]['bot_access'] = True
        control.store(api, settings)
        capabilities = self.tool(name='mihomo_capabilities').json()
        self.assertEqual(capabilities['role'], 'administrator')
        for method, path in [('POST', '/modules/transport-wg/install'), ('DELETE', '/profiles/other'), ('PATCH', '/dns/settings'), ('PATCH', '/routing/presets')]:
            result = self.tool(name='mihomo_api', method=method, path=path, body={})
            self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(any(method != 'GET' for method, *_ in self.calls))

    def test_link_and_address_preferences_survive_legacy_bot_edits(self):
        payload = self.payload()
        payload.update(component='nodera-bot', expected_revision=control.read(api)['revision'], bot_token=None)
        payload['accounts'] = [{'telegram_id': 11, 'name': 'Александр', 'bot_access': False}]
        self.save(payload)
        user = control.read(api)['accounts'][0]
        self.assertEqual(user['mihomo_profile_id'], 'own')
        self.assertEqual(user['address_gender'], 'neutral')
        legacy = json.loads((self.root / 'assistant/ai-agent' / (control.read(api)['revision'] + '.json')).read_text())
        self.assertNotIn('mihomo_profile_id', legacy['accounts'][0])

    def test_user_can_edit_own_profile_but_cannot_change_any_limit_or_service(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['mihomo'], permission_overrides={})
        control.store(api, settings)
        for body in ({'limits': {'traffic_gb': 100}}, {'limits': {'days': 90}}, {'limits': {'speed_mbps': 100}}, {'limits': None}, {'limits': {}}):
            self.assertEqual(self.prepare({**body, 'expected_revision': 'a' * 64}).status_code, 403)
        self.assertEqual(self.prepare({'name': 'My name', 'expected_revision': 'a' * 64}).status_code, 200)
        for name in ('vps_services', 'vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics'):
            self.assertEqual(self.tool(name=name).status_code, 403)
        self.assertEqual(self.client.post('/api/assistant/bot-chat', headers=self.headers, json={'prompt': 'Hello'}).status_code, 403)

    def test_partial_quota_edit_preserves_expiry_and_speed_without_granting_expiry_right(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='moderator', products=['mihomo'], permission_overrides={'subscription_quota': True})
        control.store(api, settings)
        self.rows[0]['limits'] = {'traffic_gb': 10, 'speed_mbps': 5, 'days': 30}
        self.assertEqual(self.prepare({'limits': {'traffic_gb': 20}, 'expected_revision': 'a' * 64}).status_code, 200)
        record = control.operations.read(commands.pending_path(api, '11'))
        commands.execute(api, '11', '/confirm ' + record['code'])
        write = next(row for row in self.calls if row[0] == 'PATCH')
        self.assertEqual(write[2]['limits'], {'traffic_gb': 20, 'speed_mbps': 5, 'days': 30})
        self.assertEqual(self.prepare({'limits': {'days': 90}, 'expected_revision': 'a' * 64}).status_code, 403)

    def test_subscription_only_bot_delivers_own_link_without_ai_product(self):
        settings = control.read(api)
        settings['modules']['ai-agent'] = False
        settings['modules']['nodera-bot'] = True
        settings['bot_token'] = self.payload()['bot_token']
        settings['accounts'][0].update(role='user', products=['mihomo'], permission_overrides={})
        control.store(api, settings)
        with patch.object(bridge, 'request', return_value={'url': 'https://subscription.example/s/own'}), patch.object(control, 'runtime_request') as model:
            response = self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/subscription'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('https://subscription.example/s/own', response.json()['text'])
            self.assertEqual(self.client.post('/api/assistant/bot-chat', headers=self.headers, json={'prompt': 'Hi'}).status_code, 403)
            model.assert_not_called()
        settings['accounts'][0]['products'] = ['assistant']
        control.store(api, settings)
        self.assertEqual(self.tool(name='mihomo_api', path='/profiles/own/subscription').status_code, 403)
