import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

from tests.api.support import api
from relay_manager import RelayManager, route_snapshot, status_snapshot
from relay_client import RelayClient, AgentAPIError
from application_operation import OperationConflict, require_recovery_clear
from network_probe import ProbePlan
import relay_export

ORIGIN = '195.14.118.112'
AGENT = '85.209.155.229'
TOKEN = 's'*48
PIN = 'a'*64


class FakeAgent:
    def __init__(self):
        self.host = AGENT
        self.revision = 0
        self.items = []
        self.calls = []
        self.offline = False
        self.lose_ack = False
        self.offline_after_write = False
        self.reject = False

    def request(self, method, path, body=None, revision=None):
        self.calls.append((method, path))
        if self.offline:
            raise OSError('offline')
        if method == 'GET':
            if path == '/v1/status':
                return {'api_version': 1, 'version': '1.0.0', 'revision': self.revision, 'routes': len(self.items), 'failed_routes': 0, 'transports': ['tcp', 'udp'], 'address_family': 'ipv4', 'listen_ports': {'min': 20000, 'max': 20999}, 'max_routes': 64}
            return copy.deepcopy({'revision': self.revision, 'items': self.items})
        if self.reject:
            raise AgentAPIError(409, 'rejected')
        assert revision == self.revision
        route_id = path.rsplit('/', 1)[1]
        self.items = [item for item in self.items if item['id'] != route_id]
        if method == 'PUT':
            self.items.append({'id': route_id, **body, 'state': 'listening', 'target_verified': False, 'sent_bytes': 0, 'received_bytes': 0, 'sessions': 0, 'errors': 0})
        self.revision += 1
        if self.offline_after_write:
            self.offline = True
        if self.lose_ack or self.offline:
            raise OSError('response lost')
        return {}


class RelayManagementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.agent = FakeAgent()
        self.control = RelayManager(self.root, ORIGIN, lambda *_: self.agent)
        self.control.save_host({'name': 'Relay', 'agent_url': f'https://{AGENT}:9443', 'token': TOKEN,
                                'certificate_sha256': PIN, 'expected_revision': '0'*32})
        self.host_id = self.control.read()['hosts'][0]['id']

    def payload(self, port=20001):
        return {'host_id': self.host_id, 'transport': 'tcp', 'listen_port': port, 'target_port': 443,
                'expected_revision': self.control.read()['revision'], 'agent_revision': self.agent.revision}

    def test_create_readback_delete_and_foreign_inventory(self):
        self.agent.items.append({'id': 'foreign', 'transport': 'udp', 'listen_port': 20001, 'target_ip': ORIGIN,
                                 'target_port': 51820, 'state': 'listening', 'target_verified': False})
        self.control.mutate_route(self.payload(), 'b'*32)
        snapshot = self.control.public()
        host = snapshot['hosts'][0]
        self.assertEqual(host['foreign_routes'], 1)
        self.assertEqual(host['routes'][0]['state'], 'listening')
        self.assertFalse(host['routes'][0]['target_verified'])
        self.assertNotIn(TOKEN, json.dumps(snapshot))
        self.assertNotIn('token', host)
        route_id = host['routes'][0]['id']
        self.control.mutate_route({**self.payload(), 'route_id': route_id}, 'c'*32, delete=True)
        self.assertEqual([item['id'] for item in self.agent.items], ['foreign'])
        self.assertEqual(self.control.read()['routes'], [])

    def test_lost_ack_is_read_back_without_replay(self):
        self.agent.lose_ack = True
        self.control.mutate_route(self.payload(), 'b'*32)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)
        self.assertEqual(len(self.control.read()['routes']), 1)
        self.assertFalse(self.control.pending.exists())

    def test_worker_interruption_survives_restart_and_blocks_writes(self):
        self.agent.offline_after_write = True
        with self.assertRaises(OSError):
            self.control.mutate_route(self.payload(), 'b'*32)
        self.assertTrue(self.control.pending.exists())
        with self.assertRaises(OperationConflict):
            require_recovery_clear(self.root)
        require_recovery_clear(self.root, 'relay-reconcile')
        self.agent.offline = False
        recovered = RelayManager(self.root, ORIGIN, lambda *_: self.agent)
        recovered.reconcile()
        self.assertEqual(len(recovered.read()['routes']), 1)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)

    def test_pending_operation_can_reconnect_same_host_without_replaying_mutation(self):
        self.agent.offline_after_write = True
        with self.assertRaises(OSError):
            self.control.mutate_route(self.payload(), 'b'*32)
        self.agent.offline = False
        host = self.control.read()['hosts'][0]
        with self.assertRaises(OperationConflict):
            self.control.save_host({**host, 'id': 'f'*32, 'expected_revision': self.control.read()['revision']}, reconnect=True)
        self.control.save_host({**host, 'token': 'n'*48, 'expected_revision': self.control.read()['revision']}, reconnect=True)
        self.assertFalse(self.control.pending.exists())
        self.assertEqual(self.control.read()['hosts'][0]['token'], 'n'*48)
        self.assertEqual(len(self.control.read()['routes']), 1)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)

    def test_unknown_write_with_absent_readback_keeps_intent(self):
        self.agent.request = Mock(side_effect=[{'revision': 0, 'items': []}, OSError('lost'), {'revision': 0, 'items': []}])
        with self.assertRaises(OperationConflict):
            self.control.mutate_route(self.payload(), 'b'*32)
        self.assertTrue(self.control.pending.exists())

    def test_delete_response_loss_is_reconciled_without_another_delete(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.agent.lose_ack = True
        self.control.mutate_route({**self.payload(), 'route_id': 'pro-'+'b'*32}, 'c'*32, delete=True)
        self.assertFalse(self.control.pending.exists())
        self.assertEqual(sum(method == 'DELETE' for method, _ in self.agent.calls), 1)

    def test_rejected_write_is_confirmed_absent_and_does_not_claim_success(self):
        self.agent.reject = True
        with self.assertRaises(OperationConflict):
            self.control.mutate_route(self.payload(), 'b'*32)
        self.assertFalse(self.control.pending.exists())
        self.assertEqual(self.control.read()['routes'], [])

    def test_revision_port_and_foreign_drift_prevent_writes(self):
        for key, value in (('expected_revision', 'f'*32), ('agent_revision', 999)):
            with self.subTest(key=key), self.assertRaises(OperationConflict):
                self.control.mutate_route({**self.payload(), key: value}, 'b'*32)
        self.control.mutate_route(self.payload(), 'b'*32)
        with self.assertRaises(OperationConflict):
            self.control.mutate_route(self.payload(), 'c'*32)
        self.agent.items[0]['target_port'] = 8443
        with self.assertRaises(OperationConflict):
            self.control.mutate_route({**self.payload(), 'route_id': 'pro-'+'b'*32}, 'd'*32, delete=True)
        self.assertFalse(any(method == 'DELETE' for method, _ in self.agent.calls))
        self.assertEqual(self.control.public()['hosts'][0]['routes'][0]['state'], 'drift')

    def test_unavailable_agent_preserves_routes_as_unknown(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.agent.offline = True
        host = self.control.public()['hosts'][0]
        self.assertEqual(host['state'], 'unknown')
        self.assertEqual(host['routes'][0]['state'], 'unknown')
        self.assertNotIn('sent_bytes', host['routes'][0])

    def test_node_with_owned_routes_cannot_be_forgotten(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        with self.assertRaises(OperationConflict):
            self.control.delete_host({'host_id': self.host_id, 'expected_revision': self.control.read()['revision']})

    def test_malformed_agent_sources_are_unknown_not_empty(self):
        for result in ({'revision': 0}, {'revision': False, 'items': []}, {'revision': 0, 'items': [{'id': 'bad'}]}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                route_snapshot(result)
        with self.assertRaises(ValueError):
            status_snapshot({'api_version': 1})

    def test_pin_mismatch_sends_no_http_or_token(self):
        socket = Mock()
        socket.getpeercert.return_value = b'other certificate'
        connection = Mock(sock=socket)
        with patch('relay_client.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaises(AgentAPIError):
                RelayClient(f'https://{AGENT}:9443', TOKEN, PIN).request('GET', '/v1/status')
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_pin_verified_before_auth_and_redirect_not_followed(self):
        socket = Mock()
        socket.getpeercert.return_value = b'certificate'
        connection = Mock(sock=socket)
        connection.getresponse.return_value.status = 302
        connection.getresponse.return_value.read.return_value = b'{}'
        with patch('relay_client.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaises(AgentAPIError):
                RelayClient(f'https://{AGENT}:9443', TOKEN, hashlib.sha256(b'certificate').hexdigest()).request('GET', '/v1/status')
        self.assertEqual(connection.request.call_count, 1)
        self.assertEqual(connection.request.call_args.args[3]['Authorization'], 'Bearer '+TOKEN)

    def test_nonpublic_agent_and_invalid_urls_rejected(self):
        for url in ('http://'+AGENT+':9443', 'https://127.0.0.1:9443', 'https://10.0.0.1:9443', 'https://example.com:9443', f'https://{AGENT}:9443/path'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                RelayClient(url, TOKEN, PIN)


class RelayExportTests(unittest.TestCase):
    def test_native_copies_preserve_credentials_and_tls_identity(self):
        cases = [
            ('udp', 51820, '[Interface]\nPrivateKey = secret\n[Peer]\nPublicKey = pub\nEndpoint = '+ORIGIN+':51820\n', 'secret'),
            ('tcp', 443, 'vless://secret@origin.example:443?security=tls&type=ws&path=%2Fx#name', 'sni=origin.example'),
            ('tcp', 443, json.dumps({'outbounds': [{'type': 'vless', 'server': 'origin.example', 'server_port': 443, 'uuid': 'secret', 'tls': {'enabled': True}}]}), 'origin.example'),
            ('tcp', 443, 'proxies:\n- name: test\n  type: vless\n  server: origin.example\n  port: 443\n  uuid: secret\n  tls: true\n', 'origin.example'),
        ]
        for transport, port, source, expected in cases:
            with self.subTest(transport=transport, source=source):
                result, count = relay_export.rewrite(source, {'transport': transport, 'target_port': port, 'listen_port': 20001}, AGENT, [ORIGIN, 'origin.example'])
                self.assertEqual(count, 1)
                self.assertIn(AGENT, result)
                self.assertIn(expected, result)
                self.assertIn('secret', result)

    def test_mismatch_or_fallback_never_silently_produces_relay_export(self):
        route = {'transport': 'udp', 'target_port': 8444, 'listen_port': 20001}
        for source in ('vless://secret@origin.example:443?security=tls', '{"outbounds":[{"type":"tuic","server":"other.example","server_port":8444}]}', 'not a config'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                relay_export.rewrite(source, route, AGENT, [ORIGIN, 'origin.example'])


class RelayAPITests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(api, 'ACTION_FILE', self.root/'application-action.json'))
        self.client = TestClient(api.app)
        self.addCleanup(api.app.dependency_overrides.clear)

    def test_every_endpoint_requires_auth_before_access_or_validation(self):
        endpoints = [('GET', '/network/relays'), ('PUT', '/network/relays'), ('PUT', '/network/relays/reconnect'), ('DELETE', '/network/relays/id'),
                     ('POST', '/network/relays/id/routes'), ('DELETE', '/network/relays/id/routes/route?agent_revision=0'),
                     ('POST', '/network/relays/reconcile'), ('POST', '/network/relays/id/routes/route/export'),
                     ('POST', '/network/relays/id/routes/route/check?consumer_id=direct:client')]
        for method, path in endpoints:
            with self.subTest(path=path):
                response = self.client.request(method, '/api'+path)
                self.assertEqual(response.status_code, 401)

    def test_cdn_native_copy_retains_gateway_sni_and_http_host(self):
        with patch.object(api, 'PUBLIC_IPV4', ORIGIN), patch.object(api, 'PUBLIC_DOMAIN', 'panel.example'), patch.object(api, 'PUBLIC_ENDPOINT', ORIGIN), patch.object(api.cdn_security, 'read_routes', return_value=[{'domain': 'cdn.example'}]), patch.object(api, 'VLESS_ENV', self.root/'absent.env'):
            hosts = api.relay_origin_hosts()
        original = 'proxies:\n- name: phone\n  type: vless\n  server: cdn.example\n  port: 443\n  uuid: secret\n  tls: true\n  servername: cdn.example\n  ws-opts:\n    path: /private-path\n    headers:\n      Host: cdn.example\n'
        output, count = relay_export.rewrite(original, {'transport': 'tcp', 'target_port': 443, 'listen_port': 20000}, AGENT, hosts)
        self.assertEqual(count, 1)
        import yaml
        node = yaml.safe_load(output)['proxies'][0]
        self.assertEqual((node['server'], node['port']), (AGENT, 20000))
        self.assertEqual(node['servername'], 'cdn.example')
        self.assertEqual(node['ws-opts']['headers']['Host'], 'cdn.example')
        self.assertEqual(node['uuid'], 'secret')

    def test_public_proof_uses_private_identity_without_returning_credentials(self):
        agent = FakeAgent()
        control = RelayManager(self.root, ORIGIN, lambda *_: agent)
        control.save_host({'name': 'Relay', 'agent_url': f'https://{AGENT}:9443', 'token': TOKEN, 'certificate_sha256': PIN, 'expected_revision': '0'*32})
        host = control.read()['hosts'][0]
        control.mutate_route({'host_id': host['id'], 'transport': 'tcp', 'target_port': 443, 'listen_port': 20001,
                              'agent_revision': agent.revision, 'expected_revision': control.read()['revision']}, 'f'*32)
        registry = control.read()
        registry['routes'][0]['check'] = {'state': 'ready', 'consumer_id': 'direct:client', 'checked_at': '2026-10-15T00:00:00Z', 'vantage_point': 'server', 'plan_seal': 'sealed'}
        control.write(registry)
        api.app.dependency_overrides[api.require_token] = lambda: None
        def prepare(route, private_host, consumer):
            self.assertEqual(private_host['token'], TOKEN)
            return object()
        with patch.object(api, 'relay_control', return_value=control), patch.object(api, 'relay_targets', return_value=[]), patch.object(api, 'network_consumers', return_value=[]), patch.object(api, 'read_clients', return_value=[]), patch.object(api, 'prepare_relay_probe', side_effect=prepare), patch.object(api.network_probe, 'evidence_key', return_value=b'x'*32), patch.object(api.network_probe, 'plan_seal', return_value='sealed') as seal:
            response = self.client.get('/api/network/relays')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['hosts'][0]['routes'][0]['check']['state'], 'ready')
            self.assertNotIn(TOKEN, response.text)
            self.assertNotIn('plan_seal', response.text)
            seal.return_value = 'changed'
            changed = self.client.get('/api/network/relays')
            self.assertEqual(changed.json()['hosts'][0]['routes'][0]['check']['state'], 'unknown')
        self.assertEqual(control.read()['routes'][0]['check']['state'], 'ready')

    def test_host_save_is_durable_and_does_not_put_token_in_command_or_history(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        with patch.object(api.subprocess, 'run', return_value=Mock(returncode=0)), patch.object(api, 'observe_service', return_value={}):
            response = self.client.put('/api/network/relays', json={'name': 'Relay', 'agent_url': f'https://{AGENT}:9443', 'token': TOKEN, 'certificate_sha256': PIN, 'expected_revision': '0'*32}, headers={'X-Operation-ID': 'e'*32})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn(TOKEN, response.text)
        history = (self.root/'operations'/('e'*32+'.json')).read_text()
        self.assertNotIn(TOKEN, history)
        self.assertEqual(response.json()['id'], 'e'*32)

    def test_native_probe_uses_remote_port_but_retains_tls_identity_and_exact_consumer(self):
        route = {'id': 'pro-'+'f'*32, 'transport': 'tcp', 'target_ip': ORIGIN, 'target_port': 443, 'listen_port': 20001}
        host = {'agent_url': f'https://{AGENT}:9443', 'token': TOKEN, 'certificate_sha256': PIN}
        plan = ProbePlan('binding', 1, 'direct:client', 'origin.example', 443, 'vless-tls', 10088, 'ws', 'fingerprint',
                         {'protocol': 'vless', 'settings': {'vnext': [{'address': 'origin.example', 'port': 443, 'users': [{'id': 'secret'}]}]}, 'streamSettings': {'tlsSettings': {'serverName': 'origin.example'}}})
        with patch.object(api, 'PUBLIC_IPV4', ORIGIN), patch.object(api, 'read_route_registry', return_value={'bindings': [{'state': 'active'}]}), patch.object(api, 'prepare_network_probe', return_value=(plan, {})), patch.object(api, 'read_clients', return_value=[]):
            actual = api.prepare_relay_probe(route, host, 'direct:client')
            self.assertEqual(actual.client['settings']['vnext'][0]['address'], AGENT)
            self.assertEqual(actual.client['settings']['vnext'][0]['port'], 20001)
            self.assertEqual(actual.client['streamSettings']['tlsSettings']['serverName'], 'origin.example')
            self.assertEqual(plan.client['settings']['vnext'][0]['address'], 'origin.example')
            with self.assertRaises(ValueError):
                api.prepare_relay_probe(route, host, 'direct:another-client')

    def test_recovery_blocks_other_mutations_but_allows_readonly_reconcile(self):
        (self.root/'relay').mkdir()
        (self.root/'relay/pending.json').write_text('{}')
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.assertEqual(self.client.put('/api/network/relays', json={}).status_code, 409)
        with patch.object(api.subprocess, 'run', return_value=Mock(returncode=0)):
            response = self.client.post('/api/network/relays/reconcile', headers={'X-Operation-ID': 'e'*32})
        self.assertEqual(response.status_code, 202, response.text)


if __name__ == '__main__':
    unittest.main()
