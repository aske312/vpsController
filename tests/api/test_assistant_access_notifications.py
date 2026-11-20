import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
from tests.api import test_assistant_control as fixtures
import application_operation as operations
import assistant_access_notifications as notices
import assistant_contacts as contacts
import assistant_control as control
import assistant_roles as roles
import assistant_role_actions as actions
import assistant_commands as commands
import assistant_worker as worker
import telegram_alerts


def settings(role='administrator'):
    return {'revision': 'a' * 32, 'bot_token': '123456:PRIVATE_BOT_TOKEN',
            'modules': {'nodera-bot': True, 'ai-agent': False},
            'accounts': [{'telegram_id': 11, 'role': role, 'products': ['assistant', 'mihomo'],
                          'permission_overrides': {}}]}


class AccessNoticeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root)
        self.current = settings()
        self.now = 1800000000
        self.enterContext(patch.object(control, '_read', side_effect=lambda _: self.current))
        self.known = self.enterContext(patch.object(contacts, 'observed', return_value=[{'telegram_id': 11}]))
        self.profile = self.enterContext(patch.object(contacts, 'profile', return_value={}))
        self.sender = self.enterContext(patch.object(telegram_alerts, 'send', return_value={'state': 'sent', 'message_id': 1}))
        self.enterContext(patch.object(notices.time, 'time', side_effect=lambda: self.now))

    def change(self, after):
        with operations.locked(self.root): result = notices.enqueue_changes(self.env, self.current, after)
        self.current = after
        return result

    def test_administrator_to_guest_is_delivered_without_chat_or_admin_permissions(self):
        self.change(settings('guest'))
        with patch.object(roles, 'require', side_effect=AssertionError('Delivery must not require revoked rights')):
            notices.deliver(self.env)
        self.assertEqual(self.sender.call_args.args[:2], (self.current['bot_token'], 11))
        text = self.sender.call_args.args[2]
        self.assertIn('Администратор → Гость', text)
        self.assertIn('Сняты разрешения', text)
        self.assertIn('чат ИИ и управление подписками недоступны', text)
        self.assertIn('/myprofile', text)
        self.assertNotIn('Теперь вам разрешено', text)
        notices.deliver(self.env)
        self.sender.assert_called_once()

    def test_granting_rights_lists_actual_new_capabilities(self):
        self.current = settings('guest')
        self.change(settings('moderator'))
        notices.deliver(self.env)
        text = self.sender.call_args.args[2]
        self.assertIn('Гость → Модератор', text)
        self.assertIn('Добавлены разрешения', text)
        self.assertIn('Общение с ИИ', text)
        self.assertIn('Просмотр сервера и служб', text)
        self.assertNotIn('Управление службами', text)

    def test_role_policy_changes_notify_only_users_whose_effective_rights_changed(self):
        self.current = settings('moderator')
        self.current['role_permissions'] = {'moderator': ['chat', 'server_read']}
        self.current['accounts'].append({'telegram_id': 22, 'role': 'moderator', 'products': ['assistant'],
                                         'permission_overrides': {'chat': False}})
        after = copy.deepcopy(self.current)
        after['role_permissions']['moderator'] = ['server_read']
        self.assertEqual(self.change(after)['queued'], 1)
        notices.deliver(self.env)
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.args[1], 11)
        self.assertIn('Сняты разрешения:\n• Общение с ИИ', self.sender.call_args.args[2])

    def test_individual_revocation_and_account_removal_are_not_hidden_by_the_new_ACL(self):
        self.current = settings('user')
        after = copy.deepcopy(self.current)
        after['accounts'][0]['permission_overrides']['chat'] = False
        self.change(after)
        notices.deliver(self.env)
        self.assertIn('Сняты разрешения:\n• Общение с ИИ', self.sender.call_args.args[2])
        self.now += 5
        self.change({**copy.deepcopy(self.current), 'accounts': []})
        notices.deliver(self.env)
        self.assertIn('Пользователь → Гость', self.sender.call_args.args[2])

    def test_unchanged_saves_are_silent_and_pending_changes_are_coalesced(self):
        unrelated = {**copy.deepcopy(self.current), 'model': 'another-model', 'api_key': 'PRIVATE_AI_KEY'}
        unrelated['accounts'][0]['name'] = 'Another name'
        self.assertEqual(self.change(unrelated)['queued'], 0)
        self.assertFalse(notices.path(self.env).exists())
        self.change(settings('moderator'))
        self.change(settings('guest'))
        self.assertEqual(len(notices.load(self.env)['queue']), 1)
        notices.deliver(self.env)
        self.assertIn('Администратор → Гость', self.sender.call_args.args[2])
        self.sender.assert_called_once()

    def test_reverting_an_unsent_change_does_not_notify(self):
        self.change(settings('guest'))
        self.assertEqual(self.change(settings())['queued'], 0)
        notices.deliver(self.env)
        self.sender.assert_not_called()

    def test_notice_waits_for_first_bot_contact_and_survives_reloading_the_queue(self):
        self.known.return_value = []
        self.change(settings('guest'))
        notices.deliver(self.env)
        self.sender.assert_not_called()
        self.assertEqual(len(notices.load(self.env)['queue']), 1)
        self.known.return_value = [{'telegram_id': 11}]
        notices.deliver(self.env)
        self.sender.assert_called_once()

    def test_unknown_or_stranded_delivery_is_not_replayed(self):
        for stranded in (False, True):
            with self.subTest(stranded=stranded):
                self.current = settings()
                self.change(settings('guest'))
                if stranded:
                    value = notices.load(self.env)
                    value['queue'][0]['state'] = 'sending'
                    operations.atomic_json(notices.path(self.env), value)
                self.sender.reset_mock()
                self.sender.return_value = {'state': 'unknown'}
                self.now += 5
                notices.deliver(self.env); notices.deliver(self.env)
                self.assertEqual(self.sender.call_count, 0 if stranded else 1)
                self.assertEqual(notices.load(self.env)['queue'], [])
                self.assertEqual(notices.load(self.env)['deliveries'][-1]['state'], 'unknown')

    def test_explicit_rate_limit_retries_without_delivering_stale_rights_twice(self):
        self.change(settings('moderator'))
        def limited(*_args):
            self.change(settings('guest'))
            return {'state': 'pending', 'retry_after': 10}
        self.sender.side_effect = limited
        notices.deliver(self.env)
        self.assertEqual(len(notices.load(self.env)['queue']), 1)
        self.sender.side_effect = None
        self.sender.return_value = {'state': 'sent'}
        self.now += 9
        notices.deliver(self.env)
        self.assertEqual(self.sender.call_count, 1)
        self.now += 1
        notices.deliver(self.env)
        self.assertEqual(self.sender.call_count, 2)
        self.assertIn('Администратор → Гость', self.sender.call_args.args[2])
        self.assertEqual(notices.load(self.env)['queue'], [])

    def test_bot_disabled_defers_delivery_and_a_different_bot_never_sends_old_notices(self):
        after = settings('guest')
        after['modules']['nodera-bot'] = False
        self.change(after)
        notices.deliver(self.env)
        self.sender.assert_not_called()
        self.current['bot_token'] = '654321:OTHER_BOT'
        self.current['modules']['nodera-bot'] = True
        notices.deliver(self.env)
        self.sender.assert_not_called()
        self.assertEqual(notices.load(self.env)['queue'], [])

    def test_reentrant_delivery_does_not_treat_an_active_sender_as_a_crash(self):
        self.change(settings('guest'))
        def send(*_args):
            notices.deliver(self.env)
            self.assertEqual(notices.load(self.env)['queue'][0]['state'], 'sending')
            return {'state': 'sent'}
        self.sender.side_effect = send
        notices.deliver(self.env)
        self.sender.assert_called_once()

    def test_permission_messages_are_bounded_and_queue_contains_no_credentials(self):
        self.assertEqual(set(notices.PERMISSION_NAMES), set(roles.PERMISSIONS))
        self.current = settings('guest')
        after = settings('owner')
        after['api_key'] = 'PRIVATE_AI_KEY'
        after['accounts'][0].update(session='PRIVATE_TELEGRAM_SESSION', telegram_action_targets=['-100123456789'])
        self.change(after)
        encoded = notices.path(self.env).read_text(encoding='utf-8')
        for secret in ('PRIVATE_AI_KEY', 'PRIVATE_BOT_TOKEN', 'PRIVATE_TELEGRAM_SESSION', '-100123456789'):
            self.assertNotIn(secret, encoded)
        row = notices.load(self.env)['queue'][0]
        self.assertLessEqual(len(notices.message(row['before'], row['after'])), 4000)
        self.assertEqual(json.loads(encoded)['queue'][0]['recipient'], 11)

    def test_telegram_tool_restrictions_notify_without_changing_the_role(self):
        self.current = settings('user')
        after = copy.deepcopy(self.current)
        after['accounts'][0]['telegram_disabled_tools'] = ['tg_get_messages']
        self.assertEqual(self.change(after)['queued'], 1)
        notices.deliver(self.env)
        self.assertIn('Telegram — сняты инструменты:', self.sender.call_args.args[2])

    def test_same_bot_token_rotation_does_not_lose_known_contact_profiles(self):
        self.known.return_value = []
        self.profile.return_value = {'snapshot': {'seen_at': self.now}}
        after = settings('guest')
        after['bot_token'] = '123456:ROTATED_BOT_TOKEN'
        self.change(after)
        notices.deliver(self.env)
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.args[0], after['bot_token'])

    def test_an_administrator_created_profile_does_not_prove_the_user_started_the_bot(self):
        self.known.return_value = []
        self.profile.return_value = {'snapshot': {'seen_at': 0}}
        self.change(settings('guest'))
        notices.deliver(self.env)
        self.sender.assert_not_called()
        self.profile.return_value = {'snapshot': {'seen_at': self.now}}
        notices.deliver(self.env)
        self.sender.assert_called_once()


