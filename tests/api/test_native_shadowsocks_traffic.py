"""Native Direct exports must reach the listener on both advertised IP families."""
import base64
import json
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from tests.api.support import api, free_port, wait_port


@unittest.skipUnless(shutil.which('ss-server') and shutil.which('ss-local'), 'Requires real Shadowsocks server and client')
class NativeShadowsocksTrafficTests(unittest.TestCase):
    def test_exported_ipv4_client_reaches_dual_stack_listener(self):
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                while value := self.request.recv(4096):
                    self.request.sendall(value)

        with tempfile.TemporaryDirectory() as folder, socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo) as echo:
            root = Path(folder)
            threading.Thread(target=lambda: echo.serve_forever(poll_interval=.02), daemon=True).start()
            server_port, local_port = free_port(udp=True), free_port()
            def command(*args, **_):
                return 'loaded' if args[1] == 'show' else 'active'
            with patch.object(api, 'SHADOWSOCKS_CONFIG_DIR', root), patch.object(api, 'run', side_effect=command), \
                    patch.object(api, 'read_clients', return_value=[]), patch.object(api, 'write_clients'), \
                    patch.object(api, 'new_client_endpoint', return_value='127.0.0.1'), \
                    patch.object(api, 'shadowsocks_firewall'), patch.object(api.port_allocation, 'allocate', return_value=server_port), \
                    patch.object(api.port_allocation, 'release'):
                exported = api.create_shadowsocks_client(api.ClientCreate(name='IPv4 export', protocol='shadowsocks'), 'fixture', 'fixture')
            uri = urlsplit(exported['config'])
            method, password = base64.urlsafe_b64decode(uri.username + '=' * (-len(uri.username) % 4)).decode().split(':', 1)
            client_config = root / 'client.json'
            client_config.write_text(json.dumps({'server': uri.hostname, 'server_port': uri.port,
                'local_address': '127.0.0.1', 'local_port': local_port, 'method': method, 'password': password}))
            processes = []
            try:
                server = subprocess.Popen(['ss-server', '-c', str(root / 'fixture.json')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(server)
                wait_port(server_port, server)
                with socket.create_connection(('::1', server_port), timeout=2):
                    pass
                client = subprocess.Popen(['ss-local', '-c', str(client_config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(client)
                wait_port(local_port, client)
                with socket.create_connection(('127.0.0.1', local_port), timeout=3) as connection:
                    connection.settimeout(3)
                    stream = connection.makefile('rb')
                    self.addCleanup(stream.close)
                    connection.sendall(b'\x05\x01\x00')
                    self.assertEqual(stream.read(2), b'\x05\x00')
                    connection.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('127.0.0.1') + echo.server_address[1].to_bytes(2, 'big'))
                    header = stream.read(4)
                    self.assertEqual(header[:2], b'\x05\x00')
                    self.assertEqual(header[3], 1)
                    self.assertEqual(len(stream.read(6)), 6)
                    payload = b'isolated-native-shadowsocks-traffic'
                    connection.sendall(payload)
                    self.assertEqual(stream.read(len(payload)), payload)
            finally:
                for process in reversed(processes):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
                echo.shutdown()
