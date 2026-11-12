import json
import asyncio
import os
import re
import subprocess
import textwrap
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.support import api
from tests.api.support import ROOT
from tests.api.test_security_hardening import BASH
import application_diagnostics as diagnostics
import application_operation as operations
import assistant_commands
import assistant_control as control
import diagnostic_checks
import diagnostic_configuration
import diagnostic_execution
import diagnostic_history
import diagnostic_rules
import observability_control
import operation_log


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = {'revision': 'a' * 32, 'accounts': [], 'mcp': {'vps-controller-mcp': True},
            'providers': [{'id': 'b' * 32, 'provider_url': 'https://provider.example/v1', 'api_key': 'fixture-private-key', 'model': 'model'}]}
        self.env = types.SimpleNamespace(DATA_DIR=self.root, ENV_FILE=self.root / 'environment',
            managed_services=lambda: {'api': {'unit': 'api.service'}},
            observe_service=lambda unit: {'runtime': {'state': 'running'}})
        self.enterContext(patch.object(control, 'read', return_value=self.settings))
        self.enterContext(patch.object(control, '_read', return_value=self.settings))

    def pending(self): return operations.read(assistant_commands.pending_path(self.env, 'panel'))
    def confirm(self): return assistant_commands.execute(self.env, 'panel', '/confirm ' + self.pending()['code'])

    def test_rule_change_is_prepared_then_confirmed_once_and_partial_PATCH_preserves_values(self):
        current = diagnostic_rules.read(self.env)
        result = diagnostic_rules.call(self.env, {'method': 'PATCH', 'body': {'revision': current['revision'], 'disk_percent': 88}}, 'panel')
        self.assertTrue(result['confirmation_required'])
        self.assertEqual(diagnostic_rules.read(self.env)['disk_percent'], 95)
        self.confirm()
        changed = diagnostic_rules.read(self.env)
        self.assertEqual(changed['disk_percent'], 88)
        self.assertEqual(changed['http_min_requests'], 20)
        first_revision = changed['revision']
        self.confirm()
        self.assertEqual(diagnostic_rules.read(self.env)['revision'], first_revision)
        diagnostic_rules.call(self.env, {'method': 'PATCH', 'body': {'revision': first_revision, 'trigger_samples': 3}}, 'panel')
        self.confirm()
        self.assertEqual(diagnostic_rules.read(self.env)['disk_percent'], 88)

    def test_stale_rule_revision_and_revoked_rights_do_not_apply(self):
        diagnostic_rules.call(self.env, {'method': 'PATCH', 'body': {'revision': '0' * 32, 'disk_percent': 88}}, 'panel')
        self.settings['revision'] = 'c' * 32
        with self.assertRaises(api.HTTPException): self.confirm()
        self.assertEqual(diagnostic_rules.read(self.env)['revision'], '0' * 32)
        with self.assertRaises(api.HTTPException):
            diagnostic_rules.call(self.env, {'method': 'PATCH', 'body': {'revision': 'f' * 32}}, 'panel')

    def test_disabled_connector_invalidates_prepared_policy(self):
        diagnostic_rules.call(self.env, {'method': 'PATCH', 'body': {'revision': '0' * 32}}, 'panel')
        self.settings['mcp']['vps-controller-mcp'] = False
        with self.assertRaises(api.HTTPException): self.confirm()
        self.assertFalse((self.root / 'diagnostic-rules.json').exists())

    def test_functional_generation_requires_confirmation_and_uncertain_result_is_not_replayed(self):
        query = diagnostics.IntegrationQuery(integration_id='ai:' + 'b' * 32, mode='functional')
        with patch.object(diagnostics, 'measure_provider', side_effect=TimeoutError('fixture-private-key')) as generate:
            result = diagnostics.integration_probe(self.env, query)
            generate.assert_not_called()
            self.assertTrue(result['confirmation_required'])
            self.confirm()
            self.confirm()
            self.assertEqual(generate.call_count, 1)
        record = operations.read(self.root / 'operations' / (result['operation_id'] + '.json'))
        self.assertEqual(record['state'], 'unknown')
        self.assertNotIn('fixture-private-key', json.dumps(record))

    def test_confirmed_functional_request_checks_catalog_then_one_token_generation_without_persisting_content(self):
        catalog = MagicMock(status=200)
        catalog.read.return_value = b'{"data":[{"id":"model"}]}'
        generation = MagicMock(status=200)
        generation.read.return_value = b'{"choices":[{"message":{"content":"private-generated-text"}}]}'
        connection = MagicMock()
        connection.getresponse.side_effect = [catalog, generation]
        with patch.object(diagnostics.subprocess, 'run', return_value=types.SimpleNamespace(stdout='93.184.216.34 STREAM public\n')), \
             patch.object(diagnostics.socket, 'create_connection', return_value=MagicMock()), \
             patch.object(diagnostics.ssl, 'create_default_context', return_value=MagicMock()), \
             patch.object(diagnostics.http.client, 'HTTPSConnection', return_value=connection):
            result = diagnostics.measure_provider(self.settings['providers'][0], 'functional')
        calls = connection.request.call_args_list
        self.assertEqual(calls[0].args[:2], ('GET', '/v1/models'))
        self.assertEqual(calls[1].args[:2], ('POST', '/v1/chat/completions'))
        self.assertEqual(json.loads(calls[1].kwargs['body'])['max_completion_tokens'], 1)
        self.assertEqual(result['observations'][0]['data']['generation']['state'], 'passed')
        self.assertNotIn('private-generated-text', json.dumps(result))
        self.assertNotIn('fixture-private-key', json.dumps(result))

    def test_configuration_generations_cover_domains_without_reading_or_hashing_secret_values(self):
        (self.root / 'environment').write_text('PASSWORD=fixture-private-key')
        operations.atomic_json(self.root / 'dns-settings.json', {'dns': 'sensitive-name'})
        first = diagnostic_configuration.inventory(self.env)
        (self.root / 'environment').write_text('PASSWORD=another-private-value')
        operations.atomic_json(self.root / 'mihomo/settings/dns.json', {'value': 'sensitive-setting'})
        current = diagnostic_configuration.inventory(self.env)
        self.assertNotEqual(first['environment']['revision'], current['environment']['revision'])
        self.assertNotEqual(first['mihomo_settings']['revision'], current['mihomo_settings']['revision'])
        self.assertTrue({'dns', 'network', 'security', 'relay', 'gateway', 'mihomo_profiles'} <= current.keys())
        self.assertNotIn('PASSWORD', json.dumps(current))
        self.assertNotIn('sensitive', json.dumps(current))

    def test_structured_journal_correlates_only_explicit_IDs_and_cleans_content(self):
        identity = 'd' * 32
        self.env.run = MagicMock(return_value=json.dumps({'__REALTIME_TIMESTAMP': '1700000000000000',
            'MESSAGE': 'operation_id=' + identity + ' token=fixture-private-key', '_SYSTEMD_UNIT': 'api.service', 'PRIORITY': '3'}))
        result = observability_control.journal(self.env)
        self.assertEqual(result['records'][0]['operation_id'], identity)
        self.assertEqual(result['records'][0]['at'], 1700000000)
        self.assertNotIn('fixture-private-key', json.dumps(result))
        self.assertEqual(self.env.run.call_args.args[-2:], ('-u', 'vps-control-web.service'))

    def test_command_journal_links_start_result_and_service_without_command_arguments(self):
        import diagnostic_context as tracing
        token = tracing.current.set({'operation_id': 'e' * 32})
        try:
            with operation_log.command(self.root, ['systemctl', 'restart', 'vps-control-api.service', 'fixture-private-key']) as completed:
                completed(1)
        finally: tracing.current.reset(token)
        journal = operation_log.get(self.root, 'system', 'e' * 32)
        self.assertEqual([row['result'] for row in journal['events']], ['running', 'failed'])
        self.assertEqual(journal['events'][0]['span_id'], journal['events'][1]['span_id'])
        self.assertNotIn('fixture-private-key', json.dumps(journal))

    def test_component_functional_read_only_check_uses_real_HTTP_contract(self):
        with patch.object(diagnostic_checks, 'local_http', return_value={'state': 'passed', 'status': 200}) as measure:
            result = diagnostic_checks.check(self.env, diagnostic_checks.CheckQuery(component='api', level='functional'))
        measure.assert_called_once_with(8000, '/api/health', json_response=True)
        self.assertEqual(result['observations'][-1]['state'], 'passed')
        self.assertEqual(result['coverage']['mode'], 'read_only_no_settings_changes')

    def test_rule_thresholds_and_sample_counts_drive_monitor(self):
        policy = {**diagnostic_rules.read(self.env), 'trigger_samples': 1}
        operations.atomic_json(self.root / 'diagnostic-rules.json', policy)
        snapshot = {'checked_at': 1700000000., 'services': {}}
        conditions = {'service:api': {'rule': 'service_error', 'component': 'api', 'failing': True}}
        with patch.object(diagnostic_history, 'sample', return_value=(snapshot, conditions, False)):
            diagnostic_history.observe(self.env)
        self.assertEqual(len(diagnostic_history.alerts(self.env, diagnostic_history.HistoryQuery())['active']), 1)

    def test_installed_runtime_functional_mode_uses_provider_adapter_with_no_tools_or_output_content(self):
        from tests.api.test_assistant_control import runner
        with patch.object(runner, 'provider_request', new=AsyncMock(return_value={'content': 'OK'})) as provider, \
             patch.object(runner, 'mcp_connections') as tools:
            result = asyncio.run(runner.request_main({'settings': {'modules': {'ai-agent': True}}, 'actor': 'panel'}, '--functional-check'))
        self.assertTrue(result['provider_response'])
        self.assertEqual(provider.call_args.args[2], [])
        self.assertEqual(provider.call_args.kwargs, {'completion_limit': 1})
        tools.assert_not_called()
        self.assertNotIn('content', result)

    def test_component_chat_prepares_installed_runtime_check_without_generating(self):
        self.env.managed_services = lambda: {'nodera': {'unit': 'vps-control-ai-agent.service'}}
        result = diagnostic_checks.check(self.env, diagnostic_checks.CheckQuery(component='nodera', level='functional', integration_id='ai:' + 'b' * 32))
        self.assertTrue(result['confirmation_required'])
        self.assertTrue(self.pending()['application_runtime'])
        with patch.object(control, 'runtime_request', return_value={'ok': True, 'result': {'provider_response': True}}) as runtime:
            self.confirm()
            self.confirm()
        self.assertEqual(runtime.call_count, 1)
        self.assertEqual(runtime.call_args.args[1], '--functional-check')

    def test_service_history_keeps_last_error_after_a_later_success(self):
        snapshot = {'checked_at': 1700000000., 'services': {'api': {'state': 'error', 'last_error': 'exit-code', 'last_finished_at': 'failed'}}}
        with patch.object(diagnostic_history, 'sample', return_value=(snapshot, {}, False)):
            diagnostic_history.observe(self.env)
        snapshot = {'checked_at': 1700000060., 'services': {'api': {'state': 'running', 'last_started_at': 'started'}}}
        with patch.object(diagnostic_history, 'sample', return_value=(snapshot, {}, False)):
            diagnostic_history.observe(self.env)
        result = diagnostic_history.service_history(self.env)['api']
        self.assertEqual(result['last_error']['reason'], 'exit-code')
        self.assertEqual(result['last_successful_start'], 'started')

    @unittest.skipUnless(BASH, 'Requires Bash')
    def test_shell_tracing_preserves_output_failure_and_single_execution_without_logging_arguments(self):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        wrappers = '\n'.join(textwrap.dedent(re.search(r'(?ms)^  ' + name + r'\(\) \{.*?^  \}', source).group()) for name in ('systemctl', 'curl'))
        for name, status in (('systemctl', 7), ('curl', 28)):
            (self.root / name).write_text('#!/usr/bin/env bash\nprintf "business-output\\n"\nprintf x >> "$TEST_ROOT/calls"\nexit ' + str(status) + '\n', encoding='utf-8')
            (self.root / name).chmod(0o755)
        root = self.root.resolve().as_posix()
        if os.name == 'nt': root = '/' + root[0].lower() + root[2:]
        script = 'set -Eeuo pipefail\nPATH="$TEST_ROOT:$PATH"\n' + '''diagnostic_shell_command() { printf '%s|' "$@" >> "$TEST_ROOT/trace"; printf '\\n' >> "$TEST_ROOT/trace"; }
''' + wrappers + '\nsystemctl restart vps-control-api.service fixture-private-key || printf "status=%s\\n" "$?"\ncurl --data fixture-private-key https://private.example || printf "status=%s\\n" "$?"\n'
        result = subprocess.run([BASH, '-s'], input=script, text=True, capture_output=True, timeout=10, env={**os.environ, 'TEST_ROOT': root})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('business-output'), 2)
        self.assertIn('status=7', result.stdout)
        self.assertIn('status=28', result.stdout)
        self.assertEqual((self.root / 'calls').read_text(), 'xx')
        trace = (self.root / 'trace').read_text()
        self.assertIn('vps-control-api.service', trace)
        self.assertNotIn('fixture-private-key', trace)
        self.assertNotIn('private.example', trace)

    def test_abandoned_diagnostic_reports_unknown_and_can_release_pending_without_replay(self):
        result = diagnostics.integration_probe(self.env, diagnostics.IntegrationQuery(integration_id='ai:' + 'b' * 32, mode='functional'))
        record = {'id': result['operation_id'], 'action': 'integration-functional-probe', 'state': 'running', 'started_at': '2000-01-01T00:00:00+00:00'}
        operations.atomic_json(self.root / 'operations' / (record['id'] + '.json'), record)
        self.assertEqual(operations.diagnostic_state(record)['state'], 'unknown')
        pending = self.pending()
        operations.atomic_json(assistant_commands.pending_path(self.env, 'panel'), {**pending, 'submitted': True})
        with patch.object(diagnostics, 'measure_provider') as generate:
            assistant_commands.execute(self.env, 'panel', '/cancel')
        generate.assert_not_called()
        self.assertFalse(assistant_commands.pending_path(self.env, 'panel').exists())