class AccessNoticeConfigureTests(unittest.TestCase):
    setUp = fixtures.AssistantTests.setUp
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save
    running_bot_settings = fixtures.AssistantTests.running_bot_settings

    def test_configuration_queues_a_downgrade_but_a_stale_save_does_not_notify(self):
        initial = self.payload()
        initial.update(component='nodera')
        initial['accounts'][0]['role'] = 'administrator'
        saved = self.save(initial)['settings']
        notices.path(api).unlink(missing_ok=True)
        payload = self.payload()
        payload.update(component='nodera', expected_revision=saved['revision'])
        payload['accounts'][0].update(role='guest', products=[])
        result = self.save(payload)
        self.assertEqual(result['settings_apply']['access_notifications']['queued'], 1)
        row = notices.load(api)['queue'][0]
        self.assertEqual((row['before']['role'], row['after']['role']), ('administrator', 'guest'))
        before = notices.path(api).read_bytes()
        with self.assertRaises(operations.OperationConflict): self.save(payload)
        self.assertEqual(notices.path(api).read_bytes(), before)

    def test_failed_bot_restart_does_not_queue_a_role_change_that_was_rolled_back(self):
        payload, unit = self.running_bot_settings()
        notices.path(api).unlink(missing_ok=True)
        payload['bot_token'] = '123456:new-token-abcdefghijklmnopqrstuvwxyz'
        payload['accounts'][0].update(role='guest', products=[])
        payload['accounts'].append(control.Account(telegram_id=22, role='owner').model_dump())
        previous = control.read(api)
        with patch.object(worker, 'assert_unit_owner', return_value=unit), patch.object(worker, 'checked', side_effect=[RuntimeError('failure'), None]) as restart:
            with self.assertRaises(api.HTTPException): self.save(payload)
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(control.read(api), previous)
        self.assertFalse(notices.path(api).exists())

    def test_ai_preparation_is_silent_and_only_human_confirmation_queues_the_change(self):
        initial = self.payload()
        initial.update(component='nodera')
        initial['accounts'][0]['role'] = 'owner'
        self.save(initial)
        notices.path(api).unlink(missing_ok=True)
        prepared = actions.call(api, 'panel', {'method': 'PATCH', 'body': {'telegram_id': 11, 'role': 'guest'}})
        self.assertTrue(prepared['confirmation_required'])
        self.assertFalse(notices.path(api).exists())
        record = operations.read(commands.pending_path(api, 'panel', operation_id=prepared['operation_id']))
        actions.confirm(api, 'panel', record)
        row = notices.load(api)['queue'][0]
        self.assertEqual((row['before']['role'], row['after']['role']), ('owner', 'guest'))
        with self.assertRaises(api.HTTPException): actions.confirm(api, 'panel', record)
        self.assertEqual(len(notices.load(api)['queue']), 1)

    def test_notice_storage_failure_does_not_claim_configuration_was_rolled_back(self):
        with patch.object(notices, 'enqueue_changes', side_effect=OSError('disk unavailable')):
            result = self.save()
        self.assertEqual(result['settings_apply']['access_notifications']['state'], 'unavailable')
        self.assertEqual(control.read(api)['revision'], result['settings']['revision'])
