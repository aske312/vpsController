from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from tests.api.support import api
from tests.api.test_assistant_control import runner
import application_operation as operations
import assistant_access_notifications as notices
import assistant_control as control
import assistant_memory as memory
import assistant_personalization as personalization
import assistant_roles as roles
import assistant_jobs as jobs
import assistant_bot_history as bot_history


def settings(role='administrator'):
    return {'revision': 'a' * 32, 'memory_enabled': True, 'memory_days': 30,
            'accounts': [{'telegram_id': 11, 'role': role, 'products': ['assistant', 'mihomo'],
                          'mihomo_profile_id': 'own', 'permission_overrides': {}}],
            'modules': {}, 'mcp': {'vps-controller-mcp': True, 'mihomo-mcp': True}}


class AssistantPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)

    def test_non_admin_template_and_overrides_cannot_grant_infrastructure_or_other_profiles(self):
        for role in ('moderator', 'user', 'guest'):
            value = settings(role)
            value['role_permissions'] = {role: list(roles.PERMISSIONS)}
            value['accounts'][0]['permission_overrides'] = dict.fromkeys(roles.PERMISSIONS, True)
            self.assertFalse(roles.effective(value, '11') & (roles.SERVER_PERMISSIONS | {'subscriptions_admin', 'modules_manage', 'routing_manage', 'dns_manage', 'recovery'}))
            self.assertFalse(roles.privileged(value, '11'))
            with patch.object(control, 'read', return_value=value):
                for name in ('developer_inspect', 'vps_overview', 'vps_terminal', 'application_diagnostics', 'vps_logs'):
                    with self.assertRaises(HTTPException) as error: control.control_tool(self.env, name, actor='11')
                    self.assertEqual(error.exception.status_code, 403)

    def test_public_persona_has_no_hostname_admin_instructions_or_runbook(self):
        value = settings('user')
        value.update(server_name='PRIVATE_HOST', assistant_instructions='PRIVATE_ADMIN_INSTRUCTION',
                     interlocutor={'role': 'owner', 'products': ['mihomo'], 'name': 'User'})
        prompt = runner.persona(value, '11')
        for secret in ('PRIVATE_HOST', 'PRIVATE_ADMIN_INSTRUCTION', 'operation_timeline', 'Mihomo', 'apt-daily', 'developer_inspect'):
            self.assertNotIn(secret, prompt)
        self.assertFalse(runner.allowed(value, '11', 'vps-controller-mcp'))
        value['accounts'][0]['role'] = 'administrator'
        value['accounts'][0]['effective_permissions'] = list(roles.PERMISSIONS)
        self.assertTrue(runner.allowed(value, '11', 'vps-controller-mcp'))
        self.assertIn('PRIVATE_HOST', runner.persona(value, '11'))
        self.assertIn('PRIVATE_ADMIN_INSTRUCTION', runner.persona(value, '11'))

    def test_downgrade_excludes_admin_dialogue_personal_facts_and_recent_topics(self):
        old = settings()
        identity = 'b' * 32
        memory.append(self.env, identity, '11', 'bot', 'PRIVATE_CONFIG_QUESTION', [],
                      {'answer': 'PRIVATE_SERVER_CONFIGURATION'}, 'model', old)
        operations.atomic_json(personalization.path(self.env, '11'),
            {'access_scope': roles.context_scope(old, '11'),
             'facts': [{'id': 'c' * 16, 'text': 'PRIVATE_SERVER_FACT', 'at': 2000000000}]})
        current = settings('user')
        self.assertEqual(memory.get(self.env, identity, '11', 'bot', settings=current)['turns'], [])
        self.assertEqual(memory.listing(self.env, current, '11', 'bot')['items'], [])
        personal = personalization.snapshot(self.env, current, '11')
        self.assertEqual(personal['facts'], [])
        self.assertEqual(personal['recent_topics'], [])
        memory.append(self.env, identity, '11', 'bot', 'New question', [], {'answer': 'New answer'}, 'model', current)
        self.assertNotIn('PRIVATE_', str(memory.get(self.env, identity, '11', 'bot', settings=current)))

    def test_scope_changes_on_authority_or_profile_but_not_provider_or_display_name(self):
        value = settings()
        before = roles.context_scope(value, '11')
        value.update(model='other', api_key='secret')
        value['accounts'][0]['name'] = 'New name'
        self.assertEqual(before, roles.context_scope(value, '11'))
        value['accounts'][0]['mihomo_profile_id'] = 'another'
        self.assertNotEqual(before, roles.context_scope(value, '11'))
        self.assertFalse(roles.context_matches({}, settings('user'), '11'))
        legacy = {'accounts': [{'telegram_id': 11, 'vps_access': True, 'bot_chat_access': True}]}
        migrated = {**legacy, 'accounts': [roles.migrated(legacy, legacy['accounts'][0])]}
        self.assertEqual(roles.context_scope(legacy, '11'), roles.context_scope(migrated, '11'))

    def test_results_from_old_authority_are_not_readable_and_workers_are_stopped(self):
        old, current = settings(), settings('user')
        row = {'id': 'd' * 32, 'actor': '11', 'state': 'running', 'stage': 'generating',
               'partial': 'PRIVATE_SERVER_DATA', 'access_scope': roles.context_scope(old, '11'),
               'created_at': 2000000000, 'updated_at': 2000000000, 'seq': 1}
        operations.atomic_json(jobs.path(self.env, row['id']), row)
        with patch.object(control, 'read', return_value=current), patch.object(control, '_read', return_value=current), \
             patch.object(jobs, 'stop_worker') as stop, patch.object(jobs, 'dispatch'), \
             patch.object(jobs, 'reconcile', side_effect=lambda api, row: row):
            with self.assertRaises(HTTPException): jobs.get(self.env, row['id'], '11')
            with self.assertRaises(HTTPException): jobs.cancel(self.env, row['id'], '11')
            with self.assertRaises(HTTPException):
                jobs.submit(self.env, control.Chat(prompt='Replay', request_id=row['id']), actor='11')
            jobs.recover(self.env)
            stop.assert_called_once_with(row['id'])
        stored = operations.read(jobs.path(self.env, row['id']))
        self.assertTrue(stored['cancel_requested'])
        self.assertEqual(stored['partial'], '')

    def test_telegram_reply_cannot_reintroduce_a_pre_downgrade_answer(self):
        import time
        old, current = settings(), settings('user')
        old['bot_token'] = current['bot_token'] = '123456:fixture'
        now = int(time.time())
        with patch.object(control, '_read', return_value=old):
            bot_history.append(self.env, old, '11', bot_history.Message(
                message_id=1, role='assistant', text='PRIVATE_SERVER_DATA', date=now))
        with patch.object(control, '_read', return_value=current):
            bot_history.append(self.env, current, '11', bot_history.Message(message_id=2, role='user',
                text='Explain this', date=now, reply_to_message_id=1, reply_to_text='PRIVATE_SERVER_DATA'))
            result = bot_history.read(self.env, current, '11', bot_history.Read(), cached=False)
        self.assertEqual(len(result['messages']), 1)
        self.assertEqual(result['messages'][0]['reply_to_text'], '')
        self.assertNotIn('PRIVATE_SERVER_DATA', str(result))

    def test_notices_only_list_current_actions_once_without_role_or_component_name(self):
        old, current = settings(), settings('user')
        text = notices.message(notices.snapshot(old, 11), notices.snapshot(current, 11))
        for hidden in ('Роль:', 'Администратор', 'Пользователь', 'Mihomo', 'Добавлены', 'Сняты'):
            self.assertNotIn(hidden, text)
        self.assertEqual(text.count('Общение с ИИ'), 1)
        self.assertNotIn('Управление службами', text)


if __name__ == '__main__': unittest.main()
