from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.api_portability_test import api
import awg_ports as ports
from fastapi.testclient import TestClient


class AwgPortsTests(unittest.TestCase):
    def manager(self, directory):
        return ports.AwgPorts(51822, 'awg0', '/opt/vps-control', '192.0.2.1', Path(directory))

    def test_actual_udp_occupation_and_tcp_is_not_a_conflict(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ports.platform, 'system', return_value='Linux'), patch.object(ports, 'nat_conflict', return_value=False):
            manager = self.manager(directory)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'): sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                self.assertEqual(manager.status(port)['status'], 'occupied')
            # TCP ephemeral allocation may coincide with an unrelated existing
            # UDP socket. Find a TCP-only candidate rather than assuming it.
            for _ in range(20):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(('127.0.0.1', 0)); sock.listen(); port = sock.getsockname()[1]
                    if manager.status(port)['status'] == 'available': break
            else:
                self.fail('No TCP-only port found for protocol-isolation check')

    def test_nat_ranges_and_protocol_isolation(self):
        cases = [('-A PREROUTING -p udp --dport 39000:40000 -j REDIRECT --to-ports 1234', True),
                 ('-A PREROUTING -p tcp --dport 39761 -j REDIRECT --to-ports 1234', False),
                 ('-A PREROUTING -p udp -m multiport --dports 123,39761 -j REDIRECT --to-ports 1234', True),
                 ('-A PREROUTING -p udp --dport 39761 -m comment --comment vps-control-awg-port-39761 -j REDIRECT --to-ports 51822', False)]
        for output, expected in cases:
            with patch.object(ports, 'command', return_value=subprocess.CompletedProcess([], 0, output, '')):
                self.assertEqual(ports.nat_conflict(39761, '192.0.2.1'), expected)

    def test_reservation_rolls_back_only_new_owned_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            with patch.object(manager, 'status', return_value={'status': 'available'}), patch.object(ports, 'command') as commands:
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    with manager.reserve(39761):
                        self.assertTrue(manager.owned(39761))
                        raise RuntimeError('failed')
                self.assertFalse(manager.path(39761).exists())
                self.assertIn(('systemctl', 'disable', '--now', manager.unit(39761)), [call.args for call in commands.call_args_list])
            with patch.object(manager, 'status', return_value={'status': 'awg'}), patch.object(manager, 'release') as release:
                with self.assertRaises(RuntimeError):
                    with manager.reserve(39761): raise RuntimeError()
                release.assert_not_called()

    def test_foreign_unit_not_overwritten_and_random_skips_conflicts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ports.platform, 'system', return_value='Linux'), patch.object(ports, 'nat_conflict', return_value=False), patch.object(ports, 'bind_port', return_value=[]):
            manager = self.manager(directory)
            foreign = manager.path(39761)
            foreign.write_text('foreign service')
            with self.assertRaises(ports.PortError):
                with manager.reserve(39761): pass
            self.assertEqual(foreign.read_text(), 'foreign service')
            with patch.object(ports.secrets, 'randbelow', side_effect=[19761, 27283]):
                self.assertEqual(manager.random_port(), 47283)

    def test_api_conflict_has_no_peer_or_file_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'awg.conf'; config.write_text('[Interface]\n')
            manager = Mock()
            manager.reserve.side_effect = ports.PortError('AWG UDP port 39761: занят')
            with patch.multiple(api, AWG_CONFIG=config, DATA_DIR=Path(directory), CLIENTS_FILE=Path(directory)/'clients.json'), patch.object(api, 'awg_port_manager', return_value=manager), patch.object(api, 'key') as keys, patch.object(api, 'append_peer') as append:
                with self.assertRaises(api.HTTPException) as error:
                    api.create_client(api.ClientCreate(name='QA conflict', protocol='awg', settings=api.ClientSettings(awg_port=39761)))
                self.assertEqual(error.exception.status_code, 409)
                keys.assert_not_called(); append.assert_not_called()
                self.assertEqual(api.read_clients(), [])

    def test_port_http_api_requires_auth_and_rejects_bad_ranges(self):
        with patch.object(api, 'ADMIN_PASSWORD', 'test-admin-password'), patch.object(api, 'awg_port_manager') as manager:
            with TestClient(api.app) as client:
                self.assertEqual(client.get('/api/clients/awg-port?port=39761').status_code, 401)
                manager.assert_not_called()
                api.app.dependency_overrides[api.require_token] = lambda: None
                try:
                    for port in (0, 65536):
                        self.assertEqual(client.get(f'/api/clients/awg-port?port={port}').status_code, 422)
                    manager.assert_not_called()
                finally:
                    api.app.dependency_overrides.clear()

    def test_api_port_export_sharing_random_and_last_client_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = root/'awg.conf'; config.write_text('[Interface]\n')
            manager = Mock(); manager.random_port.return_value = 47283
            @contextmanager
            def reserve(port): yield port
            manager.reserve.side_effect = reserve
            with patch.multiple(api, AWG_CONFIG=config, DATA_DIR=root, CLIENTS_FILE=root/'clients.json', PUBLIC_IP='192.0.2.1'), patch.object(api, 'awg_port_manager', return_value=manager), patch.object(api, 'key', return_value='key'), patch.object(api, 'run', return_value='public'), patch.object(api, 'run_with_input'):
                created = [api.create_client(api.ClientCreate(name='QA ports', protocol='awg', settings=api.ClientSettings(awg_port=39761))) for _ in range(2)]
                self.assertIn('Endpoint = 192.0.2.1:39761', created[0]['config'])
                api.delete_client(created[0]['id']); manager.release.assert_not_called()
                api.delete_client(created[1]['id']); manager.release.assert_called_once_with(39761)
                result = api.create_client(api.ClientCreate(name='QA random', protocol='awg', settings=api.ClientSettings(awg_port_random=True)))
                self.assertIn('Endpoint = 192.0.2.1:47283', result['config'])
                api.delete_client(result['id'])

    def test_api_peer_failure_rolls_back_config_and_random_endpoint_is_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = root/'awg.conf'; config.write_text('[Interface]\n')
            manager = Mock(); manager.random_port.return_value = 39761; manager.status.return_value = {'port': 39761, 'status': 'available'}
            @contextmanager
            def reserve(port): yield port
            manager.reserve.side_effect = reserve
            with patch.multiple(api, AWG_CONFIG=config, DATA_DIR=root, CLIENTS_FILE=root/'clients.json'), patch.object(api, 'awg_port_manager', return_value=manager), patch.object(api, 'key', return_value='key'), patch.object(api, 'run', return_value='public'), patch.object(api, 'run_with_input', side_effect=RuntimeError('failed')):
                self.assertEqual(api.check_awg_port(random=True)['port'], 39761)
                manager.reserve.assert_not_called()
                with self.assertRaises(RuntimeError):
                    api.create_client(api.ClientCreate(name='QA fail', protocol='awg'))
                self.assertEqual(config.read_text(), '[Interface]\n')
                self.assertEqual(api.read_clients(), [])


if __name__ == '__main__': unittest.main()
