import tempfile
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
        self.assertEqual(len(alerts.load(self.env)['queue']), 3)
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
        self.settings['accounts'][0]['role'] = 'administrator'
        with patch.object(alerts, 'send') as send: alerts.deliver(self.env)
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
