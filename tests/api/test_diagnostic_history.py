import copy
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
import application_diagnostics as diagnostics
import application_operation as operations
import assistant_control as control
import diagnostic_history as history


class DiagnosticHistoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root)
        self.now = time.time()
        self.snapshot = {'checked_at': self.now, 'build': {'commit': 'a' * 40}, 'services': {'api': {'state': 'running'}},
            'local_health': {'nodera': 'healthy'}, 'configuration_revisions': {'assistant': 'b' * 32},
            'http': {'complete_coverage': True, 'error_rate': 0}, 'disk_used_percent': 10}

    def sample(self, failing, offset, eligible=False):
        snapshot = {**copy.deepcopy(self.snapshot), 'checked_at': self.now + offset}
        conditions = {'service:api': {'rule': 'service_error', 'component': 'api', 'failing': failing}}
        with patch.object(history, 'sample', return_value=(snapshot, conditions, eligible)):
            history.observe(self.env)

    def test_alert_needs_two_samples_and_unknown_never_proves_recovery(self):
        self.sample(True, 0)
        self.assertEqual(history.alerts(self.env, history.HistoryQuery())['active'], [])
        self.sample(True, 60)
        self.assertEqual(len(history.alerts(self.env, history.HistoryQuery())['active']), 1)
        self.sample(None, 120)
        self.sample(False, 180)
        self.assertEqual(len(history.alerts(self.env, history.HistoryQuery())['active']), 1)
        self.sample(False, 240)
        result = history.alerts(self.env, history.HistoryQuery())
        self.assertEqual(result['active'], [])
        self.assertEqual([row['transition'] for row in result['history']], ['firing', 'recovered'])
        self.assertEqual(result['history'][-1]['observed_duration_s'], 240)

    def test_sampling_gap_and_fast_repeat_do_not_count_as_consecutive_evidence(self):
        self.sample(True, 0)
        self.sample(True, 10)
        self.sample(True, 300)
        self.assertEqual(history.alerts(self.env, history.HistoryQuery())['active'], [])
        self.sample(True, 360)
        self.assertEqual(len(history.alerts(self.env, history.HistoryQuery())['active']), 1)

    def test_disappeared_component_does_not_implicitly_recover(self):
        self.sample(True, 0)
        self.sample(True, 60)
        snapshot = {**self.snapshot, 'checked_at': self.now + 120, 'services': {}}
        with patch.object(history, 'sample', return_value=(snapshot, {}, False)):
            history.observe(self.env)
        active = history.alerts(self.env, history.HistoryQuery())['active']
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['measurement'], 'unknown')

    def test_only_eligible_samples_replace_baseline_and_comparison_does_not_write(self):
        with patch.object(history, 'sample', return_value=(self.snapshot, {}, False)):
            result = history.baseline(self.env)
        self.assertTrue(result['missing_data'])
        self.sample(False, 0, eligible=True)
        self.sample(True, 60, eligible=False)
        before = history.path(self.env).read_bytes()
        current = copy.deepcopy(self.snapshot)
        current['build']['commit'] = 'c' * 40
        with patch.object(history, 'sample', return_value=(current, {}, False)):
            result = history.baseline(self.env)
        self.assertEqual(history.path(self.env).read_bytes(), before)
        self.assertEqual(result['confirmed'][0]['claim'], 'local_snapshot_fields_differ')
        self.assertEqual(result['coverage']['causation'], 'not_proven')
        baseline = next(row['data'] for row in result['observations'] if row['id'] == 'baseline')
        self.assertEqual(baseline['build']['commit'], 'a' * 40)

    def test_empty_or_stale_alert_history_does_not_mean_healthy(self):
        result = history.alerts(self.env, history.HistoryQuery())
        self.assertTrue(result['coverage']['stale'])
        self.assertTrue(result['missing_data'])
        self.assertEqual(result['coverage']['rule_changes'], 'diagnostic_alert_rules_PATCH_requires_human_confirmation')

    def test_history_is_bounded_and_drops_are_reported(self):
        data = {'schema_version': 1, 'alerts': {}, 'history': [{'at': self.now, 'transition': 'firing'} for _ in range(510)], 'dropped': 0}
        operations.atomic_json(history.path(self.env), data)
        self.sample(False, 0)
        result = history.alerts(self.env, history.HistoryQuery(limit=10))
        self.assertEqual(len(result['history']), 10)
        self.assertEqual(result['coverage']['dropped'], 10)
        self.assertTrue(result['coverage']['truncated'])

    def report(self):
        return {'observations': [
            {'id': 'build', 'data': {'commit': 'a' * 40}},
            {'id': 'service:api', 'data': {'runtime': {'state': 'running'}}},
            {'id': 'health', 'data': {'nodera': {'status': 'healthy', 'checked_at': operations.timestamp()}}},
            {'id': 'resources', 'data': {'disk_used_percent': 10}},
            {'id': 'errors', 'data': {'summary': {'requests': 20, 'errors': 0}}},
            {'id': 'pending_operations', 'data': []}],
            'coverage': {'http': {'successful_requests_recorded': True, 'first_at': self.now - 600,
                'tracked_since': self.now - 600, 'coverage_since': self.now - 600, 'requested_from': self.now - 300},
                'operations': {'truncated': False, 'sources': {'system': {'available': True, 'scan_truncated': False, 'unreadable': 0}}},
                'service_inventory_truncated': False}}

    def test_baseline_acceptance_rejects_stale_health_partial_storage_and_unreadable_operations(self):
        good = self.report()
        self.enterContext(patch.object(control, 'read', return_value={'revision': 'b' * 32}))
        with patch.object(diagnostics, 'summary', return_value=good):
            self.assertTrue(history.sample(self.env)[2])
        variants = [copy.deepcopy(good) for _ in range(4)]
        variants[0]['coverage']['http']['successful_requests_recorded'] = False
        variants[1]['observations'][2]['data']['nodera']['checked_at'] = '2000-01-01T00:00:00+00:00'
        variants[2]['coverage']['operations']['sources']['system']['unreadable'] = 1
        variants[3]['observations'][5]['data'] = [{'operation_id': 'c' * 32}]
        for variant in variants:
            with self.subTest(variant=variants.index(variant)), patch.object(diagnostics, 'summary', return_value=variant):
                snapshot, _, eligible = history.sample(self.env)
                self.assertFalse(eligible)
                self.assertNotIn('provider_url', str(snapshot))

    def test_diagnostic_tools_are_read_only_and_malformed_history_is_reported(self):
        settings = {'accounts': []}
        with patch.object(control, 'read', return_value=settings):
            for tool in ('diagnostic_baseline', 'diagnostic_alerts'):
                with self.assertRaises(api.HTTPException) as error:
                    control.control_tool(self.env, tool, {'method': 'PATCH'}, 'panel')
                self.assertEqual(error.exception.status_code, 422)
        history.path(self.env).write_text('broken')
        with patch.object(control, 'read', return_value=settings):
            result = control.control_tool(self.env, 'diagnostic_alerts', actor='panel')
        self.assertEqual(result['missing_data'][0]['reason'], 'storage_unavailable')
