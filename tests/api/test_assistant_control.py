import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from tests.api.support import api, load_module, ROOT
import assistant_control as control
import assistant_worker as worker
runner = load_module('assistant_image_runner', 'protocol-images/ai-agent/runner.py')


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(api, 'ACTION_FILE', self.root / 'action.json'))
        self.observe = self.enterContext(patch.object(api, 'observe_service', return_value={
            'active': False, 'unit_present': True, 'state': 'inactive', 'runtime': {'state': 'stopped'}}))
        self.enterContext(patch.dict('os.environ', {'AI_AGENT_RUNTIME_DIR': str(self.root / 'ai-runtime'), 'NODERA_BOT_RUNTIME_DIR': str(self.root / 'bot-runtime')}))
        for component in control.MODULES:
            control.ComponentRegistry(self.root).write_receipt(component, 'installed')
            runtime = control.runtime_directory(component)
            (runtime / 'venv/bin').mkdir(parents=True)
            (runtime / 'venv/bin/python').touch()
            (runtime / ('runner.py' if component == 'ai-agent' else 'bot.py')).touch()
            (runtime / 'version').write_text('1.0.0')

    def payload(self):
        return {'expected_revision': '0' * 32, 'component': 'ai-agent',
                'modules': {'ai-agent': True, 'nodera-bot': False},
                'mcp': {'telegram-mcp': True, 'vps-controller-mcp': True},
                'provider_url': 'https://provider.example/v1', 'model': 'configured-model', 'api_key': 'secret-ai',
                'telegram_api_id': 123, 'telegram_api_hash': 'a' * 32,
                'bot_token': '123456:abcdefghijklmnopqrstuvwxyz0123456789',
                'accounts': [{'telegram_id': 11, 'bot_access': True, 'telegram_access': True, 'vps_access': False,
                              'session': '1' + 'a' * 352, 'excluded_dialogs': [-1001]}]}

    def save(self, payload=None):
        return control.configure(api, control.Settings.model_validate(payload or self.payload()))

    def test_nodera_keeps_separate_api_keys_when_switching_and_never_reuses_a_key_at_another_url(self):
        legacy = self.save()['settings']
        first = legacy['providers'][0]
        second = {'id': 'b' * 32, 'name': 'Second', 'provider_url': first['provider_url'],
                  'model_mode': 'catalog', 'api_key': 'second-private-key'}
        payload = self.payload()
        payload.update(component='nodera', expected_revision=legacy['revision'], active_provider=second['id'],
                       providers=[{k: v for k, v in first.items() if k != 'api_key_configured'}, second],
                       bot_token=None)
        self.observe.return_value['active'] = True
        result = self.save(payload)
        self.assertNotIn('secret-ai', json.dumps(result))
        self.assertNotIn('second-private-key', json.dumps(result))
        self.assertEqual(control.read(api)['api_key'], 'second-private-key')
        with patch.object(control.assistant_provider, 'catalog', return_value=['model']) as catalog:
            control.model_catalog(api, control.Catalog(provider_url=first['provider_url'], provider_id=first['id']))
            catalog.assert_called_once_with(first['provider_url'], 'secret-ai')
        payload.update(expected_revision=result['settings']['revision'], active_provider=first['id'])
        payload['providers'][0]['provider_url'] = 'https://different.example/v1'
        second['api_key'] = None
        self.save(payload)
        value = control.read(api)
        self.assertFalse(value['api_key'])
        self.assertEqual(value['providers'][1]['api_key'], 'second-private-key')
        legacy_config = json.loads((self.root / 'assistant/ai-agent' / (value['revision'] + '.json')).read_text())
        self.assertNotIn('providers', legacy_config)
        self.assertNotIn('address_as', legacy_config['accounts'][0])

    def test_provider_usage_records_only_reported_tokens(self):
        self.save()
        value = control.read(api)
        identity = control.active_provider(value)
        control.provider_usage(api, value, {'usage': {'prompt_tokens': 41, 'completion_tokens': 9}})
        control.provider_usage(api, value, {})
        self.assertEqual(control.status(api)['provider_usage'][identity], {
            'requests': 2, 'prompt_tokens': 41, 'completion_tokens': 9, 'reported_requests': 1,
        })

    def test_telegram_identity_matches_only_the_verified_token(self):
        payload = self.payload()
        payload.update(component='nodera', bot_token='123456:abcdefghijklmnopqrstuvwxyz0123456789')
        control.bot_identity(api, payload['bot_token'], 'first_bot')
        control.bot_identity(api, '987654:abcdefghijklmnopqrstuvwxyz0123456789', 'another_bot')
        result = self.save(payload)
        self.assertEqual(result['bot_username'], 'first_bot')
        self.assertNotIn(payload['bot_token'], json.dumps(result))
        payload.update(expected_revision=result['settings']['revision'], bot_token='')
        self.assertIsNone(self.save(payload)['bot_username'])

    def test_bundle_installs_both_runtimes_and_restarts_only_a_running_bot(self):
        bundle = types.SimpleNamespace(DATA_DIR=self.root, INSTALL_DIR=ROOT, observe_service=self.observe)
        for active in (False, True):
            self.observe.return_value['active'] = active
            with (patch.object(worker, 'assert_nodera_unit_ownership'), patch.object(worker, 'require_idle'),
                  patch.object(worker, 'install') as install, patch.object(worker, 'perform') as perform,
                  patch.object(worker, 'checked') as checked):
                worker.install_nodera(bundle)
            self.assertEqual([call.args[1] for call in install.call_args_list], ['ai-agent', 'nodera-bot'])
            self.assertEqual(install.call_args.kwargs, {'start_bot': False})
            self.assertEqual(checked.called, active)
            self.assertEqual(perform.called, active)
            manifest = json.loads((ROOT / 'protocol-images/nodera/manifest.json').read_text(encoding='utf-8'))
            self.assertEqual((control.runtime_directory() / 'nodera-version').read_text().strip(), manifest['version'])

    def test_two_modules_and_mcp_options_have_no_false_health_claims(self):
        with patch.object(control, 'runtime_ready', return_value=False):
            status = control.status(api)
        self.assertEqual({m['id'] for m in status['modules']}, {'ai-agent', 'nodera-bot'})
        self.assertEqual({m['id'] for m in status['mcp_modules']}, set(control.MCP_MODULES))
        self.assertFalse(status['runtime_installed'])
        self.assertFalse(any(m['enabled'] for m in status['modules']))
        self.assertNotIn('healthy', json.dumps(status))

    def test_secret_preservation_clear_and_revision_conflict(self):
        result = self.save()
        serialized = json.dumps(result)
        for secret in ('secret-ai', 'a' * 32, '1' + 'a' * 352, self.payload()['bot_token']):
            self.assertNotIn(secret, serialized)
        self.assertTrue(result['settings']['accounts'][0]['session_configured'])
        payload = self.payload()
        payload.update(expected_revision=result['settings']['revision'], api_key=None, telegram_api_hash=None, bot_token=None)
        payload['accounts'][0]['session'] = None
        saved = self.save(payload)
        self.assertEqual(control.read(api)['api_key'], 'secret-ai')
        self.assertEqual(control.read(api)['accounts'][0]['session'], '1' + 'a' * 352)
        with self.assertRaises(control.operations.OperationConflict):
            self.save(payload)
        payload.update(expected_revision=saved['settings']['revision'], api_key='')
        payload['accounts'][0]['session'] = ''
        self.save(payload)
        self.assertFalse(control.read(api)['api_key'])
        self.assertFalse(control.read(api)['accounts'][0]['session'])

    def test_http_save_does_not_conflict_with_its_own_admission_lock(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        client = TestClient(api.app)
        payload = self.payload(); payload['component'] = 'nodera-bot'
        response = client.put('/api/assistant', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['settings']['bot_token_configured'])
        self.assertNotIn(payload['bot_token'], response.text)
        payload['expected_revision'] = response.json()['settings']['revision']
        with control.operations.mutation_lock(self.root):
            blocked = client.put('/api/assistant', json=payload)
        self.assertEqual(blocked.status_code, 409)

    def test_draft_bot_token_check_needs_no_enable_or_users_and_does_not_save(self):
        self.save()
        before = control.read(api)
        self.assertFalse(before['modules']['nodera-bot'])
        self.assertFalse(any(a.get('bot_access') for a in before['accounts']))
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        client = TestClient(api.app)
        token = self.payload()['bot_token']
        with patch.object(control.subprocess, 'Popen') as launch:
            process = launch.return_value.__enter__.return_value
            process.communicate.return_value = (json.dumps({'ok': True, 'result': {'username': 'FixtureBot'}}), '')
            process.returncode = 0
            checked = client.post('/api/assistant/check/nodera-bot', json={'bot_token': token})
            self.assertEqual(checked.status_code, 200, checked.text)
            self.assertEqual(checked.json()['result']['username'], 'FixtureBot')
            self.assertNotIn(token, checked.text)
            supplied = json.loads(process.communicate.call_args.args[0])
            self.assertEqual(supplied['settings']['bot_token'], token)
            self.assertTrue(supplied['settings']['modules']['nodera-bot'])
            self.assertNotIn(token, str(launch.call_args))
        self.assertEqual(control.read(api), before)
        self.assertEqual(client.post('/api/assistant/check/ai-agent', json={'bot_token': token}).status_code, 422)
        with patch.object(control.subprocess, 'Popen') as launch:
            self.assertEqual(client.post('/api/assistant/check/nodera-bot', json={}).status_code, 422)
            launch.assert_not_called()

    def test_reassigning_id_does_not_carry_another_users_session(self):
        status = self.save()
        payload = self.payload(); payload['expected_revision'] = status['settings']['revision']
        payload['accounts'][0].update(telegram_id=22, session=None)
        self.save(payload)
        self.assertEqual(control.read(api)['accounts'][0]['session'], '')

    def test_invalid_scope_and_private_or_credential_provider_urls_are_rejected(self):
        for url in ('http://provider.example', 'https://127.0.0.1/v1', 'https://[::1]/v1',
                    'https://localhost/v1', 'https://user:secret@provider.example', 'https://provider.example:8000'):
            payload = self.payload(); payload['provider_url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                control.Settings.model_validate(payload)
        payload = self.payload(); payload['mcp']['shell'] = True
        with self.assertRaises(ValueError): control.Settings.model_validate(payload)
        payload = self.payload(); payload['accounts'] *= 2
        with self.assertRaises(ValueError): control.Settings.model_validate(payload)

    def test_running_bot_requires_stop_before_credential_snapshot_changes(self):
        self.observe.return_value['active'] = True
        payload = self.payload()
        payload['component'] = 'nodera-bot'
        with self.assertRaises(api.HTTPException) as caught: self.save(payload)
        self.assertEqual(caught.exception.status_code, 409)

    def test_agent_can_be_configured_without_stopping_or_changing_running_bot(self):
        payload = self.payload()
        payload['component'] = 'nodera-bot'
        payload['modules']['nodera-bot'] = True
        saved = self.save(payload)
        before = control.read(api)
        snapshot = control.directory(api) / 'nodera-bot/runtime.json'
        snapshot.write_text('running-bot-credential-snapshot')
        self.observe.return_value['active'] = True
        payload = self.payload()
        payload['expected_revision'] = saved['settings']['revision']
        payload['accounts'] = []
        payload['bot_token'] = ''
        with patch.object(worker, 'checked') as service_command:
            self.save(payload)
        current = control.read(api)
        self.assertTrue(current['modules']['ai-agent'])
        self.assertEqual(current['model'], 'configured-model')
        self.assertEqual(current['api_key'], 'secret-ai')
        self.assertTrue(current['modules']['nodera-bot'])
        self.assertEqual(current['bot_token'], before['bot_token'])
        self.assertEqual(current['control_token'], before['control_token'])
        self.assertTrue(current['accounts'][0]['bot_access'])
        self.assertEqual(snapshot.read_text(), 'running-bot-credential-snapshot')
        service_command.assert_not_called()

    def test_agent_setup_does_not_require_bot_service_observation(self):
        self.observe.side_effect = lambda unit: ({'active': False, 'unit_present': None, 'state': 'unknown'}
            if unit == control.BOT_UNIT else {'active': True, 'unit_present': True, 'state': 'active'})
        self.save()
        self.assertTrue(control.read(api)['modules']['ai-agent'])

    def test_mcp_modules_add_remove_and_enable_preserve_account_sessions(self):
        payload = self.payload()
        saved = self.save(payload)
        before = control.read(api)['accounts'][0]['session']
        payload.update(expected_revision=saved['settings']['revision'], mcp_installed=['telegram-mcp'])
        payload['mcp']['vps-controller-mcp'] = False
        saved = self.save(payload)
        self.assertFalse(next(row for row in saved['mcp_modules'] if row['id'] == 'vps-controller-mcp')['installed'])
        payload['mcp']['vps-controller-mcp'] = True
        with self.assertRaises(ValueError): control.Settings.model_validate(payload)
        payload.update(expected_revision=saved['settings']['revision'], mcp_installed=[])
        payload['mcp'] = {key: False for key in control.MCP_MODULES}
        self.save(payload)
        self.assertEqual(control.read(api)['accounts'][0]['session'], before)
        self.assertFalse(any(control.read(api)['mcp'].values()))

    def test_old_runtime_rejects_files_instead_of_silently_ignoring_them(self):
        self.save()
        self.observe.return_value['active'] = True
        with self.assertRaises(api.HTTPException) as caught:
            control.runtime_request(api, '--chat', {'prompt': 'Read', 'attachments': [{'name': 'a.txt', 'mime': 'text/plain', 'data': 'dGV4dA=='}]})
        self.assertEqual(caught.exception.status_code, 409)

    def test_persona_and_cache_settings_persist_and_legacy_clients_preserve_them(self):
        payload = self.payload()
        payload.update(assistant_style='friendly', assistant_instructions='Use exact units', memory_enabled=True, memory_days=7)
        saved = self.save(payload)
        self.assertEqual(saved['settings']['assistant_style'], 'friendly')
        self.assertEqual(control.read(api)['memory_days'], 7)
        legacy = self.payload(); legacy['expected_revision'] = saved['settings']['revision']
        self.save(legacy)
        self.assertEqual(control.read(api)['assistant_instructions'], 'Use exact units')
        self.assertEqual(control.read(api)['memory_days'], 7)

    def test_catalog_uses_draft_or_saved_key_without_saving_or_leaking_it_to_another_provider(self):
        self.save()
        before = control.read(api)
        with patch.object(control.assistant_provider, 'catalog', return_value=['model-a', 'model-b']) as discover:
            result = control.model_catalog(api, control.Catalog(provider_url='https://provider.example/v1'))
            self.assertEqual(result, {'models': ['model-a', 'model-b']})
            discover.assert_called_once_with('https://provider.example/v1', 'secret-ai')
            with self.assertRaises(api.HTTPException) as caught:
                control.model_catalog(api, control.Catalog(provider_url='https://another.example'))
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(discover.call_count, 1)
            control.model_catalog(api, control.Catalog(provider_url='https://api.nyxos.shop/', api_key='draft-secret'))
            discover.assert_called_with('https://api.nyxos.shop/v1', 'draft-secret')
        self.assertEqual(control.read(api), before)

    def test_catalog_key_errors_are_sanitized_and_route_requires_admin(self):
        self.save()
        with patch.object(control.assistant_provider, 'catalog', side_effect=RuntimeError('secret-ai')):
            with self.assertRaises(api.HTTPException) as caught:
                control.model_catalog(api, control.Catalog(provider_url='https://provider.example/v1'))
        self.assertNotIn('secret-ai', caught.exception.detail)
        self.assertEqual(caught.exception.status_code, 502)
        with TestClient(api.app) as client:
            self.assertEqual(client.post('/api/assistant/models', json={'provider_url': 'https://api.nyxos.shop'}).status_code, 401)

    def test_catalog_chat_selection_is_request_scoped_and_single_mode_rejects_override(self):
        payload = self.payload()
        saved = self.save(payload)
        with self.assertRaises(api.HTTPException) as caught:
            control.runtime_request(api, '--chat', {'prompt': 'hello', 'actor': 'panel', 'model': 'other-model'})
        self.assertEqual(caught.exception.status_code, 409)
        payload.update(model_mode='catalog', expected_revision=saved['settings']['revision'])
        payload['model'] = ''
        configured = self.save(payload)
        self.assertTrue(next(m for m in configured['modules'] if m['id'] == 'ai-agent')['configured'])
        self.assertEqual(configured['settings']['model'], '')
        self.observe.return_value['active'] = True
        before = control.read(api)
        with self.assertRaises(api.HTTPException) as caught:
            control.runtime_request(api, '--chat', {'prompt': 'hello', 'actor': 'panel'})
        self.assertEqual(caught.exception.status_code, 409)
        with patch.object(control.assistant_provider, 'catalog', return_value=['a', 'b']):
            checked = control.runtime_request(api, '--check', {'module': 'ai-agent', 'actor': 'panel'})
        self.assertEqual(checked['result']['models_available'], 2)
        with patch.object(control.subprocess, 'Popen') as process_factory:
            process = process_factory.return_value.__enter__.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps({'ok': True, 'result': {'answer': 'OK'}}), '')
            control.runtime_request(api, '--chat', {'prompt': 'hello', 'actor': 'panel', 'model': 'other-model'})
            sent = json.loads(process.communicate.call_args.args[0])
            self.assertEqual(sent['settings']['model'], 'other-model')
            self.assertEqual(sent['settings']['provider_url'], 'https://provider.example/v1')
        self.assertEqual(control.read(api), before)

    def test_control_token_has_per_user_scope_and_revocation(self):
        self.save()
        value = control.read(api)
        token = 'Bearer ' + value['control_token']
        with self.assertRaises(api.HTTPException) as caught: control.authorize_control(api, token, '11')
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(api.HTTPException): control.authorize_control(api, token, '22')
        with self.assertRaises(api.HTTPException): control.authorize_control(api, 'Bearer wrong', 'panel')
        control.authorize_control(api, token, 'panel')
        value['mcp']['vps-controller-mcp'] = False
        control.store(api, value)
        with self.assertRaises(api.HTTPException): control.authorize_control(api, token, 'panel')

    def test_routes_require_admin_or_dedicated_service_token(self):
        self.save()
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/assistant').status_code, 401)
        self.assertEqual(client.post('/api/assistant/chat', json={'prompt': 'test'}).status_code, 401)
        self.assertEqual(client.post('/api/assistant/control-tool', json={'name': 'vps_overview'}).status_code, 401)
        token = control.read(api)['control_token']
        headers = {'Authorization': 'Bearer ' + token, 'X-MCP-Actor': 'panel'}
        with patch.object(api, 'overview', return_value={'server': {'name': 'test'}, 'resources': {'cpu_percent': 1}}):
            self.assertEqual(client.post('/api/assistant/control-tool', headers=headers, json={'name': 'vps_overview'}).json()['server']['name'], 'test')
        self.assertEqual(client.post('/api/assistant/control-tool', headers=headers, json={'name': 'shell'}).status_code, 422)

    def test_components_projection_excludes_connection_credentials(self):
        with patch.object(api, 'protocol_images', return_value={'items': [{'id': 'mihomo', 'name': 'Mihomo',
            'installed': True, 'private_key': 'never-send', 'password': 'never-send'}]}):
            result = control.control_tool(api, 'vps_components')
        self.assertNotIn('never-send', json.dumps(result))

    def test_metrics_tool_returns_bounded_resource_points_only(self):
        points = [{'at': i, 'cpu_percent': 15, 'internal': 'hidden'} for i in range(80)]
        with patch.object(api, 'get_metrics_history', return_value={'resolution_s': 60, 'points': points}) as history:
            result = control.control_tool(api, 'vps_metrics')
        history.assert_called_once_with('hour', None)
        self.assertEqual(len(result['points']), 60)
        self.assertEqual(result['points'][0]['at'], 20)
        self.assertNotIn('hidden', json.dumps(result))

    def test_corrupted_config_is_not_treated_as_new_installation(self):
        control.directory(api).mkdir()
        (control.directory(api) / 'settings.json').write_text('{broken')
        with self.assertRaises(control.operations.OperationConflict): control.status(api)

    def test_bot_settings_cannot_overwrite_agent_key_and_purges_are_separate(self):
        saved = self.save()
        payload = self.payload()
        payload.update(component='nodera-bot', expected_revision=saved['settings']['revision'], api_key='must-be-ignored')
        payload['accounts'][0]['bot_access'] = True
        self.save(payload)
        self.assertEqual(control.read(api)['api_key'], 'secret-ai')
        self.assertTrue(control.read(api)['accounts'][0]['bot_access'])
        import shutil
        shutil.rmtree(control.directory(api) / 'nodera-bot')
        self.assertFalse(control.read(api).get('bot_token'))
        self.assertEqual(control.read(api)['api_key'], 'secret-ai')
        self.assertFalse(control.read(api)['accounts'][0]['bot_access'])

    def test_failed_generation_commit_preserves_previous_credentials(self):
        saved = self.save()
        payload = self.payload(); payload.update(expected_revision=saved['settings']['revision'], api_key='new-key')
        original = control.operations.atomic_json
        def fail_pointer(path, value, **kwargs):
            if path.name == 'settings.json': raise OSError('disk failure')
            return original(path, value, **kwargs)
        with patch.object(control.operations, 'atomic_json', side_effect=fail_pointer):
            with self.assertRaises(OSError): self.save(payload)
        self.assertEqual(control.read(api)['api_key'], 'secret-ai')
        self.assertEqual(control.read(api)['revision'], saved['settings']['revision'])

    def test_removed_component_cannot_run_even_with_retained_keys(self):
        self.save()
        control.ComponentRegistry(self.root).write_receipt('ai-agent', 'installed', retained=True)
        with self.assertRaises(api.HTTPException) as caught:
            control.runtime_request(api, '--chat', {'prompt': 'test'})
        self.assertEqual(caught.exception.status_code, 409)

    def test_unauthenticated_service_requests_do_not_read_or_create_private_storage(self):
        with patch.object(control, 'read', side_effect=AssertionError('Unauthenticated storage access')):
            for authorize in (control.authorize_control, control.authorize_bot):
                with self.assertRaises(api.HTTPException) as caught:
                    authorize(api, None, '')
                self.assertEqual(caught.exception.status_code, 401)

    def test_bot_image_starts_only_with_enabled_saved_configuration(self):
        project = Path(__file__).resolve().parents[2]
        for configured in (False, True):
            saved = self.payload()
            saved['modules']['nodera-bot'] = configured
            with self.subTest(configured=configured), patch.object(api, 'INSTALL_DIR', project), \
                 patch.object(worker, 'assert_unit_owner', return_value=self.root / 'nodera.service'), \
                 patch.object(worker, 'require_idle'), patch.object(worker, 'checked'), \
                 patch.object(control, 'migrate_settings'), patch.object(control, 'read', return_value=saved), \
                 patch.object(worker, 'perform') as start:
                worker.install(api, 'nodera-bot')
                if configured: start.assert_called_once_with(api, 'start', restoring=True)
                else: start.assert_not_called()
                self.assertTrue((control.runtime_directory('nodera-bot') / 'version').is_file())


class AgentRuntimeTests(unittest.TestCase):
    def test_mcp_scope_is_bound_to_selected_user_and_panel_has_no_telegram_session(self):
        settings = {'mcp': {'telegram-mcp': True, 'vps-controller-mcp': True}, 'accounts': [
            {'telegram_id': 11, 'telegram_access': True, 'session': 'session', 'vps_access': False}]}
        self.assertTrue(runner.allowed(settings, '11', 'telegram-mcp'))
        self.assertFalse(runner.allowed(settings, 'panel', 'telegram-mcp'))
        self.assertFalse(runner.allowed(settings, '22', 'telegram-mcp'))
        self.assertFalse(runner.allowed(settings, '11', 'vps-controller-mcp'))
        self.assertTrue(runner.allowed(settings, 'panel', 'vps-controller-mcp'))

    def test_tool_loop_uses_mcp_result_and_cannot_execute_unregistered_tools(self):
        session = types.SimpleNamespace(call_tool=AsyncMock(return_value=types.SimpleNamespace(
            content=[types.SimpleNamespace(type='text', text='{"cpu_percent":12}')])) )
        tool = types.SimpleNamespace(name='vps_overview', description='Read metrics', inputSchema={'type': 'object'})
        @asynccontextmanager
        async def connections(*_): yield {'vps_overview': (session, tool)}
        messages_seen = []
        async def provider(settings, messages, tools):
            messages_seen.append(json.loads(json.dumps(messages)))
            if len(messages_seen) == 1:
                return {'tool_calls': [
                    {'id': 'one', 'type': 'function', 'function': {'name': 'vps_overview', 'arguments': '{}'}},
                    {'id': 'two', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{"cmd":"whoami"}'}}]}
            return {'content': 'CPU: 12%'}
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example', 'model': 'test', 'api_key': 'secret'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', 'Read CPU'))
        self.assertEqual(result['tools_used'], ['vps_overview'])
        session.call_tool.assert_awaited_once_with('vps_overview', {})
        self.assertIn('cpu_percent', messages_seen[1][3]['content'])
        self.assertIn('error', messages_seen[1][4]['content'])

    def test_errors_do_not_disclose_bot_tokens_or_telegram_session_contents(self):
        for exc in (RuntimeError('https://api.telegram.org/botSECRET/getUpdates'),
                    ValueError('session=SECRET'), PermissionError('SECRET')):
            self.assertNotIn('SECRET', runner.safe_error(exc))

    def test_private_dns_answers_cannot_receive_provider_key(self):
        async def check():
            loop = asyncio.get_running_loop()
            with patch.object(loop, 'getaddrinfo', new=AsyncMock(return_value=[(2, 1, 6, '', ('127.0.0.1', 443))])):
                with self.assertRaises(ValueError):
                    await runner.provider_request({'provider_url': 'https://provider.example/v1', 'api_key': 'secret'}, [], [])
        asyncio.run(check())
