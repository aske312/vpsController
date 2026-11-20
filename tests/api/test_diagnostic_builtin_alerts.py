import tempfile
import json
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api  # initializes API module paths
import application_operation as operations
import diagnostic_builtin_alerts as builtin
import diagnostic_history as history
import diagnostic_rules as rules
import telegram_alerts
import alert_context


class BuiltinAlertTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root)
        self.policy = rules.read(self.env)
        self.now = time.time()
        self.host = {'id': 'a' * 32, 'name': 'Relay A', 'state': 'connected',
                     'routes': [{'state': 'listening', 'listen_port': 443, 'errors': 900,
                                 'diagnostics': {'interval': {'seconds': 60, 'errors': 0}}}],
                     'address_checks': [{'domain': 'relay.example.org', 'state': 'verified'}]}

    def relay(self, host=None):
        return builtin.relay_conditions({'hosts': [host or self.host]}, self.policy, self.now)

    def test_resources_use_fresh_finite_evidence_and_load_per_core(self):
        value = {'cpu_percent': 95, 'memory_used_percent': 92, 'load5': 8, 'cpu_count': 4, 'observations': None}
        result = builtin.resources_conditions(value, self.policy, self.now)
        self.assertTrue(all(row['failing'] for row in result.values()))
        self.assertEqual(result['load_pressure']['details']['value'], 2)
        value.update(cpu_percent=float('nan'), memory_used_percent=True, cpu_count=0)
        self.assertTrue(all(row['failing'] is None for row in builtin.resources_conditions(value, self.policy, self.now).values()))
        value.update(cpu_percent=95, memory_used_percent=92, cpu_count=4,
                     observations={'cpu': {'available': True, 'checked_at': self.now - 181}, 'memory': {'available': False}})
        self.assertTrue(all(row['failing'] is None for row in builtin.resources_conditions(value, self.policy, self.now).values()))

    def test_idle_clients_and_lifetime_errors_do_not_fire(self):
        result = self.relay()
        self.assertTrue(all(row['failing'] is False for row in result.values()))
        self.host['routes'][0]['diagnostics']['interval']['errors'] = 10
        self.assertTrue(self.relay()['relay-errors:' + self.host['id']]['failing'])

    def test_incomplete_intervals_never_prove_recovery(self):
        identity = 'relay-errors:' + self.host['id']
        self.host['routes'].append({'state': 'unknown'})
        self.assertIsNone(self.relay()[identity]['failing'])
        self.host['routes'][0]['diagnostics']['interval']['errors'] = 10
        self.assertTrue(self.relay()[identity]['failing'])
        self.host['routes'][0]['diagnostics']['interval']['seconds'] = 2
        self.assertIsNone(self.relay()[identity]['failing'])

    def test_relay_loss_route_drift_and_dns_only_are_separate(self):
        self.host['routes'][0]['state'] = 'drift'
        self.host['address_checks'][0]['state'] = 'mismatch'
        result = self.relay()
        self.assertTrue(result['relay-route:' + self.host['id']]['failing'])
        self.assertEqual(result['relay-route:' + self.host['id']]['details']['ports'], [443])
        self.assertTrue(result['relay-address:' + self.host['id']]['failing'])
        self.host['address_checks'][0]['state'] = 'dns_only'
        self.assertIsNone(self.relay()['relay-address:' + self.host['id']]['failing'])
        self.host['state'] = 'unknown'
        result = self.relay()
        self.assertTrue(result['relay:' + self.host['id']]['failing'])
        self.assertTrue(all(row['failing'] is None for key, row in result.items() if key != 'relay:' + self.host['id']))

    def test_normal_mutation_snapshot_is_unknown_and_abandoned_snapshot_fires(self):
        target = self.root / 'recovery/application-update'
        target.mkdir(parents=True)
        operations.atomic_json(self.root / 'application-action.json', {'state': 'running'})
        self.assertIsNone(builtin.recovery_condition(self.env)['failing'])
        operations.atomic_json(self.root / 'application-action.json', {'state': 'failed'})
        self.assertTrue(builtin.recovery_condition(self.env)['failing'])
        target.rmdir()
        self.assertFalse(builtin.recovery_condition(self.env)['failing'])

    def test_profile_snapshot_is_owned_by_its_worker_not_an_unrelated_running_operation(self):
        identity = 'd' * 32
        operations.atomic_json(self.root / 'mihomo/profile-recovery/manifest.json', {'operation_id': identity})
        operations.atomic_json(self.root / 'operations' / (identity + '.json'),
                               {'id': identity, 'state': 'running', 'unit': 'profile-worker.service'})
        operations.atomic_json(self.root / 'application-action.json', {'id': 'e'*32, 'state': 'running'})
        self.env.observe_service = lambda unit: {'unit_present': True, 'active': True, 'state': 'active'}
        self.assertIsNone(builtin.recovery_condition(self.env)['failing'])
        self.env.observe_service = lambda unit: {'unit_present': False, 'active': False, 'state': 'inactive'}
        self.assertTrue(builtin.recovery_condition(self.env)['failing'])
        operations.atomic_json(self.root / 'mihomo/operations' / (identity + '.json'),
                               {'id': identity, 'state': 'running', 'unit': 'delegated-worker.service'})
        self.env.observe_service = lambda unit: {'unit_present': True, 'active': True, 'state': 'active'} if unit == 'delegated-worker.service' else {'unit_present': False, 'active': False, 'state': 'inactive'}
        self.assertIsNone(builtin.recovery_condition(self.env)['failing'])

    def test_disabled_network_rules_do_not_call_relay(self):
        self.env.relay_control = lambda: self.fail('disabled rules must not perform network reads')
        policy = {**self.policy, **{key: False for key in ('relay_unavailable', 'relay_route', 'relay_address', 'connection_errors', 'cpu_pressure')}}
        result = builtin.collect(self.env, {'cpu_percent': 99}, policy)
        self.assertIsNone(result['cpu_pressure']['failing'])

    def test_legacy_partial_save_preserves_new_thresholds_and_switches(self):
        first = rules.save(self.env, rules.Rules(revision=self.policy['revision'], cpu_percent=80, relay_address=False))
        second = rules.save(self.env, rules.Rules(revision=first['revision'], disk_percent=96))
        self.assertEqual(second['cpu_percent'], 80)
        self.assertFalse(second['relay_address'])

    def test_relay_alert_context_matches_configured_profiles_and_resources(self):
        profiles = self.root / 'mihomo/profiles.json'
        profiles.parent.mkdir()
        profiles.write_text(json.dumps([
            {'id': 'one', 'name': 'Affected', 'connections': [{'component': 'transport-reality', 'settings': {'relay_host_id': self.host['id']}}]},
            {'id': 'two', 'name': 'Other', 'connections': []}]), encoding='utf-8')
        current = {'checked_at': self.now, 'services': {'api': {'state': 'running'}},
                   'basic_warnings': 1, 'resource_usage': {'cpu_percent': 92, 'memory_used_percent': 40}}
        with patch.object(history, 'load', return_value={'current': current}):
            context = alert_context.snapshot(self.env)
        text = '\n'.join(alert_context.lines(context, 'relay', host_id=self.host['id']))
        self.assertIn('Affected', text)
        self.assertNotIn('Other', text)
        self.assertIn('CPU 92%', text)
        self.assertIn('есть предупреждения', text)
        self.assertIn('связь с устройствами не проверена', text)

    def test_new_rules_require_consecutive_measurements_and_preserve_transition_evidence(self):
        for offset, state in ((0, True), (60, True), (120, None), (180, False), (240, False)):
            snapshot = {'checked_at': self.now + offset, 'services': {}, 'configuration_revisions': {}}
            condition = {'rule': 'cpu_pressure', 'component': 'system', 'failing': state,
                         'details': {'value': 96 if state else 20, 'threshold': 90}}
            with patch.object(history, 'sample', return_value=(snapshot, {'cpu_pressure': condition}, False)):
                history.observe(self.env)
            if offset == 180:
                self.assertTrue(history.load(self.env)['alerts']['cpu_pressure']['active'])
        rows = history.load(self.env)['history']
        self.assertEqual([row['transition'] for row in rows], ['firing', 'recovered'])
        self.assertEqual(rows[0]['details']['value'], 96)
        text = telegram_alerts.format_alert(rows[0])
        self.assertIn('96', text)
        self.assertIn('90', text)

    def test_relay_snapshot_only_uses_fixed_authenticated_pinned_gets(self):
        from relay_manager import RelayManager
        manager = RelayManager(self.root, '1.1.1.1')
        registry = {'revision': 'b' * 32, 'hosts': [{'id': 'a' * 32, 'name': 'A', 'agent_url': 'https://8.8.8.8:8443',
                     'token': 'secret', 'certificate_sha256': 'c' * 64, 'api_version': '1'}], 'routes': []}
        with patch.object(manager, 'read', return_value=registry), patch('relay_client.RelayClient') as client:
            client.return_value.host = '8.8.8.8'
            client.return_value.request.side_effect = [{'api_version': 1, 'version': '1.2.0', 'address_family': 'ipv4',
                'listen_ports': {'min': 20000, 'max': 20999}, 'transports': ['tcp', 'udp'], 'revision': 0,
                'routes': 0, 'failed_routes': 0, 'max_routes': 64}, {'revision': 0, 'items': []}]
            self.env.relay_control = lambda: manager
            result = builtin.relay_snapshot(self.env)
            self.assertEqual(result['hosts'][0]['state'], 'connected')
            self.assertEqual(client.return_value.request.call_count, 2)
            self.assertEqual(client.call_args.kwargs, {'timeout': 2})
            self.assertEqual(client.call_args.args[1:], ('secret', 'c' * 64))
            self.assertTrue(all(call.args[0] == 'GET' and call.args[1] in ('/v1/status', '/v1/routes') for call in client.return_value.request.call_args_list))
