import unittest
from unittest.mock import patch
from tests.api.support import api
from tests.api import test_assistant_control as fixtures
import assistant_control as control
import assistant_commands as commands
import assistant_roles as roles
import assistant_role_actions as actions


class AssistantRoleTests(unittest.TestCase):
    setUp = fixtures.AssistantTests.setUp
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def test_explicit_role_is_authoritative_and_owner_rights_cannot_be_delegated(self):
        settings = {'accounts': [{'telegram_id': 11, 'role': 'guest', 'bot_access': True, 'vps_access': True}]}
        self.assertEqual(roles.effective(settings, '11'), {'chat', 'web'})
        self.assertEqual(roles.effective(settings, 'unknown'), set())
        self.assertEqual(roles.effective(settings, 'panel'), set(roles.PERMISSIONS))
        for role in roles.ROLES:
            settings['accounts'][0]['role'] = role
            self.assertEqual(roles.allows(settings, '11', 'roles_manage'), role == 'owner')
        with self.assertRaises(ValueError): control.Account(telegram_id=11, role='administrator', permission_overrides={'roles_manage': True})
        payload = self.payload(); payload['role_permissions'] = {'user': ['roles_manage']}
        with self.assertRaises(ValueError): control.Settings.model_validate(payload)
        payload['role_permissions'] = {'owner': ['chat']}
        with self.assertRaises(ValueError): control.Settings.model_validate(payload)

    def test_migration_preserves_legacy_grants_and_roles_survive_restart(self):
        saved = self.save()['settings']
        old = control.read(api)
        migrated = roles.migrated(old, old['accounts'][0])
        self.assertEqual(roles.account_grants(old, migrated), roles.legacy(old['accounts'][0]))
        payload = self.payload(); payload.update(expected_revision=saved['revision'], role_permissions={'moderator': ['chat', 'server_read']})
        payload['accounts'][0].update(role='moderator', permission_overrides={'web': False})
        self.save(payload)
        fresh = control.read(api)
        self.assertEqual(fresh['accounts'][0]['role'], 'moderator')
        self.assertEqual(roles.effective(fresh, '11'), {'chat', 'server_read'})
        payload.pop('role_permissions'); payload['expected_revision'] = fresh['revision']
        self.save(payload)
        self.assertEqual(control.read(api)['role_permissions']['moderator'], ['chat', 'server_read'])

    def test_read_only_moderator_cannot_execute_or_diagnose_and_guest_cannot_view_server(self):
        payload = self.payload(); payload['accounts'][0].update(role='moderator', permission_overrides={})
        self.save(payload)
        with self.assertRaises(api.HTTPException): control.control_tool(api, 'vps_service_action', {'service': 'api', 'action': 'restart'}, '11')
        with self.assertRaises(api.HTTPException): control.control_tool(api, 'vps_diagnostics', actor='11')
        with self.assertRaises(api.HTTPException): control.control_tool(api, 'vps_access', actor='11')
        fresh = control.read(api); payload['expected_revision'] = fresh['revision']; payload['accounts'][0]['role'] = 'guest'; self.save(payload)
        with self.assertRaises(api.HTTPException): control.control_tool(api, 'vps_overview', actor='11')
        with self.assertRaises(api.HTTPException): control.bot_models(api, '11')

    def test_owner_role_change_needs_confirmation_and_cannot_be_confirmed_by_another_actor(self):
        payload = self.payload(); payload['accounts'][0]['role'] = 'owner'; self.save(payload)
        result = actions.call(api, '11', {'method': 'PATCH', 'body': {'telegram_id': 22, 'role': 'user'}})
        self.assertTrue(result['confirmation_required'])
        self.assertFalse(any(row['telegram_id'] == 22 for row in control.read(api)['accounts']))
        record = control.operations.read(commands.pending_path(api, '11'))
        with self.assertRaises(api.HTTPException): commands.execute(api, '22', '/confirm ' + record['code'])
        text = commands.execute(api, '11', '/confirm ' + record['code'])
        self.assertIn('обновлены', text)
        self.assertEqual(next(row['role'] for row in control.read(api)['accounts'] if row['telegram_id'] == 22), 'user')
        self.assertEqual(commands.execute(api, '11', '/confirm ' + record['code']), text)
        self.assertEqual(api.get_application_operation(result['operation_id'], None)['state'], 'succeeded')

    def test_revoked_permission_blocks_prepared_command_before_side_effect(self):
        payload = self.payload(); payload['accounts'][0]['role'] = 'owner'; self.save(payload)
        actions.call(api, '11', {'method': 'PATCH', 'body': {'telegram_id': 22, 'role': 'administrator'}})
        record = control.operations.read(commands.pending_path(api, '11'))
        payload['expected_revision'] = control.read(api)['revision']; payload['accounts'][0]['role'] = 'user'; self.save(payload)
        with patch.object(control, 'configure') as configure:
            with self.assertRaises(api.HTTPException): commands.execute(api, '11', '/confirm ' + record['code'])
            configure.assert_not_called()
        self.assertFalse(any(row['telegram_id'] == 22 for row in control.read(api)['accounts']))

    def test_guest_chat_uses_default_model_without_persistent_memory(self):
        payload = self.payload(); payload['accounts'][0]['role'] = 'guest'; self.save(payload)
        with patch.object(control, 'runtime_request', return_value={'ok': True, 'result': {'answer': 'Hello', 'tools_used': []}}):
            result = control.chat_request(api, control.Chat(prompt='Hello'), '11', 'bot')
        self.assertNotIn('conversation_id', result)
        import assistant_memory
        self.assertEqual(list(assistant_memory.folder(api, '11', 'bot').glob('*.json')), [])
        with self.assertRaises(api.HTTPException): control.chat_request(api, control.Chat(prompt='Hi', model='another-model'), '11', 'bot')
