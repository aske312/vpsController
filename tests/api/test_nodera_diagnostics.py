import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.api.support import api, manager
import assistant_control as control
import assistant_diagnostics as diagnostics
import assistant_memory as memory
import assistant_personalization as context
import application_operation as operations
import component_checks as checks
import component_state
import mihomo_core
from service_state import _service_observation


class NoderaDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)
        self.settings = {'revision': 'a' * 32, 'control_token': 'internal-secret', 'api_key': 'provider-secret',
                         'memory_days': 30, 'accounts': [{'telegram_id': 11, 'vps_access': True}]}

    def test_long_term_facts_survive_expiry_and_global_cache_evicts_conversations(self):
        context.update(self.env, self.settings, '11', context.MemoryAction(operation='remember', text='Stable preference', permanent=True))
        context.update(self.env, self.settings, '11', context.MemoryAction(operation='remember', text='Temporary topic'))
        with patch.object(context.time, 'time', return_value=time.time() + 31 * 86400):
            snapshot = context.snapshot(self.env, self.settings, '11')
            self.assertEqual([row['text'] for row in snapshot['facts']], ['Stable preference'])
        memory.append(self.env, 'b' * 32, '22', 'bot', 'Conversation', [], {'answer': 'Answer'}, 'model', self.settings)
        permanent_size = context.usage(self.env)['used_bytes']
        with patch.object(memory, 'MAX_BYTES', permanent_size - 10):
            memory._prune(self.env, self.settings)
            self.assertFalse(memory.path(self.env, 'b' * 32, '22', 'bot').exists())
            self.assertEqual(context.snapshot(self.env, self.settings, '11')['facts'][0]['text'], 'Stable preference')
        self.assertEqual(memory.MAX_BYTES, 5 * 1024 ** 3)

    def test_sensitive_diagnostics_are_admin_only_and_secrets_are_redacted(self):
        with patch.object(control, 'read', return_value=self.settings):
            for name in ('vps_logs', 'vps_network', 'vps_security', 'vps_diagnostics'):
                with self.assertRaises(api.HTTPException) as error: diagnostics.call(self.env, name, {}, '11')
                self.assertEqual(error.exception.status_code, 403)
            result = diagnostics.sanitize(self.env, {'api_key': 'provider-secret', 'password_authentication': 'yes',
                'admin_password_strong': False, 'lines': ['Bearer internal-secret token=provider-secret']})
        text = json.dumps(result)
        self.assertNotIn('provider-secret', text)
        self.assertNotIn('internal-secret', text)
        self.assertEqual(result['password_authentication'], 'yes')
        self.assertIs(result['admin_password_strong'], False)

    def test_once_applied_units_are_distinct_from_exited_daemons(self):
        properties = {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited', 'Type': 'oneshot', 'RemainAfterExit': 'yes'}
        one_shot = _service_observation(properties, 'now')
        self.assertTrue(one_shot['active'])
        self.assertIn('постоянный процесс не требуется', one_shot['runtime']['reason'])
        daemon = _service_observation({**properties, 'Type': 'simple'}, 'now')
        self.assertFalse(daemon['active'])
        self.assertEqual(daemon['runtime']['state'], 'error')
        unknown = _service_observation({**properties, 'Type': ''}, 'now')
        self.assertEqual(unknown['runtime']['state'], 'unknown')

    def test_health_hides_removed_components_and_does_not_keep_stale_green_status(self):
        self.env.protocol_image_manifests = lambda **kwargs: {'nodera': {'installed': True}, 'mihomo': {'installed': False}}
        operations.atomic_json(self.root / 'component-health.json', {'items': {
            'nodera': {'status': 'healthy', 'reason': 'OK', 'checked_at': '2020-01-01T00:00:00+00:00'},
            'mihomo': {'status': 'healthy', 'reason': 'OK', 'checked_at': operations.timestamp()}}})
        visible = checks.visible(self.env)
        self.assertEqual([item['id'] for item in visible], ['nodera'])
        self.assertEqual(visible[0]['status'], 'unknown')
        (self.root / 'component-health.json').write_text('broken')
        self.assertEqual(component_state.saved_health(self.root, 'mihomo')['state'], 'unknown')

    def test_nodera_health_checks_are_local_and_do_not_call_the_paid_model(self):
        self.settings['modules'] = {'ai-agent': True, 'nodera-bot': False}
        self.env.observe_service = lambda unit: {'runtime': {'state': 'running', 'reason': 'Active'}}
        self.env.run = lambda *args, **kwargs: 'AI-agent runtime ready' if 'ai-agent' in str(args[0]) else 'NoderaBot runtime ready'
        for component in control.MODULES:
            runtime = self.root / component
            (runtime / 'venv' / 'bin').mkdir(parents=True)
            (runtime / 'venv' / 'bin' / 'python').touch()
            (runtime / ('runner.py' if component == 'ai-agent' else 'bot.py')).write_text('pass')
        with patch.object(control, 'read', return_value=self.settings), patch.object(control, 'ready', return_value=True), patch.object(control, 'runtime_directory', side_effect=lambda component: self.root / component), patch.object(control, 'runtime_request', side_effect=AssertionError('Paid request')):
            report = checks.inspect(self.env, 'nodera', {'service': 'agent.service'})
        self.assertEqual(report['status'], 'healthy')

    def test_mihomo_health_checks_api_and_actual_core_version(self):
        self.env.observe_service = lambda unit: {'runtime': {'state': 'running', 'reason': 'Active'}}
        self.env.mihomo_core_installed_version = lambda: '1.19.34'
        with patch('assistant_mihomo.request', return_value={'installed': True, 'core_version': '1.19.31'}):
            report = checks.inspect(self.env, 'mihomo', {'service': 'manager.service'})
        self.assertEqual(report['status'], 'critical')
        self.assertTrue(any(row['name'] == 'version_consistency' and not row['ok'] for row in report['checks']))

    def test_core_upgrade_uses_newer_verified_bundle_and_never_downgrades(self):
        bundled, runtime = self.root / 'bundled', self.root / 'runtime'
        bundled.write_text('1.19.34'); runtime.write_text('1.19.31')
        def version(path): return tuple(map(int, path.read_text().split('.'))) if path.is_file() else None
        with patch.object(mihomo_core, 'version', side_effect=version):
            self.assertTrue(mihomo_core.sync(bundled, runtime))
            self.assertEqual(runtime.read_text(), '1.19.34')
            bundled.write_text('1.19.32')
            self.assertFalse(mihomo_core.sync(bundled, runtime))
            self.assertEqual(runtime.read_text(), '1.19.34')
        with patch.object(manager, 'RUNTIME_CORE_BIN', runtime), patch.object(manager, 'BUNDLED_CORE_BIN', bundled), patch.object(manager, 'CORE_BIN', bundled):
            self.assertEqual(manager.core_binary(), runtime)
            runtime.unlink()
            self.assertEqual(manager.core_binary(), bundled)
