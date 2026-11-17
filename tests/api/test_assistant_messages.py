"""Presentation preserves operation state, command identities and safe markup."""
import unittest

from tests.api.test_assistant_commands import bot
from assistant_messages import styled_message, diagnostic_text


class MessagePresentationTests(unittest.TestCase):
    def test_diagnostics_are_readable_and_preserve_unknown_and_partial_states(self):
        text = diagnostic_text({'state': 'unknown', 'partial': True, 'message': 'Collector timed out',
                                'findings': [{'id': 'admin_password_strong', 'state': 'passed', 'risk': 'Weak password', 'recommendation': 'Change password'},
                                             {'id': 'panel_access', 'state': 'risk', 'risk': 'Firewall mismatch', 'recommendation': 'Review rules'}]})
        self.assertIn('Состояние: неизвестно', text)
        self.assertIn('Неполные данные: да', text)
        self.assertIn('проверка пройдена', text)
        self.assertNotIn('Weak password', text)
        self.assertIn('Firewall mismatch', text)
        self.assertNotIn('{', text)
        self.assertNotIn('```', text)
        self.assertLess(len(diagnostic_text({'logs': ['x' * 1000] * 100})), 6600)
    def test_operation_states_do_not_claim_success_before_completion(self):
        identity = 'a' * 32
        for state, title in [('в очереди', 'Операция принята'), ('выполняется', 'Операция выполняется'),
                             ('ошибка', 'Операция завершилась ошибкой'), ('состояние не подтверждено', 'Результат пока не подтверждён'),
                             ('выполнено', 'Операция завершена')]:
            with self.subTest(state=state):
                display = styled_message('/confirm', f'Операция {identity}\n{state}\n/operation {identity}')
                self.assertIn(title, display)
                self.assertIn('/operation ' + identity, display)
                self.assertEqual('✅' in display, state == 'выполнено')

    def test_confirmation_preserves_decisions_and_escapes_telegram_markup(self):
        raw = 'Подтвердите restart для fixture. Действие может прервать соединения службы.\n/confirm aabbccdd'
        display = styled_message('/service fixture restart', raw)
        self.assertIn('/confirm aabbccdd', display)
        self.assertIn('/cancel aabbccdd', display)
        self.assertIn('ещё не началось', display)
        actions = {}
        bot.inline_keyboard(raw, '/service fixture restart', True, actions)
        self.assertIn('/confirm aabbccdd', str(actions))
        html = bot.telegram_html(styled_message('/status', 'Сервер: <fixture>\nВерсия: 1.0'))
        self.assertIn('<code>&lt;fixture&gt;</code>', html)
        self.assertNotIn('<fixture>', html)

    def test_model_ids_and_empty_guest_replies_are_preserved(self):
        raw = 'Модели · страница 1/2\nvendor/model-a\nvendor/model-b\n\n/model <ID> — выбрать'
        actions = {}
        keyboard = bot.inline_keyboard(raw, '/models', True, actions)
        self.assertTrue(keyboard)
        display = styled_message('/models', raw)
        for model in ('vendor/model-a', 'vendor/model-b'):
            self.assertIn(model, display)
            self.assertIn('/model ' + model, str(actions))
        self.assertEqual(styled_message('/help', ''), '')
        self.assertEqual(styled_message('/ai', 'Ответ модели'), 'Ответ модели')
