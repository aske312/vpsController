import base64
import json
import http.server
import os
import subprocess
import socket
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api, free_port, wait_port

ech = api.ech_settings


def public_config(name):
    name = name.encode()
    contents = b'\x01\x00\x20\x00\x20' + b'x' * 32 + b'\x00\x04\x00\x01\x00\x01\x00' + bytes([len(name)]) + name + b'\x00\x00'
    return b'\xfe\x0d' + len(contents).to_bytes(2, 'big') + contents


class EchTests(unittest.TestCase):
    def test_hostname_rejects_configuration_injection_and_ips(self):
        for name in ['evil.test\n}', 'https://a.test', 'a.test:443', '127.0.0.1', '*.test', '-a.test']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ech.hostname(name)

    def test_ech_endpoints_require_authentication(self):
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/application/ech?domain=cdn.example.com').status_code, 401)
        self.assertEqual(client.put('/api/application/ech', json={'domain': 'cdn.example.com'}).status_code, 401)

    def test_public_record_is_valid_config_list_and_does_not_read_private_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ech, 'STORAGE', Path(temp)):
            folder = Path(temp) / '1'; folder.mkdir()
            raw = public_config('panel.example.com')
            (folder / 'config.bin').write_bytes(raw)
            # No private key file is required to export DNS fields.
            state = {'domains': ['cdn.example.com'], 'public_name': 'panel.example.com'}
            record = ech.record('cdn.example.com', state)
            encoded = record['parameters'].split('ech="')[1].rstrip('"')
            self.assertEqual(base64.b64decode(encoded, validate=True), len(raw).to_bytes(2, 'big') + raw)
            self.assertEqual(record['target'], '.')
            self.assertIsNone(ech.record('other.example.com', state))

    def test_gateway_regeneration_preserves_one_ech_directive(self):
        state = {'domains': ['cdn.example.com'], 'public_name': 'panel.example.com'}
        text = '{\n servers { protocols h1 h2 }\n}\nexample.com { respond 200 }\n'
        first = ech.inject(text, state)
        self.assertEqual(ech.inject(first, state).count('ech panel.example.com'), 1)
        self.assertIn('example.com { respond 200 }', first)
        import gateway_config
        with patch.object(ech, 'settings', return_value=state):
            regenerated = gateway_config.render('{\n}\n{$SITE_ADDRESS} { respond 200 }', 'external', 8080, {'PUBLIC_DOMAIN': 'panel.example.com'})
        self.assertIn('ech panel.example.com', regenerated)

    def test_proxy_domain_cannot_receive_origin_ech_keys(self):
        with patch.object(ech.cdn_security, 'read_routes', return_value=[{'domain': 'cdn.example.com'}]), patch.object(ech.cdn_security, 'read_env', return_value={'PUBLIC_IP': '192.0.2.1'}), patch.object(ech.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('192.0.2.2', 0))]):
            with self.assertRaisesRegex(ValueError, 'CDN-провайдера'):
                ech.check_domain('cdn.example.com')

    def test_failed_reload_restores_gateway_and_does_not_publish_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); caddy = root / 'Caddyfile'; caddy.write_text('{\n}\n')
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if len(calls) == 2:
                    raise subprocess.CalledProcessError(1, args)
            with patch.object(ech, 'STATE', root / 'ech.json'), patch.object(ech.cdn_security, 'CADDY', caddy), patch.object(ech.cdn_security, 'SNIPPET', root / 'routes.caddy'), patch.object(ech, 'check_domain', return_value='cdn.example.com'), patch.object(ech.cdn_security, 'read_env', return_value={'PUBLIC_DOMAIN': 'panel.example.com'}), patch.object(ech, 'record', return_value={'domain': 'cdn.example.com'}), patch.object(ech.subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    ech.prepare('cdn.example.com')
                self.assertEqual(caddy.read_text(), '{\n}\n')
                self.assertFalse(ech.STATE.exists())
                self.assertEqual(len(calls), 3)

    def test_native_caddy_generated_public_config_is_exportable(self):
        binary = os.environ.get('PRIVACY_CADDY_BIN')
        if not binary:
            self.skipTest('PRIVACY_CADDY_BIN not configured')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); storage = root / 'storage'
            config = root / 'Caddyfile'
            config.write_text('{\n admin off\n storage file_system {\n root "' + storage.as_posix() + '"\n }\n ech outer.example.test\n}\nhttps://localhost:18443 {\n tls internal\n respond 200\n}\n')
            result = subprocess.run([binary, 'validate', '--config', str(config), '--adapter', 'caddyfile'], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            with patch.object(ech, 'STORAGE', storage / 'ech' / 'configs'):
                record = ech.record('inner.example.test', {'domains': ['inner.example.test'], 'public_name': 'outer.example.test'})
                self.assertIsNotNone(record)

    def test_mihomo_handshake_uses_generated_ech_config(self):
        caddy = os.environ.get('PRIVACY_CADDY_BIN')
        mihomo = os.environ.get('PRIVACY_MIHOMO_BIN')
        xray = os.environ.get('PRIVACY_XRAY_BIN')
        if not caddy or not mihomo or not xray:
            self.skipTest('Caddy, Xray and Mihomo test binaries not configured')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); port = free_port(); socks = free_port(); upstream = free_port()
            storage = root / 'storage'; access = root / 'access.json'
            conf = root / 'Caddyfile'
            conf.write_text('{\n admin off\n persist_config off\n auto_https disable_redirects\n skip_install_trust\n local_certs\n storage file_system {\n root "' + storage.as_posix() + '"\n }\n ech outer.example.test\n}\nhttps://inner.example.test:' + str(port) + ', https://outer.example.test:' + str(port) + ' {\n bind 127.0.0.1\n log {\n output file "' + access.as_posix() + '"\n format json\n }\n log_append ech {http.request.tls.ech}\n reverse_proxy 127.0.0.1:' + str(upstream) + '\n}\n')
            identity = '00000000-0000-4000-8000-000000000001'
            backend = root / 'xray.json'
            backend.write_text(json.dumps({'inbounds': [{'listen': '127.0.0.1', 'port': upstream, 'protocol': 'vless', 'settings': {'clients': [{'id': identity}], 'decryption': 'none'}, 'streamSettings': {'network': 'ws', 'wsSettings': {'path': '/probe'}}}], 'outbounds': [{'protocol': 'freedom'}]}))
            class Origin(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers(); self.wfile.write(b'ech-traffic-success')
                def log_message(self, *_):
                    pass
            origin = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Origin)
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            xray_process = subprocess.Popen([xray, 'run', '-c', str(backend)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with (root / 'caddy.log').open('w') as log:
                server = subprocess.Popen([caddy, 'run', '--config', str(conf), '--adapter', 'caddyfile'], stdout=log, stderr=log)
                client = None
                try:
                    wait_port(upstream, xray_process)
                    try:
                        wait_port(port, server)
                    except AssertionError:
                        self.fail((root / 'caddy.log').read_text())
                    with patch.object(ech, 'STORAGE', storage / 'ech' / 'configs'):
                        record = ech.record('inner.example.test', {'domains': ['inner.example.test'], 'public_name': 'outer.example.test'})
                    encoded = record['parameters'].split('ech="')[1].rstrip('"')
                    config = {'mixed-port': socks, 'bind-address': '127.0.0.1', 'mode': 'rule', 'proxies': [
                        {'name': 'ech-probe', 'type': 'vless', 'uuid': identity, 'server': '127.0.0.1', 'port': port, 'tls': True, 'network': 'ws',
                         'servername': 'inner.example.test', 'client-fingerprint': 'chrome', 'ws-opts': {'path': '/probe', 'headers': {'Host': 'inner.example.test'}},
                         'skip-cert-verify': True, 'ech-opts': {'enable': True, 'config': encoded}}
                    ], 'rules': ['MATCH,ech-probe']}
                    path = root / 'client.json'; path.write_text(json.dumps(config))
                    with (root / 'client.log').open('w') as client_log:
                        client = subprocess.Popen([mihomo, '-d', str(root / 'client'), '-f', str(path)], stdout=client_log, stderr=client_log)
                        wait_port(socks, client)
                        with socket.create_connection(('127.0.0.1', socks), timeout=5) as connection:
                            connection.sendall(f'GET http://127.0.0.1:{origin.server_port}/ HTTP/1.1\r\nHost: 127.0.0.1:{origin.server_port}\r\nConnection: close\r\n\r\n'.encode())
                            response = b''
                            while chunk := connection.recv(65536):
                                response += chunk
                        self.assertIn(b'ech-traffic-success', response)
                        client.terminate(); client.wait(timeout=10); client = None
                        deadline = time.monotonic() + 3
                        while (not access.exists() or not access.stat().st_size) and time.monotonic() < deadline:
                            time.sleep(.05)
                        rows = [json.loads(line) for line in access.read_text().splitlines()]
                        self.assertTrue(any(str(row.get('ech')).lower() == 'true' for row in rows), (rows, (root / 'client.log').read_text(), (root / 'caddy.log').read_text()))
                finally:
                    if client:
                        client.terminate(); client.wait(timeout=10)
                    server.terminate(); server.wait(timeout=10)
                    xray_process.terminate(); xray_process.wait(timeout=10)
                    origin.shutdown(); origin.server_close()


if __name__ == '__main__':
    unittest.main()
