"""Exercise real TLS ingress and controller fences, without public network access."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from relay_agent_test import relay, free_port
ControllerPolicy, SecurityError, public_route = relay.ControllerPolicy, relay.SecurityError, relay.public_route


class ControllerTests(unittest.TestCase):
    def test_pairing_uses_tcp_source_and_persists_without_rebinding(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = ControllerPolicy(folder, relay.atomic_json)
            for peer, origin in [('1.1.1.1', '8.8.8.8'), ('127.0.0.1', '127.0.0.1')]:
                with self.assertRaises(SecurityError): policy.bind(peer, {'origin_ip': origin})
            self.assertFalse(policy.path.exists())
            policy.bind('::ffff:1.1.1.1', {'origin_ip': '1.1.1.1'})
            self.assertTrue(policy.permits_peer('1.1.1.1'))
            self.assertTrue(policy.permits_peer('::1'))
            self.assertFalse(policy.permits_peer('8.8.8.8'))
            with self.assertRaises(SecurityError): policy.bind('8.8.8.8', {'origin_ip': '8.8.8.8'})
            self.assertEqual(ControllerPolicy(folder, relay.atomic_json).read()['origin_ip'], '1.1.1.1')
            policy.path.write_text('{"schema":1,"origin_ip":"broken"}')
            with self.assertRaises(SecurityError): policy.permits_peer('1.1.1.1')

    def test_unpaired_and_external_raw_routes_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = ControllerPolicy(folder, relay.atomic_json)
            config = {'id': 'legacy', 'transport': 'tcp', 'target_ip': '8.8.8.8'}
            with self.assertRaises(SecurityError): policy.authorize_route(config)
            policy.bind('1.1.1.1', {'origin_ip': '1.1.1.1'})
            with self.assertRaises(SecurityError): policy.authorize_route(config)
            policy.authorize_route({**config, 'target_ip': '1.1.1.1'})
            self.assertEqual(policy.snapshot([config])['unauthenticated_external_routes'], ['legacy'])


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        openssl = shutil.which('openssl') or 'D:/Git/usr/bin/openssl.exe'
        if not Path(openssl).is_file(): self.skipTest('OpenSSL required')
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-keyout', str(self.root/'server.key'),
                        '-out', str(self.root/'server.crt')], check=True, capture_output=True)
        self.hits = 0
        async def echo(reader, writer):
            self.hits += 1
            try:
                while data := await reader.read(65536):
                    writer.write(data); await writer.drain()
                    if data == b'fixture-close': break
            finally: writer.close()
        self.origin = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.password = 'synthetic-password-with-256-bit-randomness-in-production'
        self.config = {'id': 'protected', 'transport': 'tcp', 'listen_port': free_port(),
                       'target_ip': '127.0.0.1', 'target_port': self.origin.sockets[0].getsockname()[1],
                       'auth': {'type': 'tls-connect', 'username': 'synthetic_username', 'credential_id': 'a'*32,
                                'password_sha256': hashlib.sha256(self.password.encode()).hexdigest()}}
        self.route = relay.RelayRoute(self.config, '127.0.0.1')
        self.route.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.route.tls_context.load_cert_chain(self.root/'server.crt', self.root/'server.key')
        await self.route.start()
        self.context = ssl.create_default_context(cafile=str(self.root/'server.crt'))

    async def asyncTearDown(self):
        if hasattr(self, 'route'): await self.route.close()
        if hasattr(self, 'origin'):
            self.origin.close(); await self.origin.wait_closed()
        self.temp.cleanup()

    async def request(self, password=None, target=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.config['listen_port'], ssl=self.context, server_hostname='localhost')
        target = target or '127.0.0.1:'+str(self.config['target_port'])
        header = '' if password is None else 'Proxy-Authorization: Basic '+base64.b64encode(('synthetic_username:'+password).encode()).decode()+'\r\n'
        writer.write(f'CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{header}\r\n'.encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3)
        return reader, writer, response

    async def test_rejects_missing_wrong_credentials_and_arbitrary_destinations_before_connect(self):
        for password, target in [(None, None), ('wrong', None), (self.password, '1.1.1.1:443')]:
            reader, writer, response = await self.request(password, target)
            self.assertIn(b' 407 ', response)
            self.assertEqual(await reader.read(), b'')
            writer.close(); await writer.wait_closed()
        self.assertEqual(self.hits, 0)
        self.assertEqual(self.route.authentication_rejections, 3)
        self.assertEqual(self.route.errors, 0)

    async def test_valid_tls_connect_transfers_large_binary_payload_and_redacts_hash(self):
        closed=[]
        self.route.diagnostic_closed=closed.append
        reader, writer, response = await self.request(self.password)
        self.assertIn(b' 200 ', response)
        reference=self.route.status()['diagnostics']['session_refs'][0]
        payload = bytes(range(256))*2048
        writer.write(payload); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 5), payload)
        writer.close(); await writer.wait_closed()
        for _ in range(100):
            if closed: break
            await asyncio.sleep(.02)
        self.assertEqual(len(closed),1)
        self.assertEqual(closed[0]['session_ref'],reference)
        self.assertEqual(closed[0]['sent_bytes'],len(payload))
        self.assertEqual(closed[0]['received_bytes'],len(payload))
        self.assertEqual(closed[0]['phase'],'closed')
        self.assertNotIn('127.0.0.1',json.dumps(closed))
        self.assertEqual(self.hits, 1)
        self.assertNotIn(self.config['auth']['password_sha256'], json.dumps(self.route.status()))
        self.assertEqual(public_route(self.config)['auth']['credential_id'], 'a'*32)

    async def test_handshake_retry_is_bounded_and_never_replays_established_data(self):
        with patch.object(relay.asyncio, 'open_connection', AsyncMock(side_effect=[OSError(), ('reader', 'writer')])) as connect:
            self.assertEqual(await self.route.connect_upstream(), ('reader', 'writer'))
            self.assertEqual(connect.await_count, 2)
        with patch.object(relay.asyncio, 'open_connection', AsyncMock(side_effect=OSError())) as connect:
            for _ in range(3):
                with self.assertRaises(OSError): await self.route.connect_upstream()
            self.assertEqual(connect.await_count, 6)
            self.assertGreater(self.route.connect_retry_at, relay.time.monotonic())

    async def test_origin_eof_closes_tls_client_without_waiting_for_idle_timeout(self):
        reader, writer, response = await self.request(self.password)
        self.assertIn(b' 200 ', response)
        writer.write(b'fixture-close'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'fixture-close')
        writer.close(); await writer.wait_closed()

    async def test_denied_api_source_is_closed_before_tls_and_never_gets_plaintext_reply(self):
        (self.root/'config.json').write_text(json.dumps({'own_ips': ['8.8.8.8']}))
        agent = relay.Agent(self.root, self.root, '127.0.0.1')
        writer = unittest.mock.Mock()
        writer.get_extra_info.return_value = ('8.8.8.8', 12345)
        agent.security.bind('1.1.1.1', {'origin_ip': '1.1.1.1'})
        writer.start_tls = AsyncMock()
        await agent.http_client(AsyncMock(), writer)
        writer.start_tls.assert_not_awaited()
        writer.write.assert_not_called()
        writer.close.assert_called_once()
        self.assertEqual(agent.http_connections, 0)


if __name__ == '__main__': unittest.main()
