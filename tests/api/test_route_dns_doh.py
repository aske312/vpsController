"""Real TLS/DoH through an observable local proxy, without public DNS or TUN."""
import base64
import http.client
import http.server
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from tests.api.test_route_dns_runtime import DnsLab, core, query, question, response, read_exact


class DohHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        encoded = parse_qs(urlsplit(self.path).query)['dns'][0]
        self.answer(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))

    def do_POST(self):
        self.answer(self.rfile.read(int(self.headers['Content-Length'])))

    def answer(self, packet):
        name, _ = question(packet)
        self.server.queries.append(name)
        answer = response(packet, self.server.answer, self.server.rcode)
        self.send_response(200)
        self.send_header('Content-Type', 'application/dns-message')
        self.send_header('Content-Length', str(len(answer)))
        self.end_headers()
        # Mihomo races its two tunnel resolvers and cancels the losing request.
        try:
            self.wfile.write(answer)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class DohLab(DnsLab):
    def __enter__(self):
        super().__enter__()
        self.https = {}
        self.folder = tempfile.TemporaryDirectory(prefix='route-dns-tls-')
        try:
            self.start_https()
        except Exception:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def start_https(self):
        root = Path(self.folder.name)
        self.cert, key = root / 'ca.pem', root / 'key.pem'
        subprocess.run([os.environ['PRIVACY_OPENSSL_BIN'], 'req', '-x509', '-newkey', 'rsa:2048',
                        '-nodes', '-days', '1', '-subj', '/CN=route-dns-test',
                        '-addext', 'subjectAltName=IP:127.0.0.1,DNS:remote-dns.test,DNS:direct-dns.test',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-keyout', str(key), '-out', str(self.cert)],
                       check=True, capture_output=True, timeout=20)
        for name in ('direct', 'primary', 'backup'):
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), DohHandler)
            server.answer = self.servers[name].answer
            server.rcode, server.queries = 0, []
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert, key)
            context.set_alpn_protocols(['http/1.1'])
            server.socket = context.wrap_socket(server.socket, server_side=True)
            self.https[name] = server
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()

    def resolver(self, name, host='127.0.0.1'):
        if name in self.https:
            return f'https://{host}:{self.https[name].server_address[1]}/dns-query'
        return super().resolver(name, host)

    def configure(self, config):
        # This is the spelling accepted by Mihomo 1.19.31. Trust only the lab CA;
        # keep normal certificate and hostname verification enabled.
        config['tls'] = {'custom-certifactes': [self.cert.read_text()]}
        self.mixed_port = config['mixed-port']
        if getattr(self, 'fake_ip', False):
            config['dns'].update({'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16'})

    def __exit__(self, *args):
        for server in self.https.values():
            server.shutdown()
            server.server_close()
        self.folder.cleanup()
        return super().__exit__(*args)


class WebHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(b'local-route-ok')))
        self.end_headers()
        self.wfile.write(b'local-route-ok')


@unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN') and os.getenv('PRIVACY_OPENSSL_BIN'),
                     'Requires Mihomo and OpenSSL')
class RouteDnsDohTests(unittest.TestCase):
    def test_fake_ip_connections_resolve_through_selected_https_path(self):
        web = http.server.ThreadingHTTPServer(('127.0.0.1', 0), WebHandler)
        threading.Thread(target=web.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
        self.addCleanup(web.server_close)
        self.addCleanup(web.shutdown)
        with DohLab() as lab:
            lab.fake_ip = True
            for server in lab.https.values():
                server.answer = '127.0.0.1'
            with core(lab) as port:
                for domain in ('fake.direct.test', 'fake.proxy.test'):
                    with self.subTest(domain=domain):
                        address = query(port, domain)[1]
                        self.assertTrue(address.startswith('198.18.'))
                        with socket.create_connection(('127.0.0.1', lab.mixed_port), timeout=5) as client:
                            client.sendall(b'\x05\x01\x00')
                            self.assertEqual(read_exact(client, 2), b'\x05\x00')
                            client.sendall(b'\x05\x01\x00\x01' + socket.inet_aton(address) + struct.pack('!H', web.server_address[1]))
                            reply = read_exact(client, 10)
                            self.assertEqual(reply[1], 0)
                            client.sendall(b'GET / HTTP/1.0\r\nHost: local.test\r\n\r\n')
                            # A proxy may keep TCP open after the complete HTTP
                            # response. Verify HTTP framing rather than TCP EOF.
                            with http.client.HTTPResponse(client) as response:
                                response.begin()
                                self.assertEqual(response.status, 200)
                                self.assertEqual(response.read(), b'local-route-ok')
                        if domain == 'fake.direct.test':
                            self.assertIn(domain, lab.https['direct'].queries)
                        else:
                            # Fake IP preserves the domain for proxy protocols
                            # with remote resolution; no client DNS is needed.
                            self.assertIn((domain, web.server_address[1]), lab.proxy.tcp_requests)
                self.assertNotIn('fake.proxy.test', lab.https['direct'].queries)
                self.assertNotIn('fake.direct.test', lab.https['primary'].queries)
                self.assertNotIn('fake.direct.test', lab.https['backup'].queries)
                for server in lab.servers.values():
                    self.assertNotIn('fake.proxy.test', server.queries)

    def test_mihomo_https_paths_fallback_and_tunnel_failure(self):
        with DohLab() as lab:
            with core(lab) as port:
                self.assertEqual(query(port, 'secure.direct.test')[1], '198.51.100.11')
                self.assertIn('secure.direct.test', lab.https['direct'].queries)
                self.assertIn(query(port, 'secure.proxy.test')[1], ('198.51.100.21', '198.51.100.22'))
                self.assertTrue(any('secure.proxy.test' in lab.https[name].queries for name in ('primary', 'backup')))
                lab.https['primary'].rcode = 2
                self.assertEqual(query(port, 'fallback.proxy.test')[1], '198.51.100.22')
                self.assertIn('fallback.proxy.test', lab.https['backup'].queries)
                destinations = lab.proxy.tcp_destinations
                self.assertNotIn(lab.https['direct'].server_address[1], destinations)
                for name in ('primary', 'backup'):
                    self.assertIn(lab.https[name].server_address[1], destinations)
                self.assertFalse(any(name.endswith('proxy.test') for name in lab.https['direct'].queries))
                for server in lab.servers.values():
                    self.assertFalse(any(name.endswith('proxy.test') for name in server.queries))
                lab.https['backup'].rcode = 2
                try:
                    self.assertIsNone(query(port, 'both-failed.proxy.test', timeout=5)[1])
                except socket.timeout:
                    pass
                self.assertNotIn('both-failed.proxy.test', lab.https['direct'].queries)
                for server in lab.servers.values():
                    self.assertNotIn('both-failed.proxy.test', server.queries)
            with core(lab, vpn_down=True) as port:
                self.assertEqual(query(port, 'disconnected.direct.test')[1], '198.51.100.11')
                try:
                    self.assertIsNone(query(port, 'disconnected.proxy.test', timeout=5)[1])
                except socket.timeout:
                    pass
                for server in [*lab.servers.values(), *lab.https.values()]:
                    self.assertNotIn('disconnected.proxy.test', server.queries)
