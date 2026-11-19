import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from tests.api.support import api
import alert_context
import alert_watches
import assistant_commands
import observability_control
import telegram_alerts as alerts


class AlertWatchesTests(unittest.TestCase):
    def setUp(self):
        self.env = types.SimpleNamespace(DATA_DIR=Path(self.enterContext(tempfile.TemporaryDirectory())),
            managed_services=lambda: {'api': {'name': 'API', 'unit': 'vps-control-api.service'}})
        self.now = 1800000000
        self.settings = {'bot_token': 'token', 'revision': 'b' * 32, 'mcp': {'vps-controller-mcp': True},
                         'modules': {}, 'accounts': [{'telegram_id': 123, 'role': 'owner'}]}
        self.rows = []
        self.history = []
        self.context = {'system': 'локальные службы работают', 'profiles': [], 'services': {'api': {'state': 'running'}}, 'checked_at': self.now}
        self.enterContext(patch.object(alerts.control, 'read', side_effect=lambda _: self.settings))
        self.enterContext(patch.object(alerts.control, '_read', side_effect=lambda _: self.settings))
        self.enterContext(patch.object(alerts, 'history', side_effect=lambda _: self.history))
        self.enterContext(patch.object(alerts.time, 'time', side_effect=lambda: self.now))
        self.enterContext(patch.object(alert_watches, 'operation_rows', side_effect=lambda _: (self.rows, {})))
        self.enterContext(patch.object(alert_context, 'snapshot', side_effect=lambda _: self.context))
        self.configure()

    def configure(self, **changes):
        value = alerts.load(self.env)
        return alerts.configure(self.env, alerts.Settings(**{**value['settings'], 'enabled': True, 'owner_id': 123,
            **changes, 'expected_revision': value['revision']}))

    def rule(self, **changes):
        return alert_watches.Watch(name='Проверка обновления', **changes).model_dump()

    def test_legacy_settings_do_not_enable_new_sources(self):
        value = alerts.load(self.env)
        for key in ('operations', 'watches', 'snoozed_until', 'delivery_interval_seconds', 'operation_timeout_seconds'):
            value['settings'].pop(key)
        alerts.operations.atomic_json(alerts.path(self.env), value)
        loaded = alerts.load(self.env)['settings']
        self.assertFalse(loaded['operations'])
        self.assertEqual(loaded['watches'], [])

    def test_old_client_save_preserves_new_watch_settings(self):
        rule = self.rule(source='service', target='api', events=['failed'])
        state = self.configure(watches=[rule], delivery_interval_seconds=30)
        alerts.configure(self.env, alerts.Settings(enabled=True, owner_id=123, failures=False, recoveries=True, boots=True, expected_revision=state['revision']))
        current = alerts.load(self.env)['settings']
        self.assertEqual(current['watches'], [rule])
        self.assertEqual(current['delivery_interval_seconds'], 30)

    def test_stall_waits_for_deadline_and_diagnostic_failure_does_not_block_it(self):
        identity = 'e' * 32
        self.configure(watches=[self.rule(operation_id=identity, after_seconds=60)])
        self.rows = [('system', {'id': identity, 'state': 'running', 'started_at': self.now, 'updated_at': self.now})]
        self.now += 59
        with patch.object(alerts, 'history', side_effect=ValueError('bad diagnostic file')): alerts.sample(self.env)
        self.assertEqual(alerts.load(self.env)['queue'], [])
        self.now += 1
        with patch.object(alerts, 'history', side_effect=ValueError('bad diagnostic file')): alerts.sample(self.env)
        self.assertEqual(len(alerts.load(self.env)['queue']), 1)

    def test_global_pause_does_not_replay_terminal_operations_after_resume(self):
        self.configure(operations=True, snoozed_until=self.now + 60)
        self.now += 1
        self.rows = [('system', {'id': 'f' * 32, 'state': 'failed', 'updated_at': self.now})]
        alerts.sample(self.env)
        self.now += 61
        alerts.sample(self.env)
        self.assertEqual(alerts.load(self.env)['queue'], [])

    def test_broad_operation_watch_skips_history_deduplicates_and_confirms_recovery(self):
        self.rows = [('system', {'id': 'a' * 32, 'state': 'failed', 'action': 'application-update', 'updated_at': self.now - 10})]
        self.configure(watches=[self.rule(target='application-update', events=['failed', 'unknown', 'recovered'])])
        alerts.sample(self.env)
        self.assertEqual(alerts.load(self.env)['queue'], [])
        self.now += 1
        self.rows.append(('system', {'id': 'c' * 32, 'state': 'failed', 'action': 'application-update', 'updated_at': self.now,
                                     'message': 'SECRET token https://private.example', 'error_code': 'exit-code'}))
        alerts.sample(self.env); alerts.sample(self.env)
        queued = alerts.load(self.env)['queue']
        self.assertEqual(len(queued), 1)
        self.assertIn('Зона:', queued[0]['text'])
        self.assertIn('Система при проверке:', queued[0]['text'])
        self.assertNotIn('SECRET', queued[0]['text'])
        self.now += 1
        self.rows[-1][1].update(state='unknown', updated_at=self.now)
        alerts.sample(self.env)
        self.now += 1
        self.rows[-1][1].update(state='succeeded', result='success', updated_at=self.now)
        alerts.sample(self.env)
        self.assertEqual(len(alerts.load(self.env)['queue']), 3)
        self.assertIn('Состояние подтверждено', alerts.load(self.env)['queue'][-1]['text'])

    def test_specific_running_operation_alerts_on_timeout_and_acknowledgement_is_not_recovery(self):
        identity = 'd' * 32
        self.rows = [('system', {'id': identity, 'state': 'running', 'started_at': self.now - 1000, 'updated_at': self.now - 100})]
        self.configure(watches=[self.rule(operation_id=identity, after_seconds=60)])
        alerts.sample(self.env)
        self.assertIn('дольше порога', alerts.load(self.env)['queue'][0]['text'])
        self.rows[0][1].update(state='failed', resolved_at='reviewed')
        alerts.sample(self.env)
        self.assertEqual(len(alerts.load(self.env)['queue']), 1)

    def test_unknown_service_does_not_claim_recovery_and_disabling_removes_queued_watch(self):
        rule = self.rule(source='service', target='api', events=['failed', 'unknown', 'recovered'], after_seconds=60)
        self.configure(watches=[rule])
        self.context['services']['api']['state'] = 'error'
        alerts.sample(self.env)
        self.context['services'] = {}
        self.now += 61
        alerts.sample(self.env)
        texts = [row['text'] for row in alerts.load(self.env)['queue']]
        self.assertEqual(len(texts), 2)
        self.assertFalse(any('Состояние подтверждено' in text for text in texts))
        self.configure(watches=[{**rule, 'enabled': False}])
        self.assertEqual(alerts.load(self.env)['queue'], [])

    def test_pause_discards_backlog_and_resumes_current_service_observation(self):
        rule = self.rule(source='service', target='api', events=['failed', 'recovered'])
        self.configure(watches=[rule], snoozed_until=self.now + 60)
        self.context['services']['api']['state'] = 'error'
        self.history.append({'id': 'disk', 'transition': 'firing', 'at': self.now, 'rule': 'disk_pressure'})
        alerts.sample(self.env)
        self.assertEqual(alerts.load(self.env)['queue'], [])
        self.now += 61
        alerts.sample(self.env)
        self.assertEqual(len(alerts.load(self.env)['queue']), 1)

    def test_delivery_history_survives_setting_change_during_send(self):
        alerts.test(self.env)
        def send(*args, **kwargs):
            self.configure(enabled=False)
            return {'state': 'sent'}
        with patch.object(alerts, 'send', side_effect=send): alerts.deliver(self.env)
        value = alerts.status(self.env)
        self.assertEqual(value['deliveries'][0]['state'], 'sent')
        self.assertEqual(value['pending'], 0)

    def test_ai_changes_require_confirmation_are_idempotent_and_revision_bound(self):
        before = alerts.status(self.env)
        result = observability_control.call(self.env, {'method': 'POST', 'path': '/owner-alerts/watches',
            'body': {'expected_revision': before['revision'], 'watch': self.rule(source='service', target='api', events=['failed'])}}, '123')
        self.assertTrue(result['confirmation_required'])
        self.assertEqual(alerts.status(self.env)['settings']['watches'], [])
        record = assistant_commands.pending_records(self.env, '123')[0][1]
        assistant_commands.execute(self.env, '123', '/confirm ' + record['code'])
        changed = alerts.status(self.env)
        self.assertEqual(len(changed['settings']['watches']), 1)
        assistant_commands.execute(self.env, '123', '/confirm ' + record['code'])
        self.assertEqual(alerts.status(self.env)['revision'], changed['revision'])
        with self.assertRaises(HTTPException) as caught:
            observability_control.call(self.env, {'method': 'PATCH', 'path': '/owner-alerts', 'body': {'expected_revision': before['revision'], 'enabled': False}}, '123')
        self.assertEqual(caught.exception.status_code, 409)

    def test_ai_catalog_is_read_only_and_permissions_are_rechecked(self):
        result = observability_control.call(self.env, {'path': '/owner-alerts/catalog'}, '123')
        self.assertEqual(result['services'][0]['id'], 'api')
        before = alerts.status(self.env)
        observability_control.call(self.env, {'method': 'PATCH', 'path': '/owner-alerts', 'body': {'expected_revision': before['revision'], 'snoozed_until': self.now + 3600}}, '123')
        record = assistant_commands.pending_records(self.env, '123')[0][1]
        self.settings['accounts'] = []
        with self.assertRaises(HTTPException): observability_control.confirm(self.env, '123', record)
        self.assertEqual(alerts.load(self.env)['settings']['snoozed_until'], 0)

    def test_rule_scope_validation_and_service_allowlist(self):
        for changes in ({'source': 'service', 'target': ''}, {'source': 'service', 'target': 'api', 'events': ['stalled']}, {'target': '; rm -rf /'}, {'events': []}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError): self.rule(**changes)
        with self.assertRaises(HTTPException): self.configure(watches=[self.rule(source='service', target='not-managed', events=['failed'])])


