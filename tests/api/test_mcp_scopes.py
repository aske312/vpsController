import copy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
from tests.api import test_assistant_control as fixtures
import assistant_control as control
import assistant_commands
import assistant_mihomo
import assistant_roles

runner = fixtures.runner


class ScopeTests(unittest.TestCase):
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def setUp(self):
        fixtures.AssistantTests.setUp(self)
        self.save()
        self.client = TestClient(api.app)
        self.rows = [{'id': 'own', 'revision': 'a' * 64, 'connections': []}, {'id': 'other', 'revision': 'b' * 64, 'connections': []}]
        self.bridge = self.enterContext(patch.object(assistant_mihomo, 'request', side_effect=self.transport))

    def transport(self, method, path, *args, **kwargs):
        if method == 'GET' and path == '/profiles': return {'items': copy.deepcopy(self.rows)}
        return {'items': []}

    def headers(self, module, actor='panel'):
        root = control.read(api)['control_token']
        return {'Authorization': 'Bearer ' + control.mcp_transport_token(root, module, actor),
                'X-MCP-Module': module, 'X-MCP-Actor': actor}

    def switches(self, vps=True, mihomo=True):
        value = control.read(api)
        value['mcp'].update({'vps-controller-mcp': vps, 'mihomo-mcp': mihomo})
        control.store(api, value)

    def tool(self, name, module, actor='panel', **arguments):
        return self.client.post('/api/assistant/control-tool', headers=self.headers(module, actor), json={'name': name, **arguments})

    def test_mihomo_works_with_vps_disabled_and_vps_cannot_reach_disabled_mihomo(self):
        self.switches(vps=False)
        self.assertEqual(self.tool('mihomo_api', 'mihomo-mcp', path='/profiles').status_code, 200)
        self.assertEqual(self.tool('vps_overview', 'vps-controller-mcp').status_code, 403)
        self.switches(mihomo=False)
        with patch.object(api, 'overview', return_value={'server': {}, 'resources': {}}):
            self.assertEqual(self.tool('vps_overview', 'vps-controller-mcp').status_code, 200)
        before = self.bridge.call_count
        for name in ('mihomo_api', 'mihomo_capabilities', 'mihomo_user', 'mihomo_compare', 'vps_user', 'vps_compare'):
            self.assertEqual(self.tool(name, 'vps-controller-mcp', path='/profiles').status_code, 403)
            legacy = {'Authorization': 'Bearer ' + control.read(api)['control_token'], 'X-MCP-Actor': 'panel'}
            self.assertEqual(self.client.post('/api/assistant/control-tool', headers=legacy, json={'name': name, 'path': '/profiles'}).status_code, 403)
        self.assertEqual(before, self.bridge.call_count)

    def test_developer_and_operation_discovery_tools_cannot_use_mihomo_credentials(self):
        for name in ('developer_inspect', 'operation_inventory'):
            with self.subTest(name=name):
                self.assertEqual(self.tool(name, 'mihomo-mcp').status_code, 403)

    def test_scoped_credentials_cannot_change_module_actor_or_drop_scope(self):
        original = self.headers('mihomo-mcp')
        for changes in ({'X-MCP-Module': 'vps-controller-mcp'}, {'X-MCP-Actor': '11'}, {'X-MCP-Module': ''}):
            headers = {**original, **changes}
            self.assertIn(self.client.post('/api/assistant/control-tool', headers=headers, json={'name': 'vps_overview'}).status_code, (401, 403))
        headers = {key: value for key, value in original.items() if key != 'X-MCP-Module'}
        self.assertEqual(self.client.post('/api/assistant/control-tool', headers=headers, json={'name': 'vps_overview'}).status_code, 401)
        self.assertEqual(self.tool('mihomo_api', 'vps-controller-mcp', path='/profiles').status_code, 403)
        self.assertEqual(self.tool('vps_overview', 'mihomo-mcp').status_code, 403)

    def test_new_diagnostics_cannot_cross_scope_or_bypass_disabled_vps_connector(self):
        names = ('application_diagnostics', 'operation_timeline', 'integration_probe', 'diagnostic_baseline', 'diagnostic_alerts', 'vps_terminal')
        for name in names:
            self.assertEqual(self.tool(name, 'mihomo-mcp').status_code, 403)
        self.switches(vps=False)
        for name in names:
            self.assertEqual(self.tool(name, 'vps-controller-mcp').status_code, 403)

    def test_subscriber_has_only_mihomo_scope_and_still_cannot_read_other_profile(self):
        value = control.read(api)
        value['accounts'][0].update(role='user', products=['mihomo'], mihomo_profile_id='own', permission_overrides={})
        control.store(api, value)
        response = self.tool('mihomo_api', 'mihomo-mcp', actor='11', path='/profiles')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row['id'] for row in response.json()['items']], ['own'])
        self.assertEqual(self.tool('mihomo_api', 'mihomo-mcp', actor='11', path='/profiles/other/config').status_code, 403)
        self.assertEqual(self.tool('vps_services', 'vps-controller-mcp', actor='11').status_code, 403)

    def test_disabling_mihomo_prevents_submitting_prepared_change(self):
        response = self.tool('mihomo_api', 'mihomo-mcp', method='PATCH', path='/profiles/own', body={'name': 'Changed', 'expected_revision': 'a' * 64})
        self.assertEqual(response.status_code, 200, response.text)
        pending = control.operations.read(assistant_commands.pending_path(api, 'panel'))
        self.switches(mihomo=False)
        with self.assertRaises(api.HTTPException) as error:
            assistant_commands.execute(api, 'panel', '/confirm ' + pending['code'])
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(any(call.args[0] != 'GET' for call in self.bridge.call_args_list))

    def test_legacy_configuration_migrates_without_new_user_permissions(self):
        value = control.read(api)
        before = assistant_roles.effective(value, '11')
        path = control.directory(api) / 'ai-agent' / (value['revision'] + '.nodera.json')
        extension = control.operations.read(path)
        extension.pop('mcp_scopes'); extension.pop('mcp_scope_installed')
        control.operations.atomic_json(path, extension)
        migrated = control.read(api)
        self.assertTrue(migrated['mcp']['mihomo-mcp'])
        self.assertIn('mihomo-mcp', migrated['mcp_installed'])
        self.assertEqual(before, assistant_roles.effective(migrated, '11'))
        self.switches(mihomo=False)
        payload = self.payload()
        payload['expected_revision'] = control.read(api)['revision']
        payload['mcp'].pop('mihomo-mcp')
        self.assertFalse(self.save(payload)['settings']['mcp']['mihomo-mcp'])

    def test_subprocess_configs_contain_only_required_secrets_and_one_actor(self):
        settings = {'mcp': dict.fromkeys(control.MCP_MODULES, True), 'modules': {'ai-agent': True, 'nodera-bot': True},
                    'control_token': 'root-private-token', 'api_key': 'provider-secret', 'providers': [{'api_key': 'other-provider-secret'}],
                    'bot_token': 'bot-secret', 'telegram_api_id': 123, 'telegram_api_hash': 'telegram-hash',
                    'accounts': [{'telegram_id': 11, 'session': 'own-session', 'vps_access': True, 'mihomo_profile_id': 'own'},
                                 {'telegram_id': 22, 'session': 'foreign-session'}]}
        outputs = {module: runner.scoped_mcp_settings(settings, '11', module) for module in control.MCP_MODULES}
        for module, value in outputs.items():
            text = json.dumps(value)
            for secret in ('provider-secret', 'other-provider-secret', 'bot-secret', 'foreign-session', 'root-private-token'):
                self.assertNotIn(secret, text)
            self.assertEqual(len(value['accounts']), 1)
            if module == 'telegram-mcp':
                self.assertEqual(value['accounts'][0]['session'], 'own-session')
                self.assertEqual(value['control_token'], control.mcp_transport_token(settings['control_token'], module, '11'))
            else:
                self.assertNotIn('own-session', text)
                self.assertNotIn('telegram-hash', text)
                self.assertEqual(value['control_token'], control.mcp_transport_token(settings['control_token'], module, '11'))
        self.assertNotEqual(outputs['mihomo-mcp']['control_token'], outputs['vps-controller-mcp']['control_token'])

    def test_operation_reader_rejects_system_operations_even_for_mihomo_administrator(self):
        identity = 'a' * 32
        with patch.object(api, 'get_application_operation', return_value={'id': identity, 'action': 'reboot', 'state': 'succeeded'}):
            self.assertEqual(self.tool('mihomo_operation', 'mihomo-mcp', operation_id=identity).status_code, 403)
        with patch.object(api, 'get_application_operation', return_value={'id': identity, 'action': 'mihomo-profile-update', 'state': 'succeeded', 'password': 'secret'}):
            result = self.tool('mihomo_operation', 'mihomo-mcp', operation_id=identity)
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('secret', result.text)

    def test_modern_catalogs_are_disjoint_and_runtime_permissions_match_scopes(self):
        (control.runtime_directory('ai-agent') / 'version').write_text('1.15.0')
        status = control.status(api)
        vps, mihomo = set(status['tools']['vps-controller-mcp']), set(status['tools']['mihomo-mcp'])
        self.assertTrue(vps and mihomo)
        self.assertFalse(vps & mihomo)
        self.assertTrue(all(name.startswith('mihomo_') for name in mihomo))
        settings = {'mcp': dict.fromkeys(control.MCP_MODULES, True), 'modules': {}, 'accounts': [
            {'telegram_id': 11, 'mihomo_profile_id': 'own', 'effective_permissions': ['subscription_read']}]}
        self.assertTrue(runner.allowed(settings, '11', 'mihomo-mcp'))
        self.assertFalse(runner.allowed(settings, '11', 'vps-controller-mcp'))
