import json
import copy
import contextlib
import select
import socket
import socketserver
import threading
from dataclasses import replace
import hashlib
import ssl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.api.support import api, free_port, wait_port
import cdn_security
import network_probe
import relay_connections

REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen


@contextlib.contextmanager
def transparent_relay(target_port):
    """A byte-preserving TCP hop in front of the isolated TLS CDN gateway."""
    class Forward(socketserver.BaseRequestHandler):
        def handle(self):
            with socket.create_connection(('127.0.0.1', target_port), timeout=5) as upstream:
                while True:
                    ready, _, _ = select.select([self.request, upstream], [], [], 10)
                    if not ready:
                        return
                    for source in ready:
                        try:
                            data = source.recv(65536)
                        except ConnectionResetError:
                            return
                        if not data:
                            return
                        try:
                            (upstream if source is self.request else self.request).sendall(data)
                        except (BrokenPipeError, ConnectionResetError):
                            return
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
    with Server(('127.0.0.1', 0), Forward) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=2)


class NativeXrayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_dir = self.root/'xray'
        self.config_dir.mkdir()
        self.config = self.config_dir/'config.json'
        self.neighbor = {'tag':'neighbor', 'listen':'127.0.0.1', 'port':19000, 'protocol':'socks', 'settings':{}}
        self.config.write_text(json.dumps({'inbounds':[self.neighbor], 'outbounds':[{'protocol':'freedom'}]}))
        self.env = self.config_dir/'reality.env'
        self.env.write_text('TLS_ENABLED=yes\nTLS_DOMAIN=tls.example\nCDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n')
        self.clients = self.root/'clients.json'
        self.clients.write_text('[]')
        self.snippet = self.root/'routes.caddy'
        self.snippet.write_text('# previous gateway\n')
        for name, value in {'DATA_DIR':self.root, 'VLESS_CONFIG_DIR':self.config_dir, 'VLESS_CONFIG':self.config,
                            'VLESS_ENV':self.env, 'CLIENTS_FILE':self.clients, 'VLESS_CDN_SNIPPET':self.snippet,
                            'MIHOMO_VLESS_CDN_ROUTES':self.root/'mihomo-routes'}.items():
            self.enterContext(patch.object(api, name, value))
        self.enterContext(patch.object(cdn_security, 'DIRECT_ENV', self.env))
        self.enterContext(patch.object(cdn_security, 'ROUTES', self.root/'mihomo-routes'))
        self.enterContext(patch.object(cdn_security, 'ENV', self.root/'environment'))
        self.enterContext(patch.object(cdn_security, 'STATE', self.root/'cdn-security.json'))
        self.enterContext(patch.dict('os.environ', {'DATA_DIR':str(self.root)}))
        self.enterContext(patch.object(api, 'new_client_endpoint', return_value='192.0.2.1'))
        self.enterContext(patch.object(api, 'read_network_endpoint_settings', return_value={'cdn_domains':['cdn.example'], 'tls_relay_domains':['tls.example']}))
        self.enterContext(patch.object(api, 'network_endpoint_check', return_value={'selectable':True, 'ready':True}))
        self.command = self.enterContext(patch.object(api, 'run', return_value=''))
        self.restart = self.enterContext(patch.object(api, 'restart_vless_service'))
        self.validation = self.enterContext(patch.object(api.subprocess, 'run', return_value=Mock(returncode=0)))
        self.enterContext(patch.object(api.os, 'chown', create=True))
        self.enterContext(patch.object(api.port_allocation, 'allocate', side_effect=lambda *_, **__:18000+len(json.loads(self.config.read_text())['inbounds'])) )
        self.enterContext(patch.object(api.port_allocation, 'release_prefix'))

    def create(self, kind='vmess', transport='grpc', identity='a'*16):
        payload = api.ClientCreate(name='Device', protocol='vless-reality-xhttp', vless_routes=['tls','cdn'],
                                   settings={'xray_protocol':kind, 'tls_transport':transport, 'cdn_transport':transport, 'tls_xhttp_mode':'packet-up', 'cdn_xhttp_mode':'packet-up'})
        # A real allocator distinguishes reservations made within one batch.
        self.validation.return_value.returncode = 0
        with patch.object(api.port_allocation, 'allocate', side_effect=[18001,18002]):
            return api.create_client_native(payload, client_identity=identity)

    def test_create_probe_relay_export_and_delete_are_isolated_for_each_protocol(self):
        for kind in ('vmess','trojan'):
            for transport in ('websocket','xhttp','grpc','httpupgrade'):
                with self.subTest(protocol=kind, transport=transport):
                    result = self.create(kind, transport)
                    saved = json.loads(self.clients.read_text())[0]
                    self.assertEqual(saved['xray_protocol'], kind)
                    self.assertNotIn('password', saved)
                    self.assertEqual(len(cdn_security.native_xray_routes(self.config, self.clients)), 2)
                    configs = [json.loads(profile['config']) for profile in result['profiles']]
                    for route, config in zip(('tls','cdn'), configs):
                        self.assertEqual(config['outbounds'][0]['protocol'], kind)
                        self.assertIn(route+'.example', self.snippet.read_text())
                        binding = {'id':route, 'kind':'tls_relay' if route=='tls' else 'cdn', 'address':route+'.example', 'state':'active', 'desired_revision':1}
                        refs = [{'id':'direct:'+saved['id'], 'owner':'direct', 'kind':binding['kind'], 'address':binding['address'], 'reference':'snapshot'}]
                        plan = network_probe.prepare_direct_vless(binding, refs, [saved], json.loads(self.config.read_text()), {})
                        self.assertEqual(plan.client['protocol'], kind)
                        self.assertEqual(plan.consumer_id, 'direct:'+saved['id'])
                        self.assertNotIn(config['outbounds'][0]['settings'].get('servers',[{}])[0].get('password','UNUSED'), json.dumps(plan.summary()))
                    control = Mock()
                    control.endpoint.return_value = ('relay.example', 20000)
                    control.cdn_endpoint.return_value = ('relay.example', 20001)
                    settings = {**saved['settings'], 'relay_host_id':'b'*32}
                    self.assertEqual(relay_connections.direct_groups('vless-reality-xhttp', settings, result), [[('tcp',443)]])
                    redirected = relay_connections.direct_result(control, 'vless-reality-xhttp', settings, result)
                    self.assertIsInstance(json.loads(redirected['config']), list)
                    for outbound in (row['outbounds'][0] for row in json.loads(redirected['config'])):
                        endpoint = outbound['settings']['servers' if kind=='trojan' else 'vnext'][0]
                        self.assertEqual((endpoint['address'],endpoint['port']), ('relay.example',20001 if outbound['streamSettings']['tlsSettings']['serverName'] == 'cdn.example' else 20000))
                        self.assertIn(outbound['streamSettings']['tlsSettings']['serverName'], {'tls.example','cdn.example'})
                    api.xray_native.delete(api, saved, [saved])
                    self.assertEqual(json.loads(self.config.read_text())['inbounds'], [self.neighbor])
                    self.assertEqual(json.loads(self.clients.read_text()), [])

    def test_gateway_failure_restores_config_clients_and_snippet(self):
        before = {path:path.read_bytes() for path in (self.config, self.clients, self.snippet)}
        self.command.side_effect = lambda *args, **_: (_ for _ in ()).throw(RuntimeError('gateway invalid')) if args[0]=='caddy' else ''
        with self.assertRaisesRegex(RuntimeError, 'gateway invalid'):
            self.create('trojan')
        self.assertEqual({path:path.read_bytes() for path in before}, before)
        self.assertEqual(self.restart.call_count, 2)

    def test_direct_reality_rejected_and_candidate_validation_preserves_neighbors(self):
        payload = api.ClientCreate(name='Device', protocol='vless-reality-xhttp', vless_routes=['direct'], settings={'xray_protocol':'vmess'})
        before = self.config.read_bytes()
        with self.assertRaises(api.HTTPException): api.create_client_native(payload)
        self.assertEqual(self.config.read_bytes(), before)
        self.restart.assert_not_called()

    def test_real_native_xray_tls_round_trips_for_both_protocols_and_all_transports(self):
        binary = os.getenv('PRIVACY_XRAY_BIN')
        openssl = os.getenv('PRIVACY_OPENSSL_BIN')
        caddy = os.getenv('PRIVACY_CADDY_BIN')
        if not binary or not openssl or not caddy: self.skipTest('Set Xray, Caddy and OpenSSL binaries')
        cert, key = self.root/'cert.pem', self.root/'key.pem'
        REAL_RUN([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=tls.example',
                  '-addext', 'subjectAltName=DNS:tls.example,DNS:cdn.example', '-addext', 'basicConstraints=critical,CA:TRUE', '-keyout', str(key), '-out', str(cert)],
                 check=True, capture_output=True, timeout=15)
        for kind in ('vmess','trojan'):
            for transport in ('websocket','xhttp','grpc','httpupgrade'):
                with self.subTest(protocol=kind, transport=transport):
                    result = self.create(kind, transport)
                    candidate = json.loads(self.config.read_text())
                    checked = REAL_RUN([binary,'run','-test','-config',str(self.config)],capture_output=True,text=True,timeout=15)
                    self.assertEqual(checked.returncode, 0, checked.stdout+checked.stderr)
                    # Production-shaped Caddy TLS -> loopback Xray transport.
                    # Substitute ephemeral ports without changing saved files.
                    candidate['inbounds'] = [row for row in candidate['inbounds'] if row['tag'].startswith('xray-')]
                    ports = {}
                    for inbound in candidate['inbounds']:
                        port = free_port()
                        while port in ports.values(): port = free_port()
                        inbound['port'] = port
                        ports[inbound['tag']] = port
                    path = self.root/'isolated-server.json'
                    path.write_text(json.dumps(candidate))
                    process = subprocess.Popen([binary,'run','-config',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    gateway = None
                    gateway_log = (self.root/'caddy-errors.log').open('wb')
                    saved = json.loads(self.clients.read_text())[0]
                    try:
                        for port in ports.values(): wait_port(port, process)
                        front = free_port(udp=True)
                        local_routes = [{**route, 'port':ports[route['tag']]} for route in saved['xray_routes']]
                        text = cdn_security.render_routes(local_routes, {}, protected=False)
                        for domain in ('tls.example','cdn.example'):
                            text = text.replace(domain+' {', f'https://{domain}:{front} {{\n    bind 127.0.0.1\n    tls {json.dumps(str(cert))} {json.dumps(str(key))}')
                        caddyfile = self.root/'Caddyfile'
                        caddyfile.write_text('{\n admin off\n auto_https off\n servers {\n  protocols h1 h2\n }\n}\n'+text)
                        gateway = subprocess.Popen([caddy,'run','--config',str(caddyfile)],stdout=gateway_log,stderr=gateway_log,
                            env={**os.environ, 'XDG_CONFIG_HOME':str(self.root/'xdg-config'), 'XDG_DATA_HOME':str(self.root/'xdg-data')})
                        try:
                            wait_port(front, gateway)
                        except AssertionError as exc:
                            raise AssertionError((self.root/'caddy-errors.log').read_text()[-1500:]) from exc
                        for route in ('tls','cdn'):
                            binding = {'id':route, 'kind':'tls_relay' if route=='tls' else 'cdn', 'address':route+'.example', 'state':'active', 'desired_revision':1}
                            refs = [{'id':'direct:'+saved['id'], 'owner':'direct', 'kind':binding['kind'], 'address':binding['address'], 'reference':'snapshot'}]
                            plan = network_probe.prepare_direct_vless(binding, refs, [saved], json.loads(self.config.read_text()), {})
                            outbound = plan.client
                            endpoint = outbound['settings']['servers' if kind=='trojan' else 'vnext'][0]
                            endpoint.update(address='127.0.0.1', port=front)
                            outbound['streamSettings']['tlsSettings']['pinnedPeerCertSha256'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
                            def start(*args, **kwargs):
                                probe_config = json.loads(Path(args[0][-1]).read_text())
                                child = REAL_POPEN(*args, **kwargs)
                                # Windows may time out a connect while Xray is
                                # still starting. Keep that harness delay apart
                                # from the handshake/nonce under examination.
                                wait_port(probe_config['inbounds'][0]['port'], child)
                                return child
                            with patch.object(network_probe.subprocess, 'Popen', side_effect=start):
                                actual = network_probe.execute_direct_vless(plan, binary, '127.0.0.1', timeout=5)
                            self.assertEqual(actual['state'], 'verified', actual)
                            # The same native protocol/transport must also work
                            # with an opaque TCP Relay before TLS termination.
                            with transparent_relay(front) as relay_port:
                                forwarded = copy.deepcopy(outbound)
                                forwarded['settings']['servers' if kind=='trojan' else 'vnext'][0]['port'] = relay_port
                                chained = replace(plan, client=forwarded)
                                with patch.object(network_probe.subprocess, 'Popen', side_effect=start):
                                    actual = network_probe.execute_direct_vless(chained, binary, '127.0.0.1', timeout=5)
                                self.assertEqual(actual['state'], 'verified', actual)
                            if kind == 'trojan' and transport == 'httpupgrade':
                                with patch.object(network_probe.subprocess, 'Popen', side_effect=start):
                                    unavailable = network_probe.execute_direct_vless(chained, binary, '127.0.0.1', timeout=2)
                                self.assertNotEqual(unavailable['state'], 'verified', unavailable)
                                invalid = copy.deepcopy(outbound)
                                invalid['settings']['servers'][0]['password'] += '-wrong'
                                with patch.object(network_probe.subprocess, 'Popen', side_effect=start):
                                    rejected = network_probe.execute_direct_vless(replace(plan, client=invalid), binary, '127.0.0.1', timeout=3)
                                self.assertEqual(rejected['state'], 'failed', rejected)
                    finally:
                        if gateway is not None:
                            gateway.terminate()
                            gateway.wait(timeout=10)
                        gateway_log.close()
                        process.terminate()
                        process.wait(timeout=10)
                        api.xray_native.delete(api, saved, [saved])
