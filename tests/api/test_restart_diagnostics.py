import json
import os
import subprocess
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import application_diagnostics
import assistant_control
import observability_control
import restart_diagnostics as restarts


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root, managed_services=lambda: {'api': {'unit': 'api.service'}},
            system_boot_time=lambda: datetime.fromtimestamp(1700000000, timezone.utc))
        self.enterContext(patch.object(assistant_control, 'read', return_value={'accounts': [], 'api_key': 'fixture-private-key', 'mcp': {'vps-controller-mcp': True}}))
        self.enterContext(patch.object(restarts.operations, 'boot_id', return_value='a' * 32))
        self.enterContext(patch.object(application_diagnostics, 'records', return_value=([], {})))
        self.commands = self.enterContext(patch.object(restarts, 'command', side_effect=self.command))

    def command(self, *args):
        if '--list-boots' in args:
            return json.dumps([{'boot_id': 'b' * 32, 'first_entry': 1699990000000000, 'last_entry': 1699999999000000},
                {'boot_id': 'a' * 32, 'first_entry': 1700000000000000, 'last_entry': 1700000010000000}])
        if args[0] == 'systemctl': return 'Id=apt-daily-upgrade.timer\nActiveState=active\nLastTriggerUSec=Fri 2026-11-13 06:01:41 MSK'
        if '_TRANSPORT=kernel' in args: return ''
        return json.dumps({'__REALTIME_TIMESTAMP': '1699999999000000', '__MONOTONIC_TIMESTAMP': '12345',
            '_BOOT_ID': 'b' * 32, '_SYSTEMD_UNIT': 'init.scope', 'UNIT': 'api.service', 'INVOCATION_ID': 'c' * 32,
            'MESSAGE': 'Stopped api.service token=fixture-private-key'})

    def collect(self, **kwargs):
        return restarts.collect(self.env, restarts.Query(until=1700000010, **kwargs))

    def observation(self, value, name):
        return next(row['data'] for row in value['observations'] if row['id'] == name)

    def test_boots_lifecycle_timers_and_no_false_causal_claim(self):
        value = self.collect()
        self.assertEqual(self.observation(value, 'current_boot')['boot_id'], 'a' * 32)
        self.assertEqual(len(self.observation(value, 'boots')), 2)
        event = self.observation(value, 'service_lifecycle')[0]
        self.assertEqual((event['boot_id'], event['unit'], event['monotonic_us']), ('b' * 32, 'api.service', 12345))
        self.assertEqual(event['invocation_id'], 'c' * 32)
        self.assertNotIn('fixture-private-key', json.dumps(value))
        self.assertFalse(value['confirmed'])
        self.assertEqual(self.observation(value, 'timers')[0]['Id'], 'apt-daily-upgrade.timer')

    def test_historical_window_and_boot_filter_precede_limit_and_kernel_is_not_current_boot_only(self):
        self.collect(minutes=60, boot_id='b' * 32, services=['api'])
        calls = [call.args for call in self.commands.call_args_list if any(arg.startswith('--grep=') for arg in call.args)]
        self.assertEqual(len(calls), 2)
        for args in calls:
            self.assertIn('--boot=' + 'b' * 32, args)
            self.assertEqual(args[args.index('--since') + 1], '@1699996410.0')
            self.assertEqual(args[args.index('--until') + 1], '@1700000010.0')
            self.assertNotIn('--dmesg', args)
        self.assertIn('--unit=apt-daily-upgrade.service', calls[0])
        self.assertIn('_TRANSPORT=kernel', calls[1])

    def test_unavailable_sources_are_unknown_not_no_incidents(self):
        self.commands.side_effect = HTTPException(503, 'private stderr')
        value = self.collect()
        sources = {row['source'] for row in value['missing_data']}
        self.assertTrue({'boots', 'service_lifecycle', 'kernel_failures', 'timers'} <= sources)
        self.assertNotIn('private stderr', json.dumps(value))
        self.assertFalse(value['confirmed'])

    def test_operation_old_boot_is_context_and_report_has_own_time(self):
        self.root.joinpath('recovery').mkdir()
        self.root.joinpath('recovery/safe-update-report.txt').write_text(
            'Started: 2023-11-14T22:13:19+00:00\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n'
            'Reboot: automatic after successful checks\nResult: success\nprivate-secret', encoding='utf-8')
        row = {'id': 'd' * 32, 'action': 'auto-safe-update', 'state': 'succeeded', 'boot_id': 'b' * 32,
               'started_at': '2023-11-14T22:13:19+00:00'}
        with patch.object(application_diagnostics, 'records', return_value=([('system', row)], {'system': {'scan_truncated': False}})):
            value = self.collect()
        operation = self.observation(value, 'operations')[0]
        self.assertEqual(operation['relation'], 'recorded_in_another_boot')
        self.assertEqual(operation['next_step']['arguments']['operation_id'], 'd' * 32)
        report = self.observation(value, 'latest_update_report')
        self.assertEqual(report['started_at'], 1699999999)
        self.assertEqual(report['packages']['upgraded'], 0)
        self.assertTrue(report['automatic_reboot_recorded'])
        self.assertNotIn('private-secret', json.dumps(value))
        self.assertFalse(value['confirmed'])

    def test_system_apt_entrypoint_is_structured_and_does_not_expose_argv(self):
        original = self.command
        def source(*args):
            if args[0] != 'systemctl': return original(*args)
            return original(*args) + '\n\nId=apt-daily-upgrade.service\nTriggeredBy=apt-daily-upgrade.timer\nExecStart={ path=/usr/local/sbin/vps-control ; argv[]=/usr/local/sbin/vps-control auto-safe-update ; secret=private-secret }'
        self.commands.side_effect = source
        value = self.collect()
        entry = self.observation(value, 'apt_upgrade_entrypoint')
        self.assertTrue(entry['recognized_application_auto_update'])
        self.assertTrue(entry['triggered_by_apt_timer'])
        self.assertNotIn('private-secret', json.dumps(value))
        self.assertNotIn('ExecStart', json.dumps(value))

    def test_report_requires_both_logs_and_server_permissions(self):
        for grants in (['server_read'], ['logs_read']):
            settings = {'accounts': [{'telegram_id': 42, 'role': 'moderator', 'products': ['assistant']}], 'role_permissions': {'moderator': grants}}
            with patch.object(assistant_control, 'read', return_value=settings), self.assertRaises(HTTPException) as error:
                observability_control.call(self.env, {'path': '/restarts'}, '42')
            self.assertEqual(error.exception.status_code, 403)
        self.commands.assert_not_called()

    def test_invalid_input_cannot_inject_paths_units_or_commands(self):
        for body in ({'command': 'reboot'}, {'boot_id': '0; reboot'}, {'until': float('nan')}, {'minutes': 10081}, {'services': ['api'] * 9}):
            with self.subTest(body=body), self.assertRaises(ValidationError): restarts.Query.model_validate(body)
        for body in ({'services': ['../../secret']}, {'until': 999999999999}):
            with self.subTest(body=body), self.assertRaises(HTTPException): restarts.collect(self.env, restarts.Query(**body))
        self.commands.assert_not_called()

    def test_truncation_and_malformed_records_are_explicit(self):
        self.commands.side_effect = lambda *args: '\n'.join([self.command(*args), '{bad', 'null']) if any(arg.startswith('--grep=') for arg in args) else self.command(*args)
        value = self.collect(limit=3)
        coverage = value['coverage']['service_lifecycle']
        self.assertTrue(coverage['possibly_truncated'])
        self.assertEqual(coverage['unparsed_records'], 2)
        page = value['next_steps'][0]['arguments']['body']
        self.assertGreater(page['until'], value['coverage']['requested_from'])
        self.assertLess(page['until'], self.observation(value, 'service_lifecycle')[0]['at'])

    def test_worker_origin_is_durable_and_not_replaced_by_a_later_reader(self):
        identity = 'f' * 32
        current = self.root / 'action.json'
        origin = {'origin_unit': 'apt-daily-upgrade.service', 'systemd_invocation_id': 'b' * 32}
        with patch.dict(os.environ, {'VPS_CONTROL_OPERATION_ID': identity}), patch.object(restarts.operations, 'worker_origin', return_value=origin):
            restarts.operations.write_status(self.root, current, 'auto-safe-update', 'running', 3, 'accepted', '2023-11-14T22:13:19+00:00', identity)
        with patch.dict(os.environ, {'VPS_CONTROL_OPERATION_ID': ''}), patch.object(restarts.operations, 'worker_origin') as observed:
            value = restarts.operations.write_status(self.root, current, 'auto-safe-update', 'rebooting', 100, 'reboot', '2023-11-14T22:13:19+00:00', identity)
            observed.assert_not_called()
        self.assertEqual(value['origin_unit'], 'apt-daily-upgrade.service')
        self.assertEqual(application_diagnostics.safe_record(value, 'system')['systemd_invocation_id'], 'b' * 32)

    def test_origin_uses_cgroup_and_validated_invocation_without_reading_environment_secrets(self):
        with patch.dict(os.environ, {'INVOCATION_ID': 'c' * 32, 'API_KEY': 'private-secret'}), \
             patch.object(Path, 'read_text', return_value='0::/system.slice/apt-daily-upgrade.service\n'):
            self.assertEqual(restarts.operations.worker_origin(), {'origin_unit': 'apt-daily-upgrade.service', 'systemd_invocation_id': 'c' * 32})
        with patch.dict(os.environ, {'INVOCATION_ID': 'private-secret'}), patch.object(Path, 'read_text', side_effect=OSError()):
            self.assertEqual(restarts.operations.worker_origin(), {})

    def test_actual_subprocess_adapter_distinguishes_grep_no_match_from_failed_source(self):
        # Invoke the real helper, bypassing the collection fixture above.
        with patch.object(restarts.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='', stderr='')):
            # Patcher keeps the original callable for exercising process semantics.
            self.assertEqual(self.command_adapter('journalctl', '--grep=OOM'), '')
            with self.assertRaises(HTTPException): self.command_adapter('systemctl', 'show', 'apt-daily.timer')
        with patch.object(restarts.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1, stdout='', stderr='access denied')):
            with self.assertRaises(HTTPException): self.command_adapter('journalctl', '--grep=OOM')
        with patch.object(restarts.subprocess, 'run', side_effect=subprocess.TimeoutExpired('journalctl', 6)):
            with self.assertRaises(HTTPException): self.command_adapter('journalctl', '--grep=OOM')

    def test_existing_HTTP_MCP_tool_has_role_and_readonly_guards(self):
        with patch.object(api, 'DATA_DIR', self.root), patch.object(api, 'managed_services', self.env.managed_services):
            api.app.dependency_overrides[api.require_assistant_control] = lambda: None
            try:
                with TestClient(api.app) as client:
                    def call(actor='panel', **kwargs):
                        return client.post('/api/assistant/control-tool', headers={'X-MCP-Actor': actor},
                            json={'name': 'vps_observability', 'path': '/restarts', **kwargs})
                    self.assertEqual(call(body={'until': 1700000010}).status_code, 200)
                    self.assertEqual(call('42').status_code, 403)
                    self.assertEqual(call(method='PATCH').status_code, 422)
                    self.assertEqual(call(body={'shell': 'reboot'}).status_code, 422)
            finally: api.app.dependency_overrides.clear()

    command_adapter = staticmethod(restarts.command)
