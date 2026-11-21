"""Snell credentials, accounting and real native TCP/UDP forwarding."""
from contextlib import contextmanager, ExitStack
import json
import os
import re
import shutil
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import yaml
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.api.support import manager, ROOT, free_port, wait_port
import snell_runtime as snell


class SnellTests(unittest.TestCase):
    def test_tcp_echo_preserves_multiple_writes_on_one_connection(self):
        server = self.enterContext(socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo))
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        with socket.create_connection(server.server_address, timeout=2) as channel:
            for payload in (b'first-fragment', b'second-fragment'):
                channel.sendall(payload)
                self.assertEqual(exact(channel, len(payload)), payload)

    def test_system_service_launcher_needs_only_standard_library(self):
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'protocol-images/mihomo/snell_runtime.py')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Expected: serve INSTANCE', result.stderr)
        self.assertNotIn('ModuleNotFoundError', result.stderr)

    def credential(self, **changes):
        return {'instance_id': 'profile-phone', 'port': 33000, 'password': 'test-key-for-snell-v6', 'mode': 'default', **changes}

    def test_provision_and_revoke_own_only_the_allocated_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = SimpleNamespace(CONFIG_ROOT=root, CORE_HOME=root / 'home', profiles=lambda: [],
                                  module_settings=lambda _: {'port_start': 33000}, core_binary=lambda: Path('/core/mihomo'),
                                  free_module_port=Mock(return_value=33012), run=Mock(),
                                  port_allocation=SimpleNamespace(release=Mock()), shutil=SimpleNamespace(which=lambda _: None),
                                  atomic_json=lambda path, value: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(json.dumps(value))))
            first = snell.add(api, 'profile', 'phone', {'mode': 'unshaped'})
            self.assertEqual(first['port'], 33012)
            self.assertEqual(first['mode'], 'unshaped')
            path = root / 'snell/profile-phone.json'
            self.assertEqual(json.loads(path.read_text())['listeners'][0]['psk'], first['password'])
            api.run.assert_any_call('systemctl', 'enable', '--now', 'vps-control-mihomo-snell@profile-phone.service', check=True)
            other = root / 'snell/other.json'; other.write_text('{}')
            snell.remove(api, first)
            self.assertFalse(path.exists())
            self.assertTrue(other.exists())
            api.port_allocation.release.assert_called_with('mihomo:snell:profile-phone')
            api.run.assert_any_call('systemctl', 'disable', '--now', 'vps-control-mihomo-snell@profile-phone.service', check=True)

    def test_failed_native_validation_releases_port_before_transaction_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = Mock()
            api = SimpleNamespace(CONFIG_ROOT=root, CORE_HOME=root, profiles=lambda: [], module_settings=lambda _: {},
                                  core_binary=lambda: Path('/core/mihomo'), free_module_port=lambda *a, **kw: 33000,
                                  run=Mock(side_effect=RuntimeError('Invalid native configuration')),
                                  atomic_json=lambda *args: None, port_allocation=SimpleNamespace(release=release))
            with self.assertRaises(RuntimeError): snell.add(api, 'profile', 'phone', {})
            release.assert_called_once_with('mihomo:snell:profile-phone')
            self.assertEqual(api.run.call_count, 1)

    def test_recovery_snapshot_includes_snell_and_preserves_shadowsocks(self):
        import profile_recovery
        with patch.object(manager.ss_runtime, 'snapshot', side_effect=[{'ss': (True, 'enabled', None)}, {'snell': (False, 'disabled', None)}]) as snapshot:
            result = profile_recovery.instance_snapshot(manager, {'transport-shadowsocks', snell.MODULE})
        self.assertEqual(set(result), {'ss', 'snell'})
        self.assertEqual(snapshot.call_args.kwargs['prefix'], snell.PREFIX)
        self.assertEqual(snapshot.call_args.kwargs['directory'], manager.CONFIG_ROOT / 'snell')

    def test_recovery_uses_the_same_unit_boundary_as_the_shadowsocks_snapshot(self):
        import profile_recovery
        with patch.object(manager.ss_runtime, 'UNIT', re.compile(r'disposable-ss@[A-Za-z0-9_-]+\.service')):
            self.assertTrue(profile_recovery.valid_instance_unit(manager, {'transport-shadowsocks'}, 'disposable-ss@old.service'))
            self.assertFalse(profile_recovery.valid_instance_unit(manager, {snell.MODULE}, 'disposable-ss@old.service'))
            self.assertTrue(profile_recovery.valid_instance_unit(manager, {snell.MODULE}, 'vps-control-mihomo-snell@phone.service'))
            self.assertFalse(profile_recovery.valid_instance_unit(manager, {snell.MODULE}, 'vps-control-mihomo-snell@../bad.service'))

    def test_native_config_and_export_share_credential_and_safe_modes(self):
        for mode in ('default', 'unshaped'):
            credential = self.credential(mode=mode)
            config = snell.configuration('profile-phone', credential)
            listener = config['listeners'][0]
            self.assertEqual((listener['version'], listener['psk'], listener['mode']), (6, credential['password'], mode))
            exported = '\n'.join(snell.render(credential, 'Phone', 'vpn.example'))
            self.assertIn('version: 6', exported)
            self.assertIn(json.dumps(credential['password']), exported)
            self.assertIn(json.dumps(mode), exported)
        for changes in ({'mode': 'unsafe-raw'}, {'password': 'short'}, {'port': True}):
            with self.assertRaises(ValueError): snell.configuration('profile-phone', self.credential(**changes))
        with self.assertRaises(ValueError): snell.configuration('../other', self.credential())

    def test_component_schema_validates_modes_and_keeps_client_options_independent(self):
        with patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'), patch.object(manager, 'module_is_installed', return_value=True), patch.object(manager, 'module_is_ready', return_value=True):
            settings = manager.validate_connection(snell.MODULE, {'mode': 'unshaped', 'udp': False, 'reuse': True,
                                                                 'ip_version': 'ipv6-prefer', 'tfo': True, 'mptcp': True})
            self.assertEqual(settings['mode'], 'unshaped')
            self.assertFalse(settings['udp'])
            self.assertTrue(settings['reuse'])
            with self.assertRaises(manager.HTTPException): manager.validate_connection(snell.MODULE, {'mode': 'unsafe-raw'})
        self.assertTrue({'udp', 'reuse', 'ip_version', 'tfo', 'mptcp'} <= manager.client_connection_keys(snell.MODULE))
        self.assertNotIn('mode', manager.client_connection_keys(snell.MODULE))
        exported = '\n'.join(manager.render_proxy(snell.MODULE, {**self.credential(), **settings}, 'Snell'))
        for option in ('ip-version: "ipv6-prefer"', 'tfo: true', 'mptcp: true'):
            self.assertIn(option, exported)

    def test_private_stats_are_directional_and_invalid_samples_remain_unknown(self):
        command = SimpleNamespace(returncode=0, stdout=json.dumps({'uploadTotal': 20, 'downloadTotal': 30, 'connections': []}))
        epoch = SimpleNamespace(returncode=0, stdout='a' * 32)
        api = SimpleNamespace(run=lambda *args: epoch if args[0] == 'systemctl' else command, systemctl_active=lambda _: True)
        sample = snell.stats(api, self.credential())
        self.assertEqual((sample['rx_bytes'], sample['tx_bytes'], sample['counter_epoch']), (20, 30, 'a' * 32))
        self.assertIs(type(sample['traffic_observed_at']), int)
        for connections, expected in ((None, 0), ([], 0), ([{'id': 'session'}], 1)):
            with self.subTest(connections=connections):
                command.stdout = json.dumps({'uploadTotal': 20, 'downloadTotal': 30, 'connections': connections})
                sample = snell.stats(api, self.credential())
                self.assertTrue(sample['stats_available'])
                self.assertTrue(sample['activity_available'])
                self.assertEqual(sample['active_connections'], expected)
                self.assertEqual(sample['active'], expected > 0)
        for invalid in ({'uploadTotal': 20, 'downloadTotal': 30},
                        {'uploadTotal': 20, 'downloadTotal': 30, 'connections': {}}):
            command.stdout = json.dumps(invalid)
            self.assertFalse(snell.stats(api, self.credential())['stats_available'])
        command.stdout = '{"uploadTotal": -1, "downloadTotal": 3, "connections": []}'
        self.assertFalse(snell.stats(api, self.credential())['stats_available'])

    def test_component_is_listed_and_limits_cover_wire_tcp_and_upstream_udp(self):
        with patch.object(manager, 'module_payload', side_effect=lambda identity: {'id': identity}):
            self.assertIn({'id': snell.MODULE}, manager.get_modules()['items'])
        from tests.api.test_subscription_limits import profile
        item = profile()
        item['connections'] = [{'id': 'snell', 'component': snell.MODULE, 'credential': self.credential()}]
        policy = manager.subscription_limits.plan([item])
        unit = policy['profiles'][0]['shadowsocks'][0]['unit']
        self.assertEqual(unit, 'vps-control-mihomo-snell@profile-phone.service')
        rules = manager.subscription_limits.nft_rules(policy, {}, {unit: {'level': 2, 'path': '/system.slice/snell.service'}})
        self.assertIn('tcp sport != 33000', rules)
        self.assertIn('meta l4proto udp', rules)


