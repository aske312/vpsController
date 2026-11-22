"""Nodera can operate an assigned subscription without acquiring global Mihomo rights."""
import copy
import json
import unittest
import uuid
import io
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api import test_assistant_control as fixtures
from tests.api.support import api
import assistant_commands as commands
import assistant_control as control
import assistant_mihomo as bridge


class MihomoRequestErrors(unittest.TestCase):
    def test_unverified_relay_preserves_a_typed_safe_error(self):
        body = {'code': 'relay_not_verified', 'detail': 'PRIVATE DEBUG'}
        error = bridge.urllib.error.HTTPError('http://127.0.0.1', 409, 'blocked', {}, io.BytesIO(json.dumps(body).encode()))
        with patch.dict('os.environ', {'ADMIN_PASSWORD': 'fixture'}), patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(api.HTTPException) as caught:
                bridge.request('GET', '/profiles/own/config')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail['code'], 'relay_not_verified')
        self.assertNotIn('PRIVATE', json.dumps(caught.exception.detail))

    def test_request_validation_identifies_revision_field_without_echoing_input_or_context(self):
        detail = {'detail': [{'loc': ['body', 'expected_revision'], 'type': 'string_pattern_mismatch', 'input': 'PRIVATE INPUT', 'msg': 'PRIVATE MESSAGE', 'ctx': {'pattern': 'PRIVATE PATTERN'}},
                             {'loc': ['body', 'values', 'PRIVATE FIELD'], 'type': 'dict_type'}]}
        error = bridge.urllib.error.HTTPError('http://127.0.0.1', 422, 'invalid', {}, io.BytesIO(json.dumps(detail).encode()))
        with patch.dict('os.environ', {'ADMIN_PASSWORD': 'fixture'}), patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(api.HTTPException) as caught: bridge.request('POST', '/routing/validate', {})
        self.assertEqual(caught.exception.detail['fields'][0]['field'], 'expected_revision')
        self.assertIn('64 hex', caught.exception.detail['fields'][0]['issue'])
        self.assertNotIn('PRIVATE', json.dumps(caught.exception.detail))

    def test_policy_errors_expose_only_allowlisted_fields_and_never_upstream_secrets(self):
        detail = {'detail': {'code': 'invalid_routing_policy', 'fields': ['rules.0.kind', 'groups.1.members.0', 'private_key', 'secret-value'], 'message': 'password=private-secret'}}
        error = bridge.urllib.error.HTTPError('http://127.0.0.1', 422, 'invalid', {}, io.BytesIO(json.dumps(detail).encode()))
        with patch.dict('os.environ', {'ADMIN_PASSWORD': 'fixture'}), patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(api.HTTPException) as caught: bridge.request('POST', '/routing/validate', {})
        self.assertIn('rules.0.kind', caught.exception.detail)
        self.assertIn('groups.1.members.0', caught.exception.detail)
        self.assertNotIn('private', caught.exception.detail)
        self.assertNotIn('secret', caught.exception.detail)


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
        if method == 'POST' and path == '/routing/validate': return {'valid': True}
        if method == 'GET': return {'ok': True}
        return {'id': operation_id, 'state': 'queued'}

    def test_routing_rules_and_groups_reach_the_same_schema_as_the_ui_only_after_human_confirmation(self):
        from tests.api.support import manager
        folder = self.root / 'mihomo-settings'
        folder.mkdir()
        for key, filename in [('ROUTING_SETTINGS_FILE', 'routing-policy.json'), ('PRESET_SETTINGS_FILE', 'presets.json'), ('PERSONAL_RULES_FILE', 'personal.json')]:
            self.enterContext(patch.object(manager, key, folder / filename))
        self.enterContext(patch.object(manager, 'SETTINGS_ROOT', folder))
        self.enterContext(patch.object(manager, 'profiles', return_value=[]))
        policy = {'groups': [{'name': 'Media', 'type': 'fallback', 'members': ['*']}],
                  'rules': [{'kind': 'domain-suffix', 'value': 'example.test', 'target': 'Media'}]}
        initial = manager.get_routing_schema()
        writes = []
        def transport(method, path, body=None, operation_id=None):
            if method == 'GET' and path == '/routing/schema': return manager.get_routing_schema()
            if method == 'POST' and path == '/routing/validate': return manager.preview_routing_settings(manager.ModuleSettingsPatch.model_validate(body))
            self.assertEqual((method, path), ('PATCH', '/routing/settings'))
            writes.append(body)
            return manager.patch_routing_settings(manager.ModuleSettingsPatch.model_validate(body))
        with patch.object(bridge, 'request', side_effect=transport):
            response = bridge.call(api, 'panel', 'PATCH', '/routing/settings',
                {'values': {'structured_policy': json.dumps(policy)}, 'expected_revision': initial['revision']})
            self.assertTrue(response['confirmation_required'])
            self.assertEqual(writes, [])
            self.assertFalse(json.loads(manager.get_routing_schema()['values'].get('structured_policy') or '{}').get('rules'))
            with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
                pending = self.client.get('/api/assistant/pending-action').json()['pending_actions']
                row = next(row for row in pending if row['operation_id'] == response['operation_id'])
                confirmed = self.client.post('/api/assistant/service-confirm', json={'code': row['code'], 'decision': 'confirm'})
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            actual = json.loads(bridge.call(api, 'panel', 'GET', '/routing/schema', {})['values']['structured_policy'])
            self.assertEqual(actual['rules'], policy['rules'])
            self.assertEqual(actual['groups'][0]['name'], 'Media')
            self.assertEqual(len(writes), 1)
            with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
                self.assertEqual(self.client.get('/api/assistant/pending-action?all_actors=true').json()['operations'], [])
                saved_path = commands.pending_path(api, 'panel', code=row['code'])
                legacy = control.operations.read(saved_path)
                legacy.pop('completion_state', None)
                control.operations.atomic_json(saved_path, legacy)
                self.assertEqual(self.client.get('/api/assistant/pending-action?all_actors=true').json()['operations'], [])

            before = manager.ROUTING_SETTINGS_FILE.read_bytes()
            policy['rules'][0]['kind'] = 'DOMAIN-KEYWORD'
            with self.assertRaises(api.HTTPException) as rejected:
                bridge.call(api, 'panel', 'PATCH', '/routing/settings', {'values': {'structured_policy': json.dumps(policy)}, 'expected_revision': manager.get_routing_schema()['revision']})
            self.assertEqual(rejected.exception.status_code, 422)
            self.assertEqual(rejected.exception.detail['fields'], ['rules.0.kind'])
            self.assertEqual(manager.ROUTING_SETTINGS_FILE.read_bytes(), before)
            self.assertEqual(commands.pending_actions(api, 'panel'), [])

    def test_old_manager_cannot_queue_unvalidated_routing_changes(self):
        with patch.object(bridge, 'request', side_effect=api.HTTPException(404, 'not found')):
            with self.assertRaises(api.HTTPException) as rejected:
                bridge.call(api, 'panel', 'PATCH', '/routing/settings', {'values': {'structured_policy': '{}'}})
        self.assertEqual(rejected.exception.status_code, 409)
        self.assertIn('Обновите компонент Mihomo', rejected.exception.detail)
        self.assertEqual(commands.pending_actions(api, 'panel'), [])

    def tool(self, **payload):
        return self.client.post('/api/assistant/control-tool', headers=self.headers, json=payload)

    def test_own_url_rotation_waits_for_human_confirmation_and_checks_scope(self):
        response = self.tool(name='mihomo_api', method='POST', path='/profiles/own/subscription/rotate', body={'expected_revision': 'a' * 64})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['confirmation_required'])
        status = self.tool(name='mihomo_operation', operation_id=response.json()['operation_id'])
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()['state'], 'awaiting_confirmation')
        self.assertFalse(any(method != 'GET' for method, *_ in self.calls))
        record = control.operations.read(commands.pending_path(api, '11'))
        commands.execute(api, '11', '/confirm ' + record['code'])
        commands.execute(api, '11', '/confirm ' + record['code'])
        writes = [call for call in self.calls if call[0] != 'GET']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:3], ('POST', '/profiles/own/subscription/rotate', {'expected_revision': 'a' * 64}))
        for path in ('/profiles/other/subscription/rotate', '/profiles/own/subscription/rotate?format=yaml'):
            self.assertEqual(self.tool(name='mihomo_api', method='POST', path=path, body={'expected_revision': 'c' * 64}).status_code, 403)

    def test_owner_can_create_profile_only_after_validation_and_confirmation(self):
        body = {'name': 'New user', 'connections': [{'component': 'transport-wg', 'device_id': 'common'}],
                'devices': [{'id': 'common', 'name': 'Common'}], 'routing': {'structured_policy': '{"groups":[],"rules":[]}'}}
        calls = []
        def transport(method, path, payload=None, operation_id=None):
            calls.append((method, path, copy.deepcopy(payload)))
            if path == '/profiles/validate': return {'valid': True}
            return {'id': operation_id, 'state': 'queued'}
        with patch.object(bridge, 'request', side_effect=transport):
            prepared = bridge.call(api, 'panel', 'POST', '/profiles', body)
            self.assertEqual([row[1] for row in calls], ['/profiles/validate'])
            pending = next(row for row in commands.pending_actions(api, 'panel') if row['operation_id'] == prepared['operation_id'])
            commands.execute(api, 'panel', '/confirm ' + pending['code'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1], '/profiles')
        self.assertEqual(calls[1][2]['routing'], body['routing'])
        self.assertEqual(calls[1][2]['operation_id'], prepared['operation_id'])
        self.assertEqual(self.tool(name='mihomo_api', method='POST', path='/profiles', body=body).status_code, 403)

    def test_invalid_profile_never_creates_a_confirmation(self):
        with patch.object(bridge, 'request', side_effect=api.HTTPException(422, 'Invalid profile')):
            with self.assertRaises(api.HTTPException): bridge.call(api, 'panel', 'POST', '/profiles', {'name': 'Bad'})
        self.assertEqual(commands.pending_actions(api, 'panel'), [])

    def test_owner_assignment_keeps_account_role_and_requires_its_own_confirmation(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['assistant'], mihomo_profile_id=None, permission_overrides={'web': False})
        control.store(api, settings)
        prepared = bridge.call(api, 'panel', 'PATCH', '/accounts/11/profile', {'profile_id': 'own'})
        self.assertIsNone(control.read(api)['accounts'][0]['mihomo_profile_id'])
        pending = next(row for row in commands.pending_actions(api, 'panel') if row['operation_id'] == prepared['operation_id'])
        with patch.object(control, 'require_installed'):
            commands.execute(api, 'panel', '/confirm ' + pending['code'])
        account = control.read(api)['accounts'][0]
        self.assertEqual(account['mihomo_profile_id'], 'own')
        self.assertEqual(account['role'], 'user')
        self.assertEqual(account['permission_overrides'], {'web': False})
        self.assertEqual(set(account['products']), {'assistant', 'mihomo'})
        for method, path, body in [('GET', '/accounts', {}), ('PATCH', '/accounts/11/profile', {'profile_id': 'other'})]:
            with self.assertRaises(api.HTTPException) as denied: bridge.call(api, '11', method, path, body)
            self.assertEqual(denied.exception.status_code, 403)

    def prepare(self, body=None):
        return self.tool(name='mihomo_api', method='PATCH', path='/profiles/own',
                         body=body or {'name': 'Renamed', 'expected_revision': 'a' * 64})

    def test_linked_subscriber_reads_only_own_profile_and_identity(self):
        result = self.tool(name='mihomo_api', path='/profiles')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([entry['id'] for entry in result.json()['items']], ['own'])
        self.assertEqual(result.json()['items'][0]['devices'][0]['id'], 'phone')
        self.assertNotIn('secret-wireguard', result.text)
        identity = self.tool(name='vps_user').json()
        self.assertEqual(identity['role'], 'user')
        self.assertEqual(identity['mihomo_profile_id'], 'own')
        self.assertEqual(identity['name'], 'Александр')
        self.assertEqual(self.tool(name='vps_services').status_code, 403)

    def test_subscriber_group_catalog_has_ids_but_no_shared_rule_values_or_endpoints(self):
        payload = {'values': {'structured_policy': json.dumps({'groups': [{'id': 'media-id', 'name': 'Media', 'url': 'https://private-health.test/', 'members': ['connection:private']}],
                    'rules': [{'kind': 'domain', 'value': 'private-domain.test', 'target': 'Media'}]})}, 'structured_policy_schema': {'type': 'object'}}
        with patch.object(bridge, 'request', return_value=payload):
            response = self.tool(name='mihomo_api', path='/routing/schema')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['policy_groups'][0]['id'], 'media-id')
        self.assertEqual(response.json()['policy_groups'][0]['rule_count'], 1)
        self.assertNotIn('private-', response.text)
        self.assertNotIn('values', response.json())
        self.assertEqual(self.tool(name='mihomo_api', method='PATCH', path='/routing/settings', body={'values': {}}).status_code, 403)

    def test_subscription_information_commands_are_scoped_and_do_not_use_ai(self):
        settings = control.read(api)
        settings['modules']['ai-agent'] = False
        settings['modules']['nodera-bot'] = True
        settings['bot_token'] = self.payload()['bot_token']
        settings['accounts'][0].update(role='user', products=['mihomo'], permission_overrides={})
        control.store(api, settings)
        self.rows[0]['limits_status'] = {'state': 'active', 'used_bytes': 2500000000, 'expires_at': 1800000000}
        with patch.object(control, 'runtime_request') as model:
            for command in ('/profiles', '/profile', '/devices', '/traffic', '/profile own'):
                response = self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': command})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn('Private foreign', response.text)
                self.assertNotIn('secret-wireguard', response.text)
                self.assertNotIn('b' * 64, response.text)
            response = self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/traffic'})
            self.assertIn('2.50 GB / 10 GB', response.json()['text'])
            response = self.client.post('/api/assistant/bot-command', headers=self.headers, json={'command': '/profile other'})
            self.assertNotIn('Private foreign', response.text)
            self.assertIn('недоступ', response.json()['text'])
            model.assert_not_called()

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

    def test_client_reports_follow_the_linked_profile_read_boundary(self):
        response = self.tool(name='mihomo_api', method='GET', path='/profiles/own/client-reports?device_id=phone')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.calls[-1][:2], ('GET', '/profiles/own/client-reports?device_id=phone'))
        before = len(self.calls)
        for path in ('/profiles/other/client-reports', '/profiles/own/client-reports?device_id=../other'):
            self.assertIn(self.tool(name='mihomo_api', method='GET', path=path).status_code, (403, 422))
        self.assertEqual(len(self.calls), before)
        capabilities = self.tool(name='mihomo_capabilities').json()
        self.assertIn('/profiles/own/client-reports', capabilities['reads'])

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

    def test_source_pages_keep_subscription_scope_and_reject_unsafe_queries(self):
        path = '/profiles/own/source?section=connections&device_id=phone&offset=0&limit=20'
        self.assertEqual(self.tool(name='mihomo_api', path=path).status_code, 200)
        for path in ('/profiles/other/source', '/profiles/catalog', '/profiles/own/source?limit=21',
                     '/profiles/own/source?offset=-1', '/profiles/own/source?section=credentials'):
            self.assertIn(self.tool(name='mihomo_api', path=path).status_code, (403, 422))
        self.assertEqual(self.tool(name='mihomo_api', method='POST', path='/profiles',
                                  body={'name': 'New', 'copy_from': {'profile_id': 'own'}}).status_code, 403)

    def test_copy_only_validates_before_human_confirmation_and_submits_once(self):
        settings = control.read(api)
        settings['accounts'][0]['bot_access'] = True
        control.store(api, settings)
        body = {'name': 'New', 'copy_from': {'profile_id': 'own', 'device_id': 'phone', 'expected_revision': 'a' * 64}}
        def transport(method, path, body=None, operation_id=None):
            if path == '/profiles/validate':
                self.calls.append((method, path, copy.deepcopy(body), operation_id))
                return {'valid': True, 'copy_preview': {'connection_count': 56, 'new_credentials': True}}
            return self.transport(method, path, body, operation_id)
        with patch.object(bridge, 'request', side_effect=transport):
            response = self.tool(name='mihomo_api', method='POST', path='/profiles', body=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['copy_preview']['connection_count'], 56)
            self.assertFalse(any(method == 'POST' and path == '/profiles' for method, path, *_ in self.calls))
            record = control.operations.read(commands.pending_path(api, '11'))
            pending = commands.pending_actions(api, '11')
            self.assertEqual(next(row for row in pending if row['operation_id'] == record['operation_id'])['changes']['copy_preview']['connection_count'], 56)
            with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
                first = self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': record['code']})
                self.client.post('/api/assistant/service-confirm', json={'telegram_id': 11, 'code': record['code']})
            self.assertEqual(first.status_code, 200, first.text)
        writes = [row for row in self.calls if row[:2] == ('POST', '/profiles')]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2]['copy_from'], body['copy_from'])

    def test_old_manager_cannot_prepare_or_execute_an_empty_copy(self):
        settings = control.read(api)
        settings['accounts'][0]['bot_access'] = True
        control.store(api, settings)
        body = {'name': 'New', 'copy_from': {'profile_id': 'own', 'device_id': 'phone', 'expected_revision': 'a' * 64}}
        with patch.object(bridge, 'request', return_value={'valid': True}) as request:
            response = self.tool(name='mihomo_api', method='POST', path='/profiles', body=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(request.call_args.args[:2], ('POST', '/profiles/validate'))
        self.assertEqual(commands.pending_actions(api, '11'), [])
        record = {'method': 'POST', 'path': '/profiles', 'body': body}
        with patch.object(bridge, 'request', return_value={'valid': True}) as request:
            with self.assertRaises(api.HTTPException) as caught:
                bridge.confirm(api, '11', record)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(request.call_count, 1)

    def test_typed_relay_error_reaches_the_agent_without_private_diagnostics(self):
        with patch.object(bridge, 'request', side_effect=api.HTTPException(409, {'code': 'relay_not_verified', 'message': 'PRIVATE DEBUG'})):
            response = self.tool(name='mihomo_api', path='/profiles/own/config')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'relay_not_verified')
        self.assertNotIn('PRIVATE DEBUG', response.text)

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
