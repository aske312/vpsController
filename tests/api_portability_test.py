"""Isolated API contracts; never uses a deployed panel or system service."""
import importlib.util
import ipaddress
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("panel_test_api", Path(__file__).resolve().parents[1] / "api/main.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class PortabilityTests(unittest.TestCase):
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
