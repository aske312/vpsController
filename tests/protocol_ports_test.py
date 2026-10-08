from contextlib import contextmanager
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from tests.api_portability_test import api
import protocol_ports as ports
from fastapi.testclient import TestClient


class ProtocolPortsTests(unittest.TestCase):
    def test_transport_binding_and_namespace(self):
        for protocol, transport in ports.PROTOCOLS.items():
            with self.subTest(protocol=protocol), socket.socket(socket.AF_INET, socket.SOCK_DGRAM if transport == 'udp' else socket.SOCK_STREAM) as occupied:
                occupied.bind(('0.0.0.0', 0))
                if transport == 'tcp': occupied.listen(1)
                with self.assertRaises(OSError): ports.bind_port(occupied.getsockname()[1], transport)
            template = ports.unit_template(protocol, '/opt/vps-control', '/var/lib/vps-control/protocol-ports/'+protocol)
            self.assertIn('Type=notify', template)
            self.assertIn(f'PartOf=vps-control-{protocol}.service', template)
            rule = ports.rule_args(protocol, 39761, 8443, '192.0.2.1')
            self.assertEqual(rule[1], transport)
            self.assertIn(f'vps-control-{protocol}-port-39761', rule)

    def test_conflicts_primary_shared_and_cleanup(self):
        result = type('Result', (), {'returncode': 0, 'stdout': ''})()
        with tempfile.TemporaryDirectory() as directory:
            manager = ports.ProtocolPorts('tuic', 8444, '192.0.2.1', directory)
            self.assertEqual(manager.status(8444)['status'], 'protocol')
            with patch.object(manager, 'status', return_value={'status': 'occupied', 'detail': 'busy'}):
                with self.assertRaises(ports.PortError):
                    with manager.reserve(39761): pass
                self.assertFalse(manager.path(39761).exists())
            with patch.object(manager, 'status', return_value={'status': 'available'}), patch.object(ports, 'command', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'failure'):
                    with manager.reserve(39761): raise RuntimeError('failure')
                self.assertFalse(manager.path(39761).exists())
                with manager.reserve(39761): pass
            with patch.object(ports.platform, 'system', return_value='Linux'), patch.object(ports, 'command', return_value=result):
                self.assertEqual(manager.status(39761)['status'], 'protocol')
                other = ports.ProtocolPorts('tuic', 9444, '192.0.2.1', directory)
                self.assertEqual(other.status(39761)['status'], 'occupied')
                with self.assertRaises(ports.PortError): other.release(39761)
                manager.release(39761)
                self.assertFalse(manager.path(39761).exists())

    def test_nat_conflicts_include_awg_but_not_other_transport(self):
        for protocol, conflict in [('tuic', True), ('hysteria2', True), ('xray', False)]:
            result = type('Result', (), {'stdout': '-A PREROUTING -p udp --dport 39761 -m comment --comment vps-control-awg-port-39761 -j REDIRECT --to-ports 51822'})()
            with patch.object(ports, 'command', return_value=result):
                self.assertEqual(ports.nat_conflict(protocol, 39761, '192.0.2.1'), conflict)

    def test_nat_rule_listing_does_not_use_incompatible_numeric_flag(self):
        result = type('Result', (), {'stdout': ''})()
        import awg_ports
        for module, args in [(ports, ('tuic', 39761, '192.0.2.1')), (awg_ports, (39761, '192.0.2.1'))]:
            with patch.object(module, 'command', return_value=result) as command:
                self.assertFalse(module.nat_conflict(*args))
                command.assert_called_once_with('iptables', '-w', '5', '-t', 'nat', '-S')

    def test_auth_bounds_and_refcounts(self):
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/clients/server-port?protocol=xray&port=443').status_code, 401)
        with patch.object(api, 'ADMIN_USER', 'test'), patch.object(api, 'ADMIN_PASSWORD', 'secret'):
            for port in (0, 65536):
                self.assertEqual(client.get(f'/api/clients/server-port?protocol=xray&port={port}', auth=('test', 'secret')).status_code, 422)
        item = {'protocol': 'tuic', 'server_port': 39761, 'server_target_port': 8444}
        with patch.object(api, 'protocol_port_manager') as factory:
            api.release_client_port(item, [item.copy()])
            factory.assert_not_called()
            api.release_client_port(item, [])
            factory.return_value.release.assert_called_once_with(39761)

    def test_custom_ports_exported_without_changing_primary(self):
        valid = type('Result', (), {'returncode': 0, 'stderr': ''})()
        @contextmanager
        def reserved(port): yield port
        for protocol in ports.PROTOCOLS:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config, settings, users = root/'config.json', root/'settings.json', root/'users.json'
                original = {'inbounds': [{'type': 'tuic', 'listen_port': 8444, 'users': [{'name': 'old'}]}]} if protocol == 'tuic' else {'inbounds': [{'protocol': 'vless', 'port': 8445, 'settings': {'clients': [{'id': 'old'}]}, 'streamSettings': {'realitySettings': {'serverNames': ['example.com']}}}]}
                config.write_text(json.dumps(original))
                settings.write_text(json.dumps({'port': 8443 if protocol == 'hysteria2' else 8444 if protocol == 'tuic' else 8445, 'domain': 'example.com', 'server_name': 'example.com', 'path': '/xhttp', 'password': 'public', 'short_id': 'ab'}))
                users.write_text('{"old":"old"}')
                primary = json.loads(settings.read_text())['port']
                (root/'server.crt').write_text('certificate')
                def run(*args, **kwargs): return 'enabled' if args[:2] == ('systemctl', 'is-enabled') else 'SHA256 Fingerprint=AB'
                with patch.multiple(api, PUBLIC_IP='192.0.2.1', DATA_DIR=root, CLIENTS_FILE=root/'clients.json', HYSTERIA2_DIR=root, HYSTERIA2_SETTINGS=settings, HYSTERIA2_USERS=users, TUIC_DIR=root, TUIC_SETTINGS=settings, TUIC_CONFIG=config, XRAY_DIR=root, XRAY_SETTINGS=settings, XRAY_CONFIG=config), patch.object(api, 'run', side_effect=run), patch.object(api.subprocess, 'run', return_value=valid), patch.object(api.Path, 'exists', return_value=True), patch.object(api, 'certificate_server_name', return_value='example.com'), patch.object(api.ProtocolPorts, 'reserve', side_effect=reserved) as reserve:
                    payload = api.ClientCreate(name='Port QA', protocol=protocol, settings=api.ClientSettings(server_port=39761, hysteria_format='sing-box'))
                    result = api.create_client(payload)
                    reserve.assert_called_once_with(39761)
                    self.assertEqual(result['profile']['endpoint'], 'example.com:39761' if protocol == 'hysteria2' else '192.0.2.1:39761')
                    exported = json.loads(result['config'])['outbounds'][0]
                    self.assertEqual(exported['settings']['vnext'][0]['port'] if protocol == 'xray' else exported['server_port'], 39761)
                    self.assertEqual(api.read_clients()[0]['server_port'], 39761)
                    if protocol != 'hysteria2':
                        self.assertEqual(json.loads(config.read_text())['inbounds'][0].get('listen_port', json.loads(config.read_text())['inbounds'][0].get('port')), original['inbounds'][0].get('listen_port', original['inbounds'][0].get('port')))
                    self.assertEqual(json.loads(settings.read_text())['port'], primary)

    def test_busy_port_rejected_before_identity_or_config_mutation(self):
        for protocol in ports.PROTOCOLS:
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root/'config.json'
                config.write_text(json.dumps({'inbounds': [{'type': 'tuic', 'protocol': 'vless', 'port': 8445, 'users': [], 'settings': {'clients': []}, 'streamSettings': {'realitySettings': {'serverNames': ['example.com']}}}]}))
                settings = root/'settings.json'; settings.write_text('{"port":8445,"server_name":"example.com"}')
                with patch.multiple(api, PUBLIC_IP='192.0.2.1', DATA_DIR=root, CLIENTS_FILE=root/'clients.json', HYSTERIA2_SETTINGS=settings, HYSTERIA2_USERS=root/'users.json', TUIC_CONFIG=config, TUIC_SETTINGS=settings, XRAY_CONFIG=config, XRAY_SETTINGS=settings), patch.object(api, 'run', return_value='enabled'), patch.object(api.Path, 'exists', return_value=True), patch.object(api.ProtocolPorts, 'status', return_value={'status': 'occupied', 'detail': 'busy'}):
                    before = config.read_bytes()
                    with self.assertRaises(api.HTTPException) as error:
                        api.create_client(api.ClientCreate(name='Port QA', protocol=protocol, settings=api.ClientSettings(server_port=22)))
                    self.assertEqual(error.exception.status_code, 409)
                    self.assertEqual(config.read_bytes(), before)


if __name__ == '__main__': unittest.main()
