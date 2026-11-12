import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from tests.api.support import api, ROOT, free_port, wait_port
import diagnostic_probes as probes
import network_probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        probes._recent.clear()

    def test_real_DNS_answer_is_validated_and_not_confused_with_TCP_availability(self):
        real_socket = socket.socket
        udp = real_socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(5)
        port = udp.getsockname()[1]
        def serve():
            try:
                packet, peer = udp.recvfrom(4096)
                answer = packet[:2] + struct.pack('!HHHHH', 0x8180, 1, 1, 0, 0) + packet[12:]
                answer += b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + b'\x01\x01\x01\x01'
                udp.sendto(answer, peer)
            finally: udp.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        class DNSFixtureSocket(real_socket):
            def connect(self, target):
                if target == ('127.0.0.1', 53): target = ('127.0.0.1', port)
                return super().connect(target)
        with patch.object(api, 'dns_provider_list', return_value=[{'id': 'fixture', 'addresses': ['127.0.0.1']}]), \
             patch.object(api.socket, 'socket', DNSFixtureSocket):
            result = probes.probe(api, probes.ProbeQuery(kind='dns', provider_id='fixture'))
        thread.join(5)
        self.assertEqual(result['observations'][0]['state'], 'passed')
        self.assertTrue(result['observations'][0]['data']['udp_ok'])
        self.assertFalse(result['observations'][0]['data']['tcp_ok'])

    def test_real_TLS_checks_certificate_chain_and_hostname(self):
        binary = shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        if not Path(binary).exists(): self.skipTest('OpenSSL unavailable')
        key, cert = self.root / 'key.pem', self.root / 'cert.pem'
        subprocess.run([binary, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        server.socket = server_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            trusted = ssl.create_default_context(cafile=str(cert))
            good = probes.tls_exchange('localhost', ['127.0.0.1'], port=server.server_port, context=trusted)
            wrong_host = probes.tls_exchange('wrong.example', ['127.0.0.1'], port=server.server_port, context=trusted)
            untrusted = probes.tls_exchange('localhost', ['127.0.0.1'], port=server.server_port)
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
        self.assertEqual(good['state'], 'passed')
        self.assertTrue(good['certificate_verified'])
        self.assertEqual(wrong_host['error_code'], 'tls_certificate_invalid')
        self.assertEqual(untrusted['error_code'], 'tls_certificate_invalid')

    def test_targets_are_inventory_only_and_private_TLS_targets_are_not_contacted(self):
        with patch.object(api, 'dns_provider_list', return_value=[]), patch.object(api, 'read_route_registry', return_value={'bindings': []}):
            for query in [probes.ProbeQuery(kind='dns', provider_id='attacker'), probes.ProbeQuery(kind='tls', binding_id='a' * 32)]:
                with self.assertRaises(HTTPException): probes.probe(api, query)
        self.assertEqual(probes.resolve_public('127.0.0.1'), [])
        self.assertEqual(probes.resolve_public('169.254.169.254'), [])

    def test_real_core_transport_nonce_and_config_change_detection(self):
        binary = os.getenv('PRIVACY_XRAY_BIN') or shutil.which('xray') or str(ROOT / '.runtime/audit-binaries/Xray-core/xray.exe')
        if not Path(binary).exists(): self.skipTest('Real Xray binary unavailable')
        port = free_port()
        identity = '11111111-1111-4111-8111-111111111111'
        path = self.root / 'server.json'
        server_config = {'log': {'loglevel': 'none'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'vless',
                         'settings': {'clients': [{'id': identity}], 'decryption': 'none'}, 'streamSettings': {'network': 'raw'}}],
                         'outbounds': [{'protocol': 'freedom'}]}
        path.write_text(json.dumps(server_config))
        process = subprocess.Popen([binary, 'run', '-config', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_port(port, process)
            target = {'id': 'a' * 32, 'kind': 'tls_relay', 'address': 'configured.example', 'desired_revision': 1, 'state': 'active'}
            plan = network_probe.ProbePlan(target['id'], 1, 'mihomo:fixture:conn', '127.0.0.1', port, 'fixture', port, 'raw', 'b' * 64,
                {'protocol': 'vless', 'settings': {'vnext': [{'address': '127.0.0.1', 'port': port, 'users': [{'id': identity, 'encryption': 'none'}]}]},
                 'streamSettings': {'network': 'raw'}}, producer='mihomo_vless')
            sources = {'config': path}
            fixture_api = types.SimpleNamespace(DATA_DIR=self.root, read_route_registry=lambda: {'bindings': [target]},
                prepare_network_probe=lambda *_: (plan, network_probe.source_revision(sources)), network_probe_sources=lambda *_: sources,
                execute_network_probe=lambda p: network_probe.execute_direct_vless(p, binary, '127.0.0.1', timeout=5))
            query = probes.ProbeQuery(kind='transport', binding_id=target['id'], consumer_id=plan.consumer_id)
            result = probes.probe(fixture_api, query)
            self.assertEqual(result['observations'][0]['state'], 'passed', result)
            self.assertNotIn(identity, json.dumps(result))
            probes._recent.clear()
            def changed(p):
                value = network_probe.execute_direct_vless(p, binary, '127.0.0.1', timeout=5)
                path.write_text(json.dumps(server_config) + ' ')
                return value
            fixture_api.execute_network_probe = changed
            result = probes.probe(fixture_api, query)
            self.assertEqual(result['observations'][0]['state'], 'unknown')
            self.assertFalse(result['confirmed'])
        finally:
            process.terminate(); process.wait(timeout=5)

    def test_rate_limit_and_unknown_are_distinct_from_failed_measurement(self):
        with patch.object(api, 'managed_services', return_value={'fixture': {'unit': 'fixed.service'}}), \
             patch.object(api, 'observe_service', return_value={'runtime': {'state': 'unknown'}}):
            query = probes.ProbeQuery(kind='service', service='fixture')
            result = probes.probe(api, query)
            self.assertEqual(result['observations'][0]['state'], 'unknown')
            with self.assertRaises(HTTPException) as error: probes.probe(api, query)
            self.assertEqual(error.exception.status_code, 429)

    def test_target_discovery_is_small_and_never_exports_credentials_or_starts_probes(self):
        target = {'id': 'a' * 32, 'state': 'active', 'kind': 'tls_relay', 'address': 'configured.example', 'password': 'private-password'}
        with patch.object(api, 'read_route_registry', return_value={'bindings': [target]}), \
             patch.object(api, 'network_consumers', return_value=[{'id': 'mihomo:one:conn', 'kind': 'tls_relay', 'address': 'configured.example', 'credential': 'private-key'}]), \
             patch.object(api, 'dns_provider_list', return_value=[{'id': 'fixture', 'password': 'private-key'}]), \
             patch.object(api, 'managed_services', return_value={'api': {'unit': 'fixed.service'}}), \
             patch.object(api, 'execute_network_probe') as execute:
            result = probes.probe(api, probes.ProbeQuery(kind='targets'))
        self.assertEqual(result['observations'][0]['data']['bindings'][0]['consumer_ids'], ['mihomo:one:conn'])
        self.assertNotIn('private-key', json.dumps(result))
        self.assertNotIn('private-password', json.dumps(result))
        execute.assert_not_called()