@contextmanager
def native(config):
    with tempfile.TemporaryDirectory(prefix='snell-core-') as directory:
        root = Path(directory)
        path = root / 'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        binary = os.environ['PRIVACY_MIHOMO_BIN']
        subprocess.run([binary, '-t', '-d', directory, '-f', str(path)], check=True, capture_output=True, timeout=20)
        with (root / 'core.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([binary, '-d', directory, '-f', str(path)], stdout=log, stderr=log)
            try:
                yield process
            except BaseException:
                log.flush()
                print((root / 'core.log').read_text(encoding='utf-8', errors='replace')[-3000:])
                raise
            finally:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        while data := self.request.recv(65536):
            self.request.sendall(data)


class UdpEcho(socketserver.BaseRequestHandler):
    def handle(self): self.request[1].sendto(self.request[0], self.client_address)


def exact(channel, count):
    result = b''
    while len(result) < count:
        chunk = channel.recv(count - len(result))
        if not chunk: raise AssertionError('Unexpected end of SOCKS response')
        result += chunk
    return result


@unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires native Mihomo 1.19.34+')
class SnellNativeTests(unittest.TestCase):
    def test_snell_tcp_and_udp_inside_vless_tls_websocket_transport(self):
        self.forward('default', carrier=True)

    def test_generated_config_forwards_tcp_udp_and_rejects_wrong_key(self):
        for mode in ('default', 'unshaped'):
            with self.subTest(mode=mode):
                self.forward(mode)

    def forward(self, mode, carrier=False):
        tcp = self.enterContext(socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo))
        udp = self.enterContext(socketserver.ThreadingUDPServer(('127.0.0.1', 0), UdpEcho))
        for server in (tcp, udp):
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
            self.addCleanup(server.shutdown)
        port, mixed = free_port(), free_port(udp=True)
        credential = {'instance_id': 'native-test', 'port': port, 'password': 'native-snell-v6-test-key', 'mode': mode}
        config = snell.configuration('native-test', credential)
        # Only the controller's filesystem path differs outside Linux/systemd.
        config.pop('external-controller-unix')
        config['listeners'][0]['listen'] = '127.0.0.1'
        outer_port = free_port()
        identity = '11111111-1111-4111-8111-111111111111'
        with native(config) as server, ExitStack() as stack:
            wait_port(port, server)
            if carrier:
                cert_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='snell-tls-')))
                certificate, key = cert_root / 'certificate.pem', cert_root / 'key.pem'
                subprocess.run([shutil.which('openssl') or 'D:/Git/usr/bin/openssl.exe', 'req', '-x509', '-newkey',
                                'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
                                '-keyout', str(key), '-out', str(certificate)], check=True, capture_output=True)
                outer = stack.enter_context(native({'listeners': [
                    {'name': 'carrier', 'type': 'vless', 'listen': '127.0.0.1', 'port': outer_port,
                     'users': [{'username': 'test', 'uuid': identity}], 'ws-path': '/snell-carrier',
                     'certificate': certificate.read_text(), 'private-key': key.read_text()}],
                    'rules': ['MATCH,DIRECT'], 'log-level': 'warning'}))
                wait_port(outer_port, outer)
            proxy = yaml.safe_load('\n'.join(['proxies:', *snell.render(credential, 'Snell', '127.0.0.1')]))['proxies'][0]
            client = {'mixed-port': mixed, 'mode': 'rule', 'log-level': 'warning', 'proxies': [proxy], 'rules': ['MATCH,Snell']}
            if carrier:
                import snell_delivery
                client['proxies'].append({'name': 'WebSocket carrier', 'type': 'vless', 'server': '127.0.0.1',
                                          'port': outer_port, 'uuid': identity, 'network': 'ws',
                                          'tls': True, 'skip-cert-verify': True,
                                          'ws-opts': {'path': '/snell-carrier'}})
                rows = [({'id': 'snell', 'component': 'transport-snell', 'device_id': 'test',
                          'settings': {'carrier_connection_id': 'outer'}}, 'Snell', None, None),
                        ({'id': 'outer', 'component': 'transport-reality', 'device_id': 'test',
                          'settings': {'route_mode': 'cdn'}}, None, 'WebSocket carrier', None)]
                client = yaml.safe_load(snell_delivery.apply(yaml.safe_dump(client), rows))
            with native(client) as process:
                wait_port(mixed, process)
                # SOCKS UDP association remains open while its datagram crosses Snell TCP.
                for command in (1, 3):
                    with socket.create_connection(('127.0.0.1', mixed), timeout=5) as control:
                        control.sendall(b'\x05\x01\x00')
                        self.assertEqual(exact(control, 2), b'\x05\x00')
                        destination = tcp.server_address[1] if command == 1 else 0
                        control.sendall(bytes([5, command, 0, 1]) + socket.inet_aton('127.0.0.1') + struct.pack('!H', destination))
                        reply = exact(control, 10)
                        self.assertEqual(reply[:2], b'\x05\x00')
                        payload = b'native-snell-v6-' + mode.encode()
                        if command == 1:
                            control.sendall(payload)
                            self.assertEqual(exact(control, len(payload)), payload)
                        else:
                            with socket.socket(type=socket.SOCK_DGRAM) as channel:
                                channel.settimeout(5)
                                packet = b'\x00\x00\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', udp.server_address[1]) + payload
                                channel.sendto(packet, ('127.0.0.1', struct.unpack('!H', reply[-2:])[0]))
                                self.assertTrue(channel.recv(2048).endswith(payload))
            client['proxies'][0]['psk'] = 'different-native-snell-key'
            with native(client) as process:
                wait_port(mixed, process)
                with socket.create_connection(('127.0.0.1', mixed), timeout=3) as control:
                    control.sendall(b'\x05\x01\x00')
                    exact(control, 2)
                    control.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', tcp.server_address[1]))
                    reply = exact(control, 10)
                    if reply[1] == 0:
                        control.sendall(b'unauthorized')
                        try: received = control.recv(2048)
                        except (OSError, TimeoutError): received = b''
                        self.assertNotEqual(received, b'unauthorized')
