"""Isolated API contracts; never uses a deployed panel or system service."""
import importlib.util
import ipaddress
import json
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("panel_test_api", Path(__file__).resolve().parents[1] / "api/main.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class PortabilityTests(unittest.TestCase):
    def test_application_task_is_published_before_systemd_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            action_file = root / 'application-action.json'

            def launch(command, **_kwargs):
                published = json.loads(action_file.read_text(encoding='utf-8'))
                self.assertEqual(published['action'], 'integrity-check')
                self.assertEqual(published['state'], 'activating')
                self.assertIn(f"--unit={published['unit'].removesuffix('.service')}", command)
                return type('Result', (), {'returncode': 0, 'stderr': ''})()

            with patch.multiple(api, DATA_DIR=root, ACTION_FILE=action_file), \
                 patch.object(api, 'run', return_value='inactive'), \
                 patch.object(api.subprocess, 'run', side_effect=launch):
                action = api.start_application_task(
                    'vps-control-test', 'integrity-check', ['/bin/true'],
                    'Starting', 'Unable to start',
                )

            self.assertEqual(action['state'], 'activating')
            self.assertEqual(json.loads(action_file.read_text(encoding='utf-8')), action)

    def test_application_task_records_launcher_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            action_file = root / 'application-action.json'
            failed = type('Result', (), {'returncode': 1, 'stderr': 'launcher failed'})()
            with patch.multiple(api, DATA_DIR=root, ACTION_FILE=action_file), \
                 patch.object(api, 'run', return_value='inactive'), \
                 patch.object(api.subprocess, 'run', return_value=failed):
                with self.assertRaises(api.HTTPException) as raised:
                    api.start_application_task(
                        'vps-control-test', 'integrity-check', ['/bin/false'],
                        'Starting', 'Unable to start',
                    )

            self.assertEqual(raised.exception.status_code, 500)
            recorded = json.loads(action_file.read_text(encoding='utf-8'))
            self.assertEqual(recorded['state'], 'failed')
            self.assertEqual(recorded['result'], 'failed')
            self.assertEqual(recorded['message'], 'launcher failed')

    def test_all_management_routes_require_authentication(self):
        client = TestClient(api.app)
        count = 0
        for route in api.app.routes:
            if not route.path.startswith('/api/') or route.path in ('/api/health', '/api/auth/status'):
                continue
            path = route.path.replace('{protocol}', 'awg').replace('{image_id}', 'awg').replace('{service_id}', 'ssh').replace('{client_id}', 'missing')
            for method in route.methods:
                with self.subTest(method=method, path=path):
                    self.assertEqual(client.request(method, path, json={}).status_code, 401)
                    count += 1
        self.assertGreater(count, 20)

    def test_ssh_service_without_socket(self):
        for state in ('loaded', 'not-found', ''):
            for action in ('start', 'restart'):
                calls = []
                def run(*args, **kwargs):
                    calls.append(args)
                    return state if 'show' in args else ''
                with patch.object(api, 'run', side_effect=run), patch.object(api, 'service_details', return_value={}):
                    api.manage_service('ssh', api.ServiceAction(action=action))
                units = ('ssh.socket', 'ssh.service') if state == 'loaded' else ('ssh.service',)
                self.assertIn(('systemctl', action, *units), calls)

    def test_metrics_history_endpoint_returns_persisted_server_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            store = api.MetricsHistory(Path(directory) / 'history.sqlite3')
            now = int(time.time() // 3) * 3
            store.record({
                'cpu_percent': 37,
                'memory_total': 100,
                'memory_available': 40,
                'disk_total': 1000,
                'disk_available': 750,
                'network_rx': 10,
                'network_tx': 20,
                'uptime_s': 100,
            }, now)
            api.app.dependency_overrides[api.require_token] = lambda: None
            self.addCleanup(api.app.dependency_overrides.clear)
            with patch.object(api, 'metrics_history_store', store):
                response = TestClient(api.app).get('/api/metrics/history?period=live')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['period'], 'live')
            self.assertEqual(response.json()['resolution_s'], 3)
            self.assertTrue(any(point['cpu_percent'] == 37 for point in response.json()['points']))

    def test_client_create_delete_isolated_for_both_protocols(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs = {protocol: root / (protocol + '.conf') for protocol in ('wg', 'awg')}
            for config in configs.values():
                config.write_text('[Interface]\nPrivateKey = server-key\n', encoding='utf-8')
            with patch.multiple(api, DATA_DIR=root, CLIENTS_FILE=root / 'clients.json', WG_CONFIG=configs['wg'], AWG_CONFIG=configs['awg'], PUBLIC_IP='192.0.2.1'), \
                 patch.object(api, 'key', return_value='test-key'), \
                 patch.object(api, 'run', return_value='server-public-key'), \
                 patch.object(api, 'run_with_input'):
                for protocol in ('wg', 'awg'):
                    with self.subTest(protocol=protocol):
                        created = api.create_client(api.ClientCreate(name='QA client', protocol=protocol))
                        self.assertIn('Endpoint = 192.0.2.1:', created['config'])
                        self.assertEqual('Jc = ' in created['config'], protocol == 'awg')
                        clients = api.read_clients()
                        self.assertEqual(len(clients), 1)
                        self.assertIn(ipaddress.ip_interface(clients[0]['address']).ip, api.WG_SUBNET if protocol == 'wg' else api.AWG_SUBNET)
                        self.assertIn(created['id'], configs[protocol].read_text())
                        api.delete_client(created['id'])
                        self.assertEqual(api.read_clients(), [])
                        self.assertNotIn('[Peer]', configs[protocol].read_text())


if __name__ == '__main__':
    unittest.main()
