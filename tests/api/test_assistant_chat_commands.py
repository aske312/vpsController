"""Web shortcuts reuse the bot executor without an AI request or weakened ACLs."""
import unittest
from unittest.mock import patch

from tests.api import test_assistant_commands as fixtures
from tests.api.support import api
import assistant_commands as commands
import assistant_control as control
import assistant_mihomo as mihomo


class ChatCommandTests(unittest.TestCase):
    payload = fixtures.ServerCommandTests.payload
    save = fixtures.ServerCommandTests.save
    enabled_bot = fixtures.ServerCommandTests.enabled_bot
    approve = fixtures.ServerCommandTests.approve
    setUp = fixtures.ServerCommandTests.setUp

    def command(self, text, actor=None, **fields):
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
            return self.client.post('/api/assistant/commands', json={'command': text, 'telegram_id': actor, **fields})

    def catalog(self, actor=None):
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
            return self.client.get('/api/assistant/commands' + (f'?telegram_id={actor}' if actor else ''))

    def test_authentication_precedes_invalid_command_body(self):
        for method, url, body in [('get', '/api/assistant/commands', None), ('post', '/api/assistant/commands', {}),
                                  ('post', '/api/assistant/commands', {'command': '/status'})]:
            response = self.client.request(method, url, json=body)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers['www-authenticate'], 'Basic')

    def test_catalog_is_filtered_by_live_permissions_and_execution_rechecks_them(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['assistant', 'mihomo'], mihomo_profile_id='own', permission_overrides={})
        control.store(api, settings)
        items = {row['command']: row for row in self.catalog(11).json()['items']}
        self.assertIn('/profile', items)
        self.assertIn('/model', items)
        self.assertFalse(items['/profile']['needs_args'])
        self.assertNotIn('/status', items)
        self.assertNotIn('/service', items)
        with patch.object(api, 'overview') as read:
            self.assertEqual(self.command('/status', 11).status_code, 403)
            read.assert_not_called()
        self.assertTrue(next(row for row in self.catalog().json()['items'] if row['command'] == '/profile')['needs_args'])

    def test_status_runs_the_same_read_executor_without_a_model_or_bot(self):
        settings = control.read(api)
        settings['modules'] = {'ai-agent': False, 'nodera-bot': False}
        control.store(api, settings)
        with patch.object(api, 'overview', return_value={'server': {'name': 'Direct web command'}, 'resources': {'uptime_s': 90}}), patch.object(control, 'runtime_request') as model:
            response = self.command('/status')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('Direct web command', response.json()['text'])
        self.assertIn('**Сервер**', response.json()['display_text'])
        self.assertIn('`Direct web command`', response.json()['display_text'])
        self.assertTrue(response.json()['context_safe'])
        model.assert_not_called()

    def test_service_commands_prepare_multiple_actions_and_confirm_through_the_queue(self):
        settings = control.read(api)
        settings['modules']['ai-agent'] = False
        settings['mcp']['vps-controller-mcp'] = False
        control.store(api, settings)
        with patch.object(api, 'manage_service', side_effect=lambda _service, _payload, _auth, identity: {'id': identity, 'state': 'queued'}) as action, patch.object(control, 'runtime_request') as model:
            first = self.command('/service fixture restart')
            second = self.command('/service fixture start')
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            rows = second.json()['pending_actions']
            self.assertEqual(len(rows), 2)
            action.assert_not_called()
            confirmed = self.command('/confirm ' + rows[0]['code'])
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(len(confirmed.json()['pending_actions']), 1)
            action.assert_called_once()
            model.assert_not_called()

    def test_subscription_link_is_scoped_and_is_marked_private_for_ai_history(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['mihomo'], mihomo_profile_id='own', permission_overrides={})
        control.store(api, settings)
        with patch.object(mihomo, 'request', return_value={'url': 'https://example.test/s/private-token'}) as read:
            response = self.command('/subscription own', 11)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('private-token', response.json()['text'])
            self.assertFalse(response.json()['context_safe'])
            read.reset_mock()
            self.assertEqual(self.command('/subscription other', 11).status_code, 403)
            read.assert_not_called()

    def test_web_model_selection_does_not_change_telegram_preference(self):
        settings = control.read(api)
        settings.update(model_mode='catalog', model='', provider_url='https://models.example.test/v1', api_key='fixture-key')
        control.store(api, settings)
        preference = control.bot_model_path(api, '11')
        control.operations.atomic_json(preference, {'model': 'bot-model'})
        with patch.object(control, 'model_catalog', return_value={'models': ['web-model']}), patch.object(control, 'runtime_request') as model:
            response = self.command('/model web-model', 11)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['model'], 'web-model')
            self.assertEqual(self.command('/model invalid', 11).status_code, 409)
            self.assertIn('web-model', self.command('/model', 11, selected_model='web-model').json()['text'])
            self.assertEqual(self.command('/model default', 11).json()['model'], '')
            model.assert_not_called()
        self.assertEqual(control.operations.read(preference), {'model': 'bot-model'})

    def test_help_and_id_do_not_leak_owner_commands_into_an_unprivileged_context(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='guest', products=[], permission_overrides={})
        control.store(api, settings)
        response = self.command('/help', 11)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('/status', response.json()['text'])
        self.assertNotIn('/service', response.json()['text'])
        self.assertFalse(self.command('/id', 11).json()['context_safe'])
        self.assertIn('11', self.command('/id', 11).json()['text'])

    def test_unknown_blocked_and_malformed_commands_never_invoke_ai_or_a_mutation(self):
        with patch.object(control, 'runtime_request') as model, patch.object(api, 'manage_service') as mutate:
            for text in ('/shell rm -rf /', '/unknown', '/status extra', '/service', '/models bad', '/models ²', '/operation bad', '/ai question'):
                with self.subTest(command=text): self.assertEqual(self.command(text).status_code, 422)
            self.assertEqual(self.command('/shutdown').status_code, 403)
            self.assertEqual(self.command('/service ssh stop').status_code, 409)
            model.assert_not_called(); mutate.assert_not_called()

    def test_cancel_without_a_code_never_cancels_a_different_pending_action(self):
        self.command('/service fixture restart'); self.command('/service fixture start')
        self.assertEqual(self.command('/cancel').status_code, 409)
        rows = commands.pending_actions(api, 'panel')
        response = self.command('/cancel ' + rows[0]['code'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['code'] for row in response.json()['pending_actions']], [rows[1]['code']])
