"""Real gateway reload with a stalled WebSocket and another active tunnel."""
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

from tests.api.support import api, free_port, wait_port

CADDY = os.getenv('PRIVACY_CADDY_BIN') or shutil.which('caddy')


class TunnelServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), TunnelHandler)
        self.written = 0
        self.last_write = time.monotonic()


class TunnelHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.close_connection = True
        if self.path == '/new':
            self.send_response(200)
            self.send_header('Content-Length', '3')
            self.end_headers()
            self.wfile.write(b'new')
            return
        key = self.headers['Sec-WebSocket-Key']
        accept = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101)
        self.send_header('Connection', 'Upgrade')
        self.send_header('Upgrade', 'websocket')
        self.send_header('Sec-WebSocket-Accept', accept)
        self.end_headers()
        try:
            if self.path == '/echo':
                while data := self.rfile.read(4):
                    self.wfile.write(data)
                    self.wfile.flush()
            else:
                frame = b'\x82\x7e\x40\x00' + b'x' * 16384
                for _ in range(4096):
                    self.wfile.write(frame)
                    self.wfile.flush()
                    self.server.written += len(frame)
                    self.server.last_write = time.monotonic()
        except OSError:
            pass


@unittest.skipUnless(CADDY, 'Requires real Caddy')
class GatewayReloadTests(unittest.TestCase):
    def test_reload_with_backpressure_keeps_admin_and_healthy_tunnel_available(self):
        server = TunnelServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        admin_port, port = free_port(), free_port()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory(prefix='gateway-reload-') as folder:
            root = Path(folder)
            path = root / 'Caddyfile'

            def config(include_new=False):
                routes = [{'domain': 'reload.example', 'path': name, 'port': server.server_port,
                           'transport': 'websocket'} for name in ('/ws', '/echo', *(['/new'] if include_new else []))]
                rendered = api.cdn_security.render_routes(routes, {}, protected=False)
                return ('{\n admin 127.0.0.1:' + str(admin_port) + '\n}\n' + rendered.replace(
                    'reload.example {', 'http://127.0.0.1:' + str(port) + ' {'))

            path.write_text(config(), encoding='utf-8')
            with (root / 'caddy.log').open('w') as log:
                process = subprocess.Popen([CADDY, 'run', '--config', str(path), '--adapter', 'caddyfile'],
                                           stdout=log, stderr=log)
                clients = []
                try:
                    wait_port(admin_port, process)
                    wait_port(port, process)

                    def connect(name, stalled=False):
                        client = socket.socket()
                        clients.append(client)
                        if stalled:
                            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                        client.settimeout(3)
                        client.connect(('127.0.0.1', port))
                        client.sendall(('GET ' + name + ' HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                                        'Connection: Upgrade\r\nUpgrade: websocket\r\n'
                                        'Sec-WebSocket-Version: 13\r\n'
                                        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n').encode())
                        headers = b''
                        while not headers.endswith(b'\r\n\r\n'):
                            data = client.recv(1)
                            self.assertTrue(data, 'Gateway closed before WebSocket upgrade')
                            headers += data
                        self.assertIn(b'101', headers.split(b'\r\n')[0])
                        return client

                    healthy = connect('/echo')
                    healthy.sendall(b'ping')
                    self.assertEqual(healthy.recv(4), b'ping')
                    connect('/ws', stalled=True)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if server.written > 65536 and time.monotonic() - server.last_write > .2:
                            break
                        time.sleep(.02)
                    else:
                        self.fail('Could not create WebSocket backpressure')
                    path.write_text(config(include_new=True), encoding='utf-8')
                    # A changed config must complete while the stalled peer stays open.
                    result = subprocess.run([CADDY, 'reload', '--config', str(path), '--adapter', 'caddyfile'],
                                            capture_output=True, timeout=3)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                    with opener.open('http://127.0.0.1:' + str(admin_port) + '/config/', timeout=2) as response:
                        self.assertIn('http', json.load(response)['apps'])
                    with opener.open('http://127.0.0.1:' + str(port) + '/new', timeout=2) as response:
                        self.assertEqual(response.read(), b'new')
                    healthy.sendall(b'pong')
                    self.assertEqual(healthy.recv(4), b'pong')
                    with connect('/echo') as new_client:
                        new_client.sendall(b'next')
                        self.assertEqual(new_client.recv(4), b'next')
                finally:
                    for client in clients:
                        client.close()
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
