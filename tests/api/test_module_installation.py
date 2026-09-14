import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from tests.api.support import ROOT, manager

BASH = (next((str(p) for p in (Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')) if p.exists()), None)
        if os.name == 'nt' else shutil.which('bash'))


class ModuleInstallationTests(unittest.TestCase):
    def test_stopped_module_remains_installed_and_repair_restarts_it(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(manager, 'CONFIG_ROOT', Path(folder)),
            patch.object(manager, 'VLESS_CDN_ROUTE_ROOT', Path(folder) / 'routes'),
            patch.object(manager, 'profiles', return_value=[]),
            patch.object(manager, 'state', return_value={'modules': {'transport-hysteria2': True}}),
            patch.object(manager, 'systemctl_active', return_value=False),
            patch.object(manager, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run,
        ):
            self.assertTrue(manager.module_is_installed('transport-hysteria2'))
            self.assertFalse(manager.module_is_ready('transport-hysteria2'))
            report = manager.reconciliation_report(False)
            self.assertFalse(report['healthy'])
            run.assert_not_called()
            manager.reconciliation_report(True)
            self.assertIn(('systemctl', 'restart', manager.SERVICE_BY_MODULE['transport-hysteria2']), [call.args for call in run.call_args_list])

    def test_module_used_by_retiring_connections_cannot_be_removed(self):
        with (
            patch.object(manager, 'manifest', return_value={'name': 'VLESS'}),
            patch.object(manager, 'profiles', return_value=[{'name': 'Existing', 'connections': [], 'retiring_connections': [{'component': 'transport-reality'}]}]),
            patch.object(manager, 'call_module_script') as uninstall,
        ):
            with self.assertRaises(manager.HTTPException) as raised:
                manager.remove_module('transport-reality')
        self.assertEqual(raised.exception.status_code, 409)
        uninstall.assert_not_called()

    def test_installer_timeout_leaves_unknown_terminal_status_without_a_retry(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(manager, 'ACTION_FILE', Path(folder) / 'action.json'),
            patch.object(manager, 'SETTINGS_ROOT', Path(folder)),
            patch.object(manager, 'manifest', return_value={'id': 'transport-hysteria2', 'name': 'Hysteria2'}),
            patch.object(manager, 'module_is_ready', return_value=False),
            patch.object(manager, 'preflight_module'),
            patch.object(manager, 'default_settings', return_value={'port': 18443}),
            patch.object(manager, 'call_module_script', side_effect=subprocess.TimeoutExpired('test-installer', 600)) as install,
            patch.object(manager.logger, 'error'),
        ):
            with self.assertRaises(manager.HTTPException) as raised:
                manager.install_module('transport-hysteria2')
            self.assertEqual(raised.exception.status_code, 504)
            self.assertEqual(manager.get_action_payload()['state'], 'unknown')
            install.assert_called_once()

    def test_module_mutation_waits_for_profile_transaction(self):
        entered = threading.Event()
        started = threading.Event()

        @manager.module_mutation('test')
        def mutate(module_id):
            entered.set()
            return module_id

        def invoke():
            started.set()
            return mutate('transport-hysteria2')

        with ThreadPoolExecutor(max_workers=1) as pool:
            with manager.profile_mutation_lock:
                future = pool.submit(invoke)
                self.assertTrue(started.wait(1))
                self.assertFalse(entered.wait(.05))
            self.assertEqual(future.result(timeout=1), 'transport-hysteria2')

    def test_command_failure_details_stay_out_of_public_operation_state(self):
        with tempfile.TemporaryDirectory() as folder:
            action = Path(folder) / 'action.json'
            with patch.object(manager, 'ACTION_FILE', action), patch.object(manager.logger, 'error'):
                manager.write_action('module-install:transport-hysteria2', 'command output with test-only credential', state='failed')
            saved = json.loads(action.read_text(encoding='utf-8'))
            self.assertEqual(saved['message'], manager.PUBLIC_COMMAND_ERROR)
            self.assertNotIn('test-only credential', action.read_text(encoding='utf-8'))

    def test_port_conflict_stops_install_before_package_operations_and_is_public(self):
        for module in ('transport-hysteria2', 'transport-tuic'):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as folder:
                action = Path(folder) / 'action.json'
                with (
                    patch.object(manager, 'ACTION_FILE', action),
                    patch.object(manager, 'module_is_installed', return_value=False),
                    patch.object(manager, 'module_settings', return_value={'port': 8443}),
                    patch.object(manager, 'systemctl_active', return_value=False),
                    patch.object(manager, 'load_json', return_value={}),
                    patch.object(manager, 'manifest', return_value={'id': module, 'installable': True}),
                    patch.object(manager, 'run', return_value=subprocess.CompletedProcess([], 0, '*:8443', '')) as run,
                    patch.object(manager, 'call_module_script') as install,
                ):
                    with self.assertRaises(manager.HTTPException) as raised:
                        manager.install_module(module)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertIn('8443', raised.exception.detail)
                install.assert_not_called()
                run.assert_called_once_with('ss', '-Hlun', 'sport', '=', ':8443')
                self.assertEqual(json.loads(action.read_text(encoding='utf-8'))['message'], raised.exception.detail)

    def test_free_port_and_own_running_listener_are_allowed_but_probe_failure_is_not(self):
        module = 'transport-hysteria2'
        for active, config, output, code, fails in (
            (False, {}, '', 0, False),
            (True, {'inbounds': [{'type': 'hysteria2', 'listen_port': 18443}]}, '*:18443', 0, False),
            (False, {}, '', 1, True),
        ):
            with (
                self.subTest(active=active, code=code),
                patch.object(manager, 'load_json', return_value=config),
                patch.object(manager, 'systemctl_active', return_value=active),
                patch.object(manager, 'run', return_value=subprocess.CompletedProcess([], code, output, '')) as run,
            ):
                if fails:
                    with self.assertRaises(RuntimeError):
                        manager.check_quic_port(module, 18443)
                else:
                    manager.check_quic_port(module, 18443)
                if active:
                    run.assert_not_called()

    def test_conflicting_settings_do_not_overwrite_saved_settings_or_restart(self):
        with (
            patch.object(manager, 'module_settings', return_value={'port': 18443}),
            patch.object(manager, 'validate_settings', return_value={'port': 8443}),
            patch.object(manager, 'check_quic_port', side_effect=manager.ModulePortConflict('UDP-порт 8443 уже занят.')),
            patch.object(manager, 'atomic_json') as save,
            patch.object(manager, 'write_quic_runtime') as restart,
        ):
            with self.assertRaises(manager.HTTPException) as raised:
                manager.patch_module_settings('transport-hysteria2', manager.ModuleSettingsPatch(values={'port': 8443}))
        self.assertEqual(raised.exception.status_code, 409)
        save.assert_not_called()
        restart.assert_not_called()

    def test_retry_changes_runtime_port_without_losing_users_or_tls(self):
        source = (ROOT / 'protocol-images/mihomo/modules/transport-hysteria2/install.sh').read_text(encoding='utf-8')
        program = source.split('python3 - "$root/$MODULE/config.json" "$PORT" "$MODULE" <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
        for module in ('hysteria2', 'tuic'):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'config.json'
                config = {'inbounds': [{'type': module, 'listen_port': 8443, 'users': [{'name': 'existing', 'password': 'test-only'}],
                                        'tls': {'enabled': True, 'key_path': 'existing.key'}}], 'outbounds': [{'type': 'direct'}]}
                path.write_text(json.dumps(config), encoding='utf-8')
                result = subprocess.run([sys.executable, '-', str(path), '18443', module], input=program, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                config['inbounds'][0]['listen_port'] = 18443
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), config)
                self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_new_hysteria_port_does_not_override_saved_legacy_port(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(manager, 'SETTINGS_ROOT', Path(folder)),
            patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'),
        ):
            self.assertEqual(manager.module_settings('transport-hysteria2')['port'], 18443)
            (Path(folder) / 'transport-hysteria2.json').write_text('{"port":8443}', encoding='utf-8')
            self.assertEqual(manager.module_settings('transport-hysteria2')['port'], 8443)

    @unittest.skipUnless(BASH, 'Requires Bash')
    def test_api_sandbox_migration_restarts_once_and_normal_operations_keep_api_running(self):
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        functions = '\n'.join(re.search(r'(?ms)^' + name + r'\(\) \{.*?^\}', source).group()
                              for name in ('ensure_api_write_access', 'refresh_protocol_api_access'))
        # Run the actual migration against isolated paths and stub systemd/chown.
        for directory in ('wireguard', 'amnezia', 'swanctl'):
            functions = functions.replace('/etc/' + directory, '${TEST_ROOT}/' + directory)
        with tempfile.TemporaryDirectory() as folder:
            script = '''set -eu
TEST_ROOT=$(cd "$TEST_ROOT" && pwd)
APP_NAME=vps-control
DATA_DIR="$TEST_ROOT/data"
CONFIG_DIR="$TEST_ROOT/config"
ENV_FILE="$CONFIG_DIR/environment"
LEGACY_ENV_FILE="$TEST_ROOT/legacy.env"
SERVICE_FILE="$TEST_ROOT/api.service"
install() { mkdir -p "${@: -1}"; }
ln() { :; }
systemctl() { echo "SYSTEMCTL $*"; }
mkdir -p "$CONFIG_DIR"
echo TEST=1 > "$ENV_FILE"
printf 'EnvironmentFile=old\nReadWritePaths=old\n' > "$SERVICE_FILE"
''' + functions + '''
refresh_protocol_api_access
echo MIGRATED
refresh_protocol_api_access
echo UNCHANGED
'''
            result = subprocess.run([BASH, '-c', script], env={**os.environ, 'TEST_ROOT': folder}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('SYSTEMCTL restart vps-control-api.service'), 1)
        self.assertEqual(result.stdout.count('SYSTEMCTL daemon-reload'), 1)
        self.assertNotIn('SYSTEMCTL', result.stdout.split('MIGRATED')[1])
