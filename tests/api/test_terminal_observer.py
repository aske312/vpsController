import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.api.support import api
import assistant_control as control
import assistant_roles as roles
import terminal_observer as terminal


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root, managed_services=lambda: {'api': {'unit': 'vps-control-api.service'}})
        self.settings = {'accounts': [{'telegram_id': 11, 'role': 'moderator', 'products': ['assistant']}], 'mcp': {}}
        self.enterContext(patch.object(control, 'read', return_value=self.settings))

    def test_help_has_no_process_or_account_creation_and_regular_roles_cannot_execute(self):
        with patch.object(terminal, 'bounded_run') as execute, patch.object(terminal, 'provision') as create:
            result = terminal.call(self.env, {}, 'panel')
            self.assertIn('processes', result['commands'])
            self.assertNotIn('shell', result['commands'])
            with self.assertRaises(api.HTTPException) as error: terminal.call(self.env, {}, '11')
            self.assertEqual(error.exception.status_code, 403)
            execute.assert_not_called()
            create.assert_not_called()
        self.assertNotIn('terminal_read', roles.DEFAULTS['moderator'])

    def test_explicit_read_only_role_can_use_terminal_without_management_rights(self):
        self.settings['role_permissions'] = {'moderator': ['server_read', 'terminal_read']}
        result = terminal.call(self.env, {}, '11')
        self.assertIn('sockets', result['commands'])
        self.assertFalse(roles.allows(self.settings, '11', 'server_manage'))
        self.settings['role_permissions']['moderator'] = ['terminal_read']
        with self.assertRaises(api.HTTPException): terminal.call(self.env, {}, '11')

    def test_shell_injection_unknown_arguments_and_secret_paths_are_rejected_before_spawn(self):
        payloads = [{'command': 'sh'}, {'command': 'uptime; reboot'}, {'command': 'processes', 'args': ['e']},
                    {'command': 'read_file', 'file': '/etc/vps-control/environment'}, {'command': 'read_file', 'file': '../shadow'},
                    {'command': 'service', 'service': 'api;reboot'}, {'command': 'memory', 'limit': 100000}]
        with patch.object(terminal, 'bounded_run') as execute:
            for payload in payloads:
                with self.subTest(payload=payload), self.assertRaises(api.HTTPException) as error:
                    terminal.call(self.env, {'body': payload}, 'panel')
                self.assertEqual(error.exception.status_code, 422)
            execute.assert_not_called()

    def test_service_profile_only_reads_explicit_non_secret_properties(self):
        command = terminal.argv(self.env, terminal.Query(command='service', service='api'))
        self.assertEqual(command[:3], ['systemctl', 'show', 'vps-control-api.service'])
        self.assertNotIn('Environment', ' '.join(command))
        self.assertNotIn('ExecStart', ' '.join(command))
        with self.assertRaises(api.HTTPException): terminal.argv(self.env, terminal.Query(command='service', service='unknown'))
        self.assertNotIn('args', ' '.join(terminal.argv(self.env, terminal.Query(command='processes'))))

    def test_sandbox_runs_non_root_without_shell_and_blocks_writes_network_and_privilege(self):
        with patch.object(terminal.shutil, 'which', side_effect=lambda name, **kwargs: '/usr/bin/' + name):
            command = terminal.launch_command(['free', '-b'], 991, 991, unit='vps-control-observer-fixture')
        self.assertIn('--uid=991', command)
        self.assertIn('--gid=991', command)
        for setting in ('ProtectSystem=strict', 'NoNewPrivileges=yes', 'CapabilityBoundingSet=',
                        'RestrictAddressFamilies=AF_UNIX AF_NETLINK', 'IPAddressDeny=any', 'RuntimeMaxSec=8s'):
            self.assertIn('--property=' + setting, command)
        self.assertEqual(command[-3:], ['--', '/usr/bin/free', '-b'])
        self.assertNotIn('/bin/sh', command)
        self.assertNotIn('sudo', command)

    def test_missing_identity_or_systemd_does_not_fall_back_to_root(self):
        with patch.object(terminal, 'identity', side_effect=api.HTTPException(409, 'missing')), patch.object(terminal, 'bounded_run') as execute:
            with self.assertRaises(api.HTTPException): terminal.call(self.env, {'body': {'command': 'memory'}}, 'panel')
            execute.assert_not_called()
        with patch.object(terminal.shutil, 'which', side_effect=['/usr/bin/free', None]):
            with self.assertRaises(api.HTTPException): terminal.launch_command(['free'], 991, 991, unit='fixed')

    def test_account_identity_rejects_root_login_shell_or_supplementary_groups(self):
        for uid, shell, groups in ((0, '/usr/sbin/nologin', [991]), (991, '/bin/bash', [991]), (991, '/usr/sbin/nologin', [991, 27])):
            user = types.SimpleNamespace(pw_uid=uid, pw_gid=991, pw_shell=shell, pw_dir='/nonexistent')
            with patch.dict(sys.modules, {'pwd': types.SimpleNamespace(getpwnam=lambda name: user), 'grp': types.SimpleNamespace(getgrnam=lambda name: types.SimpleNamespace(gr_gid=991))}), \
                 patch.object(terminal.os, 'name', 'posix'), patch.object(terminal.os, 'getgrouplist', return_value=groups, create=True):
                with self.assertRaises(api.HTTPException): terminal.identity()

    def test_result_is_bounded_sanitized_and_not_a_health_claim(self):
        self.settings['api_key'] = 'fixture-private-key'
        with patch.object(terminal, 'identity', return_value=(991, 991)), patch.object(terminal, 'launch_command', return_value=['fixed']), \
             patch.object(terminal, 'bounded_run', return_value=('fixture-private-key\none\ntwo\n', 0, False, False)):
            result = terminal.call(self.env, {'body': {'command': 'memory', 'limit': 2}}, 'panel')
        self.assertNotIn('fixture-private-key', json.dumps(result))
        self.assertEqual(len(result['observations'][0]['data']['lines']), 2)
        self.assertTrue(result['coverage']['truncated'])
        self.assertEqual(result['confirmed'], [])

    def test_output_collector_never_buffers_more_than_limit(self):
        stream = self.enterContext(tempfile.TemporaryFile())
        stream.write(b'line\n' * 10000)
        stream.seek(0)
        process = MagicMock(stdout=stream, returncode=0)
        process.poll.return_value = 0
        with patch.object(terminal.subprocess, 'Popen', return_value=process) as launch:
            output, code, timeout, truncated = terminal.bounded_run(['fixed'])
        self.assertLessEqual(len(output.encode()), terminal.MAX_BYTES)
        self.assertTrue(truncated)
        self.assertFalse(timeout)
        self.assertEqual(code, 0)
        self.assertEqual(launch.call_args.kwargs['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        self.assertNotIn('shell', launch.call_args.kwargs)
