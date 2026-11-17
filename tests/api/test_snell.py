"""Snell credentials, accounting and real native TCP/UDP forwarding."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import unittest
import yaml
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.api.support import manager, ROOT, free_port, wait_port
import snell_runtime as snell


class SnellTests(unittest.TestCase):
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
            settings = manager.validate_connection(snell.MODULE, {'mode': 'unshaped', 'udp': False, 'reuse': True})
            self.assertEqual(settings['mode'], 'unshaped')
            self.assertFalse(settings['udp'])
            self.assertTrue(settings['reuse'])
            with self.assertRaises(manager.HTTPException): manager.validate_connection(snell.MODULE, {'mode': 'unsafe-raw'})
        self.assertTrue({'udp', 'reuse'} <= manager.client_connection_keys(snell.MODULE))
        self.assertNotIn('mode', manager.client_connection_keys(snell.MODULE))

    def test_private_stats_are_directional_and_invalid_samples_remain_unknown(self):
        command = SimpleNamespace(returncode=0, stdout=json.dumps({'uploadTotal': 20, 'downloadTotal': 30, 'connections': []}))
        epoch = SimpleNamespace(returncode=0, stdout='a' * 32)
        api = SimpleNamespace(run=lambda *args: epoch if args[0] == 'systemctl' else command, systemctl_active=lambda _: True)
        sample = snell.stats(api, self.credential())
        self.assertEqual((sample['rx_bytes'], sample['tx_bytes'], sample['counter_epoch']), (20, 30, 'a' * 32))
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
            finally:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


class Echo(socketserver.BaseRequestHandler):
    def handle(self): self.request.sendall(self.request.recv(2048))


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
    def test_generated_config_forwards_tcp_udp_and_rejects_wrong_key(self):
        for mode in ('default', 'unshaped'):
            with self.subTest(mode=mode):
                self.forward(mode)

    def forward(self, mode):
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
        with native(config) as server:
            wait_port(port, server)
            proxy = yaml.safe_load('\n'.join(['proxies:', *snell.render(credential, 'Snell', '127.0.0.1')]))['proxies'][0]
            client = {'mixed-port': mixed, 'mode': 'rule', 'log-level': 'warning', 'proxies': [proxy], 'rules': ['MATCH,Snell']}
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
            proxy['psk'] = 'different-native-snell-key'
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