class AlertContextTests(unittest.TestCase):
    def test_context_links_only_configured_profiles_and_never_claims_client_connectivity(self):
        env = types.SimpleNamespace(DATA_DIR=Path(self.enterContext(tempfile.TemporaryDirectory())))
        current = {'checked_at': 1800000000, 'services': {'api': {'state': 'running'}}, 'local_health': {'mihomo': 'healthy'}}
        import diagnostic_history
        root = env.DATA_DIR / 'mihomo'; root.mkdir()
        (root / 'profiles.json').write_text(json.dumps([{'id': 'one', 'name': 'Family', 'connections': [{'component': 'transport-reality', 'credential': {'secret': 'SECRET'}}]},
            {'id': 'two', 'name': 'Work', 'connections': [{'component': 'transport-wireguard'}]}]))
        with patch.object(diagnostic_history, 'load', return_value={'current': current}), patch.object(alert_context.time, 'time', return_value=1800000001):
            context = alert_context.snapshot(env)
        text = '\n'.join(alert_context.lines(context, 'transport-reality'))
        self.assertIn('возможное влияние на 1: Family', text)
        self.assertIn('связь с устройствами не проверена', text)
        self.assertNotIn('Work', text)
        self.assertNotIn('SECRET', str(context))
        with patch.object(diagnostic_history, 'load', return_value={'current': current}), patch.object(alert_context.time, 'time', return_value=1800001000):
            self.assertEqual(alert_context.snapshot(env)['system'], 'состояние не подтверждено')


if __name__ == '__main__': unittest.main()
