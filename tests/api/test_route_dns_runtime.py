"""Isolated DNS/proxy sockets: no TUN, public queries or production settings.

UDP resolvers make the selected network path observable. Settings validation
separately requires HTTPS for the production tunnel resolver pool.
"""
import contextlib
import json
import os
import select
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

module_root = Path(__file__).resolve().parents[2] / 'protocol-images/mihomo'
if module_root.is_dir():
    sys.path.insert(0, str(module_root))
from dns_routing import mihomo_route_dns, singbox_route_dns, xray_route_dns


def free_port():
    for _ in range(20):
        with socket.socket() as sock, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            try:
                udp.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('No free loopback test port')


def question(data):
    labels, end = [], 12
    while data[end]:
        length = data[end]
        labels.append(data[end + 1:end + 1 + length].decode('ascii'))
        end += 1 + length
    return '.'.join(labels), end + 5


def response(data, address, rcode=0):
    _, end = question(data)
    count = 1 if not rcode and data[end - 4:end - 2] == b'\0\1' else 0
    answer = data[:2] + struct.pack('!HHHHH', 0x8180 | rcode, 1, count, 0, 0) + data[12:end]
    if count:
        answer += b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton(address)
    return answer


def query(port, domain, timeout=7):
    packet = b'\x12\x34' + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0)
    packet += b''.join(bytes([len(label)]) + label.encode() for label in domain.split('.')) + b'\0\0\1\0\1'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, ('127.0.0.1', port))
        result = sock.recv(4096)
    _, end = question(result)
    count = struct.unpack('!H', result[6:8])[0]
    address = None
    for _ in range(count):
        while result[end]:
            if result[end] & 0xc0 == 0xc0:
                end += 2
                break
            end += result[end] + 1
        else:
            end += 1
        kind, _, _, length = struct.unpack('!HHIH', result[end:end + 10])
        end += 10
        if kind == 1 and length == 4:
            address = socket.inet_ntoa(result[end:end + 4])
        end += length
    return (result[3] & 15), address


class DnsHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, transport = self.request
        name, _ = question(packet)
        self.server.queries.append(name)
        self.server.details.append((name, struct.unpack('!H', packet[question(packet)[1] - 4:question(packet)[1] - 2])[0]))
        address = '127.0.0.1' if name in {'vpn-node.test', 'direct-dns.test', 'remote-dns.test'} else self.server.answer
        transport.sendto(response(packet, address, self.server.rcode), self.client_address)


def read_exact(sock, length):
    value = b''
    while len(value) < length:
        chunk = sock.recv(length - len(value))
        if not chunk:
            raise EOFError()
        value += chunk
    return value


def socks_destination(data, start=3):
    kind = data[start]
    if kind == 1:
        end = start + 5
        host = socket.inet_ntoa(data[start + 1:end])
    elif kind == 3:
        end = start + 2 + data[start + 1]
        host = data[start + 2:end].decode('ascii')
    else:
        raise ValueError('Only loopback IPv4/domain fixtures are supported')
    return host, struct.unpack('!H', data[end:end + 2])[0], end + 2


class SocksHandler(socketserver.BaseRequestHandler):
    def handle(self):
        active = threading.Event()
        udp = None
        try:
            header = read_exact(self.request, 2)
            read_exact(self.request, header[1])
            self.request.sendall(b'\x05\x00')
            header = read_exact(self.request, 4)
            size = 4 if header[3] == 1 else (16 if header[3] == 4 else read_exact(self.request, 1)[0])
            address = read_exact(self.request, size)
            destination_port = struct.unpack('!H', read_exact(self.request, 2))[0]
            if header[1] == 1:
                host = socket.inet_ntoa(address) if header[3] == 1 else address.decode('ascii')
                self.server.tcp_requests.append((host, destination_port))
                if host.endswith('.test'):
                    host = '127.0.0.1'
                if host != '127.0.0.1':
                    raise ValueError('Public destinations are forbidden')
                with socket.create_connection((host, destination_port), timeout=2) as upstream:
                    self.server.tcp_destinations.append(destination_port)
                    self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01' + b'\0\0')
                    while True:
                        readable, _, _ = select.select([self.request, upstream], [], [], 2)
                        if not readable:
                            return
                        for source in readable:
                            data = source.recv(65535)
                            if not data:
                                return
                            (upstream if source is self.request else self.request).sendall(data)
                return
            if header[1] != 3:
                self.request.sendall(b'\x05\x07\x00\x01' + b'\0' * 6)
                return
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind(('127.0.0.1', 0))
            udp.settimeout(.2)
            port = udp.getsockname()[1]
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', port))
            active.set()

            def relay():
                while active.is_set():
                    try:
                        packet, client = udp.recvfrom(65535)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    try:
                        host, destination_port, end = socks_destination(packet)
                        if host.endswith('.test'):
                            host = '127.0.0.1'
                        if host != '127.0.0.1':
                            raise ValueError('Public destinations are forbidden')
                        name, _ = question(packet[end:])
                        self.server.queries.append(name)
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                            upstream.settimeout(2)
                            upstream.sendto(packet[end:], (host, destination_port))
                            result = upstream.recv(65535)
                        udp.sendto(b'\0\0\0\x01\x7f\x00\x00\x01' + struct.pack('!H', destination_port) + result, client)
                    except (OSError, ValueError):
                        continue

            threading.Thread(target=relay, daemon=True).start()
            while self.request.recv(1):
                pass
        except (OSError, EOFError):
            pass
        finally:
            active.clear()
            if udp:
                udp.close()


class TcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class DnsLab:
    def __enter__(self):
        self.servers = {}
        for name, address in [('direct', '198.51.100.11'), ('primary', '198.51.100.21'),
                              ('backup', '198.51.100.22'), ('bootstrap', '198.51.100.31'),
                              ('proxy', '198.51.100.32'), ('local', '198.51.100.41')]:
            server = socketserver.ThreadingUDPServer(('127.0.0.1', 0), DnsHandler)
            server.daemon_threads = True
            server.answer, server.rcode, server.queries = address, 0, []
            server.details = []
            self.servers[name] = server
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
        self.proxy = TcpServer(('127.0.0.1', 0), SocksHandler)
        self.proxy.queries = []
        self.proxy.tcp_destinations = []
        self.proxy.tcp_requests = []
        threading.Thread(target=self.proxy.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
        return self

    def __exit__(self, *_):
        for server in [self.proxy, *self.servers.values()]:
            server.shutdown()
            server.server_close()

    def resolver(self, name, host='127.0.0.1'):
        return f'udp://{host}:{self.servers[name].server_address[1]}'

    def settings(self):
        return {'route_dns': True, 'nameserver': self.resolver('primary', 'remote-dns.test'),
                'fallback': self.resolver('backup'), 'direct_nameserver': self.resolver('direct', 'direct-dns.test'),
                'proxy_nameserver': self.resolver('proxy'), 'bootstrap_nameserver': self.resolver('bootstrap'),
                'local_nameserver': self.resolver('local')}

    def saw(self, name, role):
        return name in (self.proxy.queries if role == 'vpn' else self.servers[role].queries)


RULES = ['DOMAIN-SUFFIX,parent.test,GATE.312', 'DOMAIN,child.parent.test,DIRECT',
         'DOMAIN,exact.test,GATE.312', 'DOMAIN-SUFFIX,exact.test,DIRECT',
         'DOMAIN-SUFFIX,direct.test,DIRECT', 'DOMAIN-SUFFIX,lan,DIRECT']


@contextlib.contextmanager
def core(lab, engine, *, vpn_down=False):
    binary = os.environ[{'mihomo': 'PRIVACY_MIHOMO_BIN', 'singbox': 'PRIVACY_SINGBOX_BIN', 'xray': 'PRIVACY_XRAY_BIN'}[engine]]
    dns_port = free_port()
    socks_port = free_port() if vpn_down else lab.proxy.server_address[1]
    settings = lab.settings()
    if engine == 'mihomo':
        config = {'mixed-port': free_port(), 'mode': 'rule', 'log-level': 'warning',
                  'dns': {'enable': True, 'listen': f'127.0.0.1:{dns_port}', 'enhanced-mode': 'redir-host',
                          **mihomo_route_dns(settings, RULES, 'VPN')},
                  'proxies': [{'name': 'VPN', 'type': 'socks5', 'server': 'vpn-node.test', 'port': socks_port, 'udp': True}],
                  'rules': [*[rule.replace(',GATE.312', ',VPN') for rule in RULES], 'MATCH,VPN']}
    elif engine == 'singbox':
        config = {'log': {'level': 'error'}, 'dns': singbox_route_dns(settings, RULES, 'VPN'),
                  'inbounds': [{'type': 'direct', 'tag': 'dns-in', 'listen': '127.0.0.1', 'listen_port': dns_port, 'network': 'udp'}],
                  'outbounds': [{'type': 'socks', 'tag': 'VPN', 'server': 'vpn-node.test', 'server_port': socks_port,
                                 'version': '5', 'domain_resolver': 'bootstrap'}, {'type': 'direct', 'tag': 'direct', 'domain_resolver': 'direct-dns'}],
                  'route': {'rules': [{'inbound': ['dns-in'], 'action': 'hijack-dns'}], 'final': 'VPN', 'default_domain_resolver': 'remote'}}
    else:
        node = {'tag': 'proxy', 'protocol': 'socks', 'settings': {'servers': [{'address': 'vpn-node.test', 'port': socks_port}]},
                'streamSettings': {'sockopt': {'domainStrategy': 'UseIP'}}}
        config = {'log': {'loglevel': 'warning'}, 'dns': xray_route_dns(settings, RULES, node),
                  'inbounds': [{'protocol': 'dokodemo-door', 'tag': 'dns-in', 'listen': '127.0.0.1', 'port': dns_port,
                                'settings': {'network': 'udp', 'address': '1.1.1.1', 'port': 53}}],
                  'outbounds': [node, {'tag': 'direct', 'protocol': 'freedom', 'settings': {'domainStrategy': 'UseIP'}},
                                {'tag': 'dns-out', 'protocol': 'dns'}],
                  'routing': {'domainStrategy': 'AsIs', 'rules': [
                      {'type': 'field', 'inboundTag': ['dns-in'], 'outboundTag': 'dns-out'},
                      {'type': 'field', 'inboundTag': ['route-dns-bootstrap', 'route-dns-direct'], 'outboundTag': 'direct'},
                      {'type': 'field', 'inboundTag': ['route-dns-vpn'], 'outboundTag': 'proxy'}]}}
    with tempfile.TemporaryDirectory(prefix='route-dns-') as folder:
        if hasattr(lab, 'configure'):
            lab.configure(config, engine)
        path = Path(folder) / 'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        args = ['-d', folder, '-f', str(path)] if engine == 'mihomo' else (
            ['run', '-c', str(path)] if engine == 'singbox' else ['run', '-config', str(path)])
        with open(Path(folder) / 'core.log', 'w', encoding='utf-8') as log:
            process = subprocess.Popen([binary, *args], stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError(Path(log.name).read_text(encoding='utf-8'))
                    try:
                        address = query(dns_port, 'ready.direct.test', .3)[1]
                        if address == '198.51.100.11' or (getattr(lab, 'fake_ip', False) and address and address.startswith('198.18.')):
                            break
                    except (OSError, ValueError, IndexError):
                        time.sleep(.05)
                else:
                    raise AssertionError('DNS startup failed: ' + Path(log.name).read_text(encoding='utf-8'))
                yield dns_port
            except Exception as exc:
                seen = {name: server.queries[-12:] for name, server in lab.servers.items()}
                seen['vpn'] = lab.proxy.queries[-12:]
                seen['bootstrap_types'] = lab.servers['bootstrap'].details[-12:]
                raise AssertionError(str(exc) + '\n' + json.dumps(seen) + '\n' + Path(log.name).read_text(encoding='utf-8')) from exc
            finally:
                process.terminate()
                process.wait(timeout=8)


class RouteDnsRuntimeTests(unittest.TestCase):
    def exercise(self, engine):
        with DnsLab() as lab:
            with core(lab, engine) as port:
                for domain, role, expected in [
                    ('one.direct.test', 'direct', '198.51.100.11'),
                    ('one.proxy.test', 'vpn', '198.51.100.21'),
                    ('child.parent.test', 'vpn', '198.51.100.21'),
                    ('exact.test', 'vpn', '198.51.100.21'),
                    ('child.exact.test', 'direct', '198.51.100.11'),
                    ('router.lan', 'local', '198.51.100.41')]:
                    with self.subTest(engine=engine, domain=domain):
                        _, address = query(port, domain)
                        if role == 'vpn' and engine == 'mihomo':
                            self.assertIn(address, ['198.51.100.21', '198.51.100.22'])
                        else:
                            self.assertEqual(address, expected)
                        self.assertTrue(lab.saw(domain, role))
                        if role != 'direct':
                            self.assertFalse(lab.saw(domain, 'direct'))
                self.assertIn('direct-dns.test', lab.servers['bootstrap'].queries)
                self.assertIn('vpn-node.test', lab.servers['proxy'].queries)
                lab.servers['primary'].rcode = 2
                self.assertEqual(query(port, 'failed-primary.proxy.test')[1], '198.51.100.22')
                self.assertTrue(lab.saw('failed-primary.proxy.test', 'backup'))
                self.assertFalse(lab.saw('failed-primary.proxy.test', 'direct'))
                lab.servers['backup'].rcode = 2
                try:
                    _, answer = query(port, 'all-failed.proxy.test')
                    self.assertIsNone(answer)
                except socket.timeout:
                    pass
                self.assertFalse(lab.saw('all-failed.proxy.test', 'direct'))
            with core(lab, engine, vpn_down=True) as port:
                self.assertEqual(query(port, 'vpn-down.direct.test')[1], '198.51.100.11')
                try:
                    _, answer = query(port, 'vpn-down.proxy.test', timeout=5)
                    self.assertIsNone(answer)
                except socket.timeout:
                    pass
                self.assertFalse(lab.saw('vpn-down.proxy.test', 'direct'))

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires Mihomo')
    def test_mihomo_dns_network_paths(self):
        self.exercise('mihomo')

    @unittest.skipUnless(os.getenv('PRIVACY_SINGBOX_BIN'), 'Requires sing-box 1.14')
    def test_singbox_dns_network_paths(self):
        self.exercise('singbox')

    @unittest.skipUnless(os.getenv('PRIVACY_XRAY_BIN'), 'Requires Xray')
    def test_xray_dns_network_paths(self):
        self.exercise('xray')


if __name__ == '__main__':
    unittest.main()
