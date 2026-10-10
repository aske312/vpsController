import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('relay_agent_runtime', Path(__file__).resolve().parents[1] / 'protocol-images/relay-agent/agent.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
client_spec = importlib.util.spec_from_file_location('relay_pro_client', Path(__file__).resolve().parents[1] / 'protocol-images/relay-agent/client.py')
pro_client = importlib.util.module_from_spec(client_spec)
client_spec.loader.exec_module(pro_client)


def free_port():
    for port in range(relay.PORT_MIN, relay.PORT_MAX + 1):
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', port))
            except OSError: continue
            return port
    raise RuntimeError('No isolated test port available')


class ValidationTests(unittest.TestCase):
    def test_targets_cannot_escape_to_private_metadata_dns_or_self(self):
        body = {'transport': 'tcp', 'listen_port': 20001, 'target_ip': '1.1.1.1', 'target_port': 443}
        for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '100.64.0.1', '224.0.0.1', '0.0.0.0', '192.168.1.1', 'example.org', '::ffff:1.1.1.1', '2606:4700::1111'):
            with self.subTest(ip=ip), self.assertRaises(relay.RelayError):
                relay.validate_route('test', {**body, 'target_ip': ip})
        with self.assertRaises(relay.RelayError): relay.validate_route('test', body, ['1.1.1.1'])
        for fields in ({'listen_port': 80}, {'target_port': True}, {'transport': 'shell'}, {'extra': 'exec'}):
            with self.assertRaises(relay.RelayError): relay.validate_route('test', {**body, **fields})
        with self.assertRaises(relay.RelayError): relay.validate_route('../../bad', body)

    def test_pro_client_pins_before_sending_auth_and_never_follows_redirects(self):
        token = 'x' * 64
        client = pro_client.RelayClient('https://8.8.8.8:9443', token, 'a' * 64)
        with patch.object(pro_client.http.client, 'HTTPSConnection') as factory:
            connection = factory.return_value
            connection.sock.getpeercert.return_value = b'untrusted certificate'
            with self.assertRaises(pro_client.AgentAPIError) as error: client.request('GET', '/v1/status')
            self.assertEqual(error.exception.status, 495)
            connection.request.assert_not_called()
            connection.close.assert_called_once()


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.token = 'test-token-which-is-not-the-panel-password'
        (root / 'config.json').write_text(json.dumps({'token_sha256': hashlib.sha256(self.token.encode()).hexdigest(), 'own_ips': ['8.8.8.8']}))
        self.agent = relay.Agent(root, root, '127.0.0.1')
        self.headers = {'authorization': 'Bearer '+self.token, 'if-match': '0'}
        self.body = {'transport': 'tcp', 'listen_port': free_port(), 'target_ip': '1.1.1.1', 'target_port': 443}

    async def asyncTearDown(self):
        for route in self.agent.routes.values(): await route.close()
        self.temp.cleanup()

    async def test_auth_rotation_revokes_old_token_without_stopping_routes(self):
        with self.assertRaises(relay.RelayError) as error: await self.agent.dispatch('GET', '/v1/status', {}, {})
        self.assertEqual(error.exception.status, 401)
        result = await self.agent.dispatch('PUT', '/v1/routes/test', self.headers, self.body)
        self.assertEqual(result['item']['state'], 'listening')
        path = self.agent.config_dir / 'config.json'
        value = json.loads(path.read_text())
        value['token_sha256'] = hashlib.sha256(b'replacement').hexdigest()
        relay.atomic_json(path, value)
        with self.assertRaises(relay.RelayError): await self.agent.dispatch('GET', '/v1/status', self.headers, {})
        status = await self.agent.dispatch('GET', '/v1/status', {'authorization': 'Bearer replacement'}, {})
        self.assertEqual(status['routes'], 1)
        self.assertNotIn('token_sha256', status)

    async def test_revisions_idempotency_conflicts_delete_and_restore(self):
        result = await self.agent.dispatch('PUT', '/v1/routes/test', self.headers, self.body)
        self.assertEqual(result['revision'], 1)
        repeated = await self.agent.dispatch('PUT', '/v1/routes/test', self.headers, self.body)
        self.assertEqual(repeated['revision'], 1)
        with self.assertRaises(relay.RelayError) as error:
            await self.agent.dispatch('PUT', '/v1/routes/other', self.headers, self.body)
        self.assertEqual(error.exception.status, 409)
        restored = relay.Agent(self.agent.config_dir, self.agent.state_dir, '127.0.0.1')
        await self.agent.routes['test'].close()
        self.agent.routes.clear()
        await restored.restore()
        self.agent = restored
        self.assertEqual(self.agent.revision, 1)
        self.assertIn('test', self.agent.routes)
        fresh = {**self.headers, 'if-match': '1'}
        removed = await self.agent.dispatch('DELETE', '/v1/routes/test', fresh, {})
        self.assertEqual(removed['revision'], 2)
        self.assertEqual(await self.agent.dispatch('DELETE', '/v1/routes/test', fresh, {}), removed)
        self.assertEqual(json.loads((self.agent.state_dir / 'routes.json').read_text())['items'], [])

    async def test_persistence_failure_releases_listener_and_preserves_revision(self):
        with patch.object(relay, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): await self.agent.dispatch('PUT', '/v1/routes/test', self.headers, self.body)
        self.assertEqual(self.agent.revision, 0)
        self.assertEqual(self.agent.routes, {})
        with socket.socket() as probe: probe.bind(('127.0.0.1', self.body['listen_port']))

    async def test_real_tls_http_api_rejects_unauthorized_and_accepts_authenticated_status(self):
        openssl = shutil.which('openssl') or 'D:/Git/usr/bin/openssl.exe'
        if not Path(openssl).is_file(): self.skipTest('OpenSSL is required for TLS integration')
        root = self.agent.config_dir
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
            '-keyout', str(root/'key'), '-out', str(root/'cert')], check=True, capture_output=True)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(root/'cert', root/'key')
        client_context = ssl.create_default_context(cafile=str(root/'cert'))
        server = await asyncio.start_server(self.agent.http_client, '127.0.0.1', 0, ssl=server_context)
        async def request(auth=''):
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1], ssl=client_context)
            writer.write(('GET /v1/status HTTP/1.1\r\nHost: localhost\r\n'+auth+'\r\n').encode()); await writer.drain()
            result = await reader.read()
            writer.close(); await writer.wait_closed()
            return result
        try:
            self.assertIn(b'HTTP/1.1 401', await request())
            response = await request('Authorization: Bearer '+self.token+'\r\n')
            self.assertIn(b'HTTP/1.1 200', response)
            self.assertEqual(json.loads(response.split(b'\r\n\r\n')[1])['api_version'], 1)
            self.assertNotIn(self.token.encode(), response)
        finally:
            server.close(); await server.wait_closed()

    async def test_real_tcp_roundtrip_supports_half_close_and_counters(self):
        async def echo(reader, writer):
            data = await reader.read()
            writer.write(b'response:'+data)
            await writer.drain()
            writer.close()
        origin = await asyncio.start_server(echo, '127.0.0.1', 0)
        route = relay.RelayRoute({**self.body, 'id': 'tcp', 'target_ip': '127.0.0.1', 'target_port': origin.sockets[0].getsockname()[1]}, '127.0.0.1')
        await route.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', self.body['listen_port'])
            writer.write(b'payload'); await writer.drain(); writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'response:payload')
            writer.close(); await writer.wait_closed()
            self.assertEqual(route.sent, 7)
            self.assertEqual(route.received, 16)
        finally:
            await route.close(); origin.close(); await origin.wait_closed()

    async def test_udp_clients_keep_independent_reply_paths_and_first_packet_bursts(self):
        class Echo(asyncio.DatagramProtocol):
            def connection_made(self, transport): self.transport = transport
            def datagram_received(self, data, addr): self.transport.sendto(data, addr)
        loop = asyncio.get_running_loop()
        origin, _ = await loop.create_datagram_endpoint(Echo, local_addr=('127.0.0.1', 0))
        route = relay.RelayRoute({**self.body, 'id': 'udp', 'transport': 'udp', 'target_ip': '127.0.0.1', 'target_port': origin.get_extra_info('sockname')[1]}, '127.0.0.1')
        await route.start()
        clients = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
        try:
            for index, sock in enumerate(clients):
                sock.setblocking(False); sock.bind(('127.0.0.1', 0))
                for packet in (b'first', b'second'): await loop.sock_sendto(sock, bytes([index])+packet, ('127.0.0.1', self.body['listen_port']))
            for index, sock in enumerate(clients):
                replies = [await asyncio.wait_for(loop.sock_recv(sock, 1024), 3) for _ in range(2)]
                self.assertEqual(set(replies), {bytes([index])+b'first', bytes([index])+b'second'})
            self.assertEqual(len(route.sessions), 2)
        finally:
            for sock in clients: sock.close()
            await route.close(); origin.close()


if __name__ == '__main__': unittest.main()
