import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
import telegram_alerts as alerts


class OwnerAlertsTests(unittest.TestCase):
    def setUp(self):
        self.env = types.SimpleNamespace(DATA_DIR=Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.settings = {'bot_token': 'test', 'modules': {'ai-agent': False, 'nodera-bot': False},
                         'accounts': [{'telegram_id': 123, 'role': 'owner'}]}
        self.rows = [{'id': 'old', 'at': 'old', 'transition': 'firing'}]
        self.enterContext(patch.object(alerts.control, 'read', side_effect=lambda _: self.settings))
        self.enterContext(patch.object(alerts, 'history', side_effect=lambda _: self.rows))
        self.boot = self.enterContext(patch.object(alerts.operations, 'boot_id', return_value='boot1'))
        self.configure()

    def configure(self, **changes):
        value = alerts.load(self.env)
        return alerts.configure(self.env, alerts.Settings(**{**value['settings'], 'enabled': True, 'owner_id': 123,
                                  **changes, 'expected_revision': value['revision']}))

    def test_baseline_dedup_recovery_boot_and_disabled_workers(self):
        alerts.sample(self.env)
        self.assertEqual(alerts.load(self.env)['queue'], [])
        self.rows += [{'id': 'new', 'at': 'now', 'transition': 'firing'}, {'id': 'new', 'at': 'later', 'transition': 'recovered'}]
        self.boot.return_value = 'boot2'
        alerts.sample(self.env)
        alerts.sample(self.env)
        queue = alerts.load(self.env)['queue']
        self.assertEqual(len(queue), 3)
        self.assertIn('если перезагрузка неожиданная', queue[-1]['text'])
        self.assertIn('Время обнаружения:', queue[-1]['text'])
        with patch.object(alerts, 'send', return_value={'state': 'sent'}) as send:
            alerts.deliver(self.env)
        send.assert_called_once()
        self.assertEqual(alerts.status(self.env)['last_delivery']['state'], 'sent')

    def test_429_respects_delay_but_unknown_is_never_retried(self):
        alerts.test(self.env)
        with patch.object(alerts, 'send', return_value={'state': 'pending', 'retry_after': 120}) as send:
            alerts.deliver(self.env)
            alerts.deliver(self.env)
        self.assertEqual(send.call_count, 1)
        value = alerts.load(self.env)
        value['next_delivery'] = 0
        value['queue'][0]['due'] = 0
        alerts.operations.atomic_json(alerts.path(self.env), value)
        with patch.object(alerts, 'send', return_value={'state': 'unknown'}) as send:
            alerts.deliver(self.env)
            alerts.deliver(self.env)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(alerts.load(self.env)['queue'], [])

    def test_disabling_and_owner_revocation_cancel_pending(self):
        alerts.test(self.env)
        self.configure(enabled=False)
        self.assertEqual(alerts.load(self.env)['queue'], [])

        self.configure()
        self.rows += [{'id': 'new', 'at': 'now', 'transition': 'firing'}]
        alerts.sample(self.env)
        self.settings['accounts'][0]['role'] = 'moderator'
        with patch.object(alerts, 'send') as send: alerts.deliver(self.env)
        send.assert_not_called()
        self.assertEqual(alerts.load(self.env)['queue'], [])


    def test_administrator_can_receive_alerts_but_blocking_cancels_delivery(self):
        self.settings['accounts'][0]['role']='administrator'
        self.assertEqual(alerts.status(self.env)['owners'][0]['telegram_id'],123)
        self.configure(); alerts.test(self.env)
        with patch.object(alerts,'send',return_value={'state':'sent'}) as send:
            alerts.deliver(self.env)
        send.assert_called_once()
        self.rows.append({'id':'blocked-new','transition':'firing','at':'now'})
        alerts.sample(self.env)
        import assistant_contacts
        assistant_contacts.save_profile(self.env,self.settings,'123',{'blocked':True})
        with patch.object(alerts,'send') as send: alerts.deliver(self.env)
        send.assert_not_called()
        self.assertEqual(alerts.load(self.env)['queue'], [])

    def test_stranded_claim_is_unknown_not_replayed(self):
        alerts.test(self.env)
        value = alerts.load(self.env)
        value['queue'][0]['state'] = 'sending'
        alerts.operations.atomic_json(alerts.path(self.env), value)
        with patch.object(alerts, 'send') as send: alerts.deliver(self.env)
        send.assert_not_called()
        self.assertEqual(alerts.status(self.env)['last_delivery']['state'], 'unknown')

    def test_http_alert_explains_impact_evidence_422_and_response_without_ai(self):
        self.rows.append({'id': 'http', 'component': 'application', 'rule': 'http_error_rate', 'transition': 'firing',
                          'at': 1794836147.250515, 'details': {'requests': 100, 'errors': 25, 'window_minutes': 5,
                          'threshold_percent': 10, 'error_statuses': {'422': 20, '500': 5}, 'confirmation_samples': 2},
                          'condition': 'SECRET-CONDITION', 'logs': 'SECRET-LOG', 'url': 'https://private.test/token'})
        with patch.object(alerts.socket, 'gethostname', return_value='vps-1'):
            alerts.sample(self.env)
        text = alerts.load(self.env)['queue'][0]['text']
        for expected in ('VPS · vps-1', 'Много HTTP-ошибок', '16.11.2026', 'UTC', '25 из 100', '25.0%',
                         'Порог: 10%', '422 × 20', 'проверку формата', 'Что делать:', 'Предупреждение', 'подписки недоступны',
                         'проверками: 2'):
            self.assertIn(expected, text)
        for hidden in ('1794836147.250515', 'SECRET', 'private.test'):
            self.assertNotIn(hidden, text)
        self.assertLess(len(text), 4096)

    def test_recovery_does_not_claim_all_errors_are_gone(self):
        text = alerts.format_alert({'component': 'application', 'rule': 'http_error_rate', 'transition': 'recovered', 'at': 1,
            'observed_duration_s': 125, 'details': {'requests': 100, 'errors': 5, 'window_minutes': 5, 'threshold_percent': 10}})
        for expected in ('Восстановление', 'Информационно', '5 из 100', '2 мин. 5 сек.', 'наблюдайте', 'этой проверке'):
            self.assertIn(expected, text)
        self.assertNotIn('ошибок нет', text)

    def test_all_rules_and_legacy_events_have_explanation_and_action(self):
        for rule, component, details, expected in (
            ('disk_pressure', 'system', {'used_percent': 97.5, 'threshold_percent': 95}, '97.5%'),
            ('service_error', 'api', {'state': 'stopped'}, 'остановлена'),
            ('local_health', 'mihomo', {'state': 'critical'}, 'критический статус'),
            ('http_error_rate', 'application', {}, 'не сохранены'),
            ('future_rule', 'new_component', {}, 'причина по этому сообщению не установлена'),
        ):
            with self.subTest(rule=rule):
                text = alerts.format_alert({'rule': rule, 'component': component, 'transition': 'firing', 'at': 'bad', 'details': details})
                self.assertIn(expected, text)
                self.assertIn('Важность:', text)
                self.assertIn('Что делать:', text)
                self.assertIn('время не сохранено', text)
                self.assertLess(len(text), 4096)

    def test_alert_text_ignores_untrusted_strings_and_invalid_numbers(self):
        text = alerts.format_alert({'component': 'secret\nTOKEN', 'rule': 'secret\nTOKEN', 'at': float('nan'),
            'transition': 'firing', 'details': {'state': 'secret', 'requests': float('inf')}})
        self.assertNotIn('secret', text)
        self.assertNotIn('TOKEN', text)
        self.assertIn('Где: Сервер', text)
        self.assertEqual(alerts.event_time(float('inf')), 'время не сохранено')
        self.assertEqual(alerts.event_time(10**30), 'время не сохранено')

    def queued_analysis(self):
        self.rows.append({'id': 'http', 'rule': 'http_error_rate', 'component': 'application', 'at': time.time(), 'transition': 'firing'})
        alerts.sample(self.env)
        return alerts.load(self.env)['queue'][0]

    def test_analysis_buttons_are_optional_and_context_survives_delivery(self):
        row = self.queued_analysis()
        self.assertIsNone(alerts.analysis_keyboard(self.settings, row))
        self.settings['modules'] = {'ai-agent': True, 'nodera-bot': True}
        with patch.object(alerts, 'send', return_value={'state': 'sent'}) as send:
            alerts.deliver(self.env)
        markup = send.call_args.kwargs['reply_markup']
        buttons = markup['inline_keyboard'][0]
        self.assertEqual([button['text'] for button in buttons], ['Разобрать с ИИ', 'Глубокий разбор'])
        self.assertTrue(all(len(button['callback_data'].encode()) <= 64 for button in buttons))
        self.assertEqual(alerts.load(self.env)['queue'], [])
        prompt = alerts.analysis_prompt(self.env, self.settings, '123', row['analysis_token'], 'deep')
        self.assertIn(row['text'], prompt)
        self.assertIn('глубокий разбор', prompt)
        self.assertIn('Не меняй настройки', prompt)
        self.assertIn('прошлое состояние', prompt)

    def test_analysis_requires_current_recipient_owner_and_unexpired_context(self):
        token = self.queued_analysis()['analysis_token']
        for actor in ('other', 'panel'):
            with self.subTest(actor=actor), self.assertRaises(alerts.HTTPException):
                alerts.analysis_prompt(self.env, self.settings, actor, token, 'brief')
        self.settings['accounts'].append({'telegram_id': 456, 'role': 'owner'})
        with self.assertRaises(alerts.HTTPException): alerts.analysis_prompt(self.env, self.settings, '456', token, 'brief')
        self.settings['accounts'][0]['role'] = 'moderator'
        with self.assertRaises(alerts.HTTPException): alerts.analysis_prompt(self.env, self.settings, '123', token, 'brief')
        self.settings['accounts'][0]['role'] = 'owner'
        with patch.object(alerts.time, 'time', return_value=time.time() + 8 * 86400):
            with self.assertRaises(alerts.HTTPException): alerts.analysis_prompt(self.env, self.settings, '123', token, 'brief')
        self.configure(enabled=False)
        with self.assertRaises(alerts.HTTPException): alerts.analysis_prompt(self.env, self.settings, '123', token, 'brief')

    def test_analysis_repeat_is_throttled_and_store_is_bounded(self):
        token = self.queued_analysis()['analysis_token']
        alerts.analysis_prompt(self.env, self.settings, '123', token, 'brief')
        with self.assertRaises(alerts.HTTPException) as error:
            alerts.analysis_prompt(self.env, self.settings, '123', token, 'brief')
        self.assertEqual(error.exception.status_code, 429)
        self.assertIn('глубокий', alerts.analysis_prompt(self.env, self.settings, '123', token, 'deep'))
        value = alerts.load(self.env)
        for at in range(150):
            value['queue'] = []
            alerts.enqueue(value, 'event', str(at), time.time(), analyze=True)
        self.assertEqual(len(value['analysis']), 100)

    def test_bot_chat_resolves_only_server_saved_context_and_keeps_normal_runtime(self):
        row = self.queued_analysis()
        settings = {**self.settings, 'memory_enabled': False, 'revision': 'revision', 'model': 'fixture'}
        payload = alerts.control.Chat(prompt='untrusted replacement', alert={'token': row['analysis_token'], 'mode': 'brief'})
        with patch.object(alerts.control, 'runtime_request', return_value={'result': {'answer': 'analysis', 'tools_used': []}}) as runtime, \
             patch.object(alerts.control, 'provider_usage', return_value={}):
            output = alerts.control._chat_request(self.env, payload, '123', 'bot', settings=settings)
        self.assertEqual(output['result']['answer'], 'analysis')
        prompt = runtime.call_args.args[2]['prompt']
        self.assertIn(row['text'], prompt)
        self.assertNotIn('untrusted replacement', prompt)
        with self.assertRaises(alerts.HTTPException):
            alerts.control._chat_request(self.env, payload, '123', 'panel', settings=settings)
