import copy
import hashlib
import json
import socket
from pathlib import Path
import tempfile
import unittest
import yaml
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

from tests.api.support import api
from tests.api.support import manager
from relay_manager import RelayManager, route_snapshot, status_snapshot
from relay_client import RelayClient, AgentAPIError
from application_operation import OperationConflict, require_recovery_clear
from network_probe import ProbePlan
import relay_export
import relay_connections
import relay_cdn
import relay_client_creation
import port_allocation
from types import SimpleNamespace

ORIGIN = '195.14.118.112'
AGENT = '85.209.155.229'
TOKEN = 's'*48
PIN = 'a'*64


class FakeAgent:
    def __init__(self):
        self.host = AGENT
        self.port = 9443
        self.revision = 0
        self.items = []
        self.calls = []
        self.offline = False
        self.lose_ack = False
        self.offline_after_write = False
        self.reject = False
        self.addresses = None

    def request(self, method, path, body=None, revision=None):
        self.calls.append((method, path))
        if self.offline:
            raise OSError('offline')
        if method == 'GET':
            if path == '/v1/status':
                return {'api_version': 1, 'version': '1.0.0', 'revision': self.revision, 'routes': len(self.items), 'failed_routes': 0, 'transports': ['tcp', 'udp'], 'address_family': 'ipv4', 'listen_ports': {'min': 20000, 'max': 20999}, 'max_routes': 64, **({'addresses':self.addresses,'client_address_families':['ipv4','ipv6']} if self.addresses else {})}
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
    def test_first_hop_cdn_keeps_protocol_transport_and_tls_in_yaml_export(self):
        for kind in ('vless', 'vmess', 'trojan'):
            for transport in ('websocket', 'xhttp', 'grpc', 'httpupgrade'):
                with self.subTest(protocol=kind, transport=transport):
                    credential = {'xray_protocol':kind, 'uuid':'secret-id', 'password':'secret-password', 'cdn_enabled':True,
                                  'cdn_domain':'cdn.example', 'cdn_path':'/private', 'cdn_transport':transport, 'cdn_xhttp_mode':'packet-up'}
                    settings = {'xray_protocol':kind, 'route_mode':'cdn', 'cdn_domain':'cdn.example', 'cdn_transport':transport,
                                'relay_host_id':self.host_id, 'relay_cdn':True, 'relay_cdn_mode':'before_cdn'}
                    connection = {'id':'cdn', 'name':'CDN', 'component':'transport-reality', 'credential':credential, 'settings':settings}
                    with patch.object(relay_cdn, 'resolve', return_value='104.16.1.2'):
                        relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
                    routes = self.control.read()['routes']
                    self.assertEqual([(row['target_ip'],row['target_port'],row['cdn_domain']) for row in routes], [('104.16.1.2',443,'cdn.example')])
                    port = routes[0]['listen_port']
                    original = copy.deepcopy(connection)
                    lines = relay_connections.redirect_mihomo_lines(self.control, 'transport-reality', settings, manager.render_vless_cdn(credential, 'CDN'))
                    proxy = yaml.safe_load('proxies:\n'+'\n'.join(lines))['proxies'][0]
                    self.assertEqual((proxy['server'],proxy['port'],proxy['servername']), (AGENT,port,'cdn.example'))
                    self.assertEqual(proxy['type'], kind)
                    self.assertEqual(connection,original)

    def test_cdn_target_is_validated_pinned_and_does_not_reuse_origin_socket(self):
        self.control.ensure(self.host_id, [[('tcp',443)]], 'c'*32)
        dns = lambda address: [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
        for address in ('10.0.0.1',ORIGIN,AGENT,'8.8.8.8'):
            with self.subTest(address=address), patch.object(relay_cdn.socket,'getaddrinfo',return_value=dns(address)), self.assertRaises(ValueError):
                self.control.ensure_cdn(self.host_id,'cdn.example','c'*32)
        self.assertEqual(len(self.control.read()['routes']),1)
        with patch.object(relay_cdn.socket,'getaddrinfo',return_value=dns('104.16.1.2')):
            self.control.ensure_cdn(self.host_id,'cdn.example','d'*32)
        before = self.control.read()
        self.assertNotEqual(self.control.endpoint(self.host_id,'tcp',443)[1],self.control.cdn_endpoint(self.host_id,'cdn.example')[1])
        self.agent.calls.clear()
        with patch.object(relay_cdn.socket,'getaddrinfo',side_effect=AssertionError('DNS during export')):
            self.control.cdn_endpoint(self.host_id,'cdn.example')
            self.assertEqual(self.agent.calls,[])
            self.control.ensure_cdn(self.host_id,'cdn.example','e'*32)
        self.assertEqual(before,self.control.read())

    def test_lost_cdn_ack_recovers_exact_pinned_target_without_replay(self):
        self.agent.lose_ack = True
        self.agent.offline_after_write = True
        with patch.object(relay_cdn,'resolve',return_value='104.16.1.2'), self.assertRaises(OSError):
            self.control.ensure_cdn(self.host_id,'cdn.example','d'*32)
        self.agent.offline = False
        self.control.reconcile()
        self.assertEqual(self.control.cdn_route(self.host_id,'cdn.example')['target_ip'],'104.16.1.2')
        self.assertEqual(len([call for call in self.agent.calls if call[0]=='PUT']),1)

    def test_cdn_nonce_is_bound_to_exact_relay_and_preserves_sni(self):
        from dataclasses import replace
        with patch.object(relay_cdn,'resolve',return_value='104.16.1.2'):
            self.control.ensure_cdn(self.host_id,'cdn.example','d'*32)
        plan = ProbePlan('cdn',1,'direct:phone','cdn.example',443,'cdn',10087,'ws','original',
                         {'protocol':'trojan','settings':{'servers':[{'address':'cdn.example','port':443,'password':'secret'}]},
                          'streamSettings':{'network':'ws','tlsSettings':{'serverName':'cdn.example'},'wsSettings':{'path':'/private','headers':{'Host':'cdn.example'}}}},
                         relay_host_id=self.host_id,relay_target_domain='cdn.example')
        with patch.object(api,'relay_control',return_value=self.control):
            actual = api.prepare_client_relay_probe(plan)
            self.assertEqual(actual.client['settings']['servers'][0]['address'],AGENT)
            self.assertEqual(actual.client['streamSettings'],plan.client['streamSettings'])
            self.assertNotEqual(actual.fingerprint,plan.fingerprint)
            saved = self.control.read()
            saved['routes'][0]['check'] = {'state':'ready'}
            self.control.write(saved)
            self.assertEqual(api.prepare_client_relay_probe(plan).fingerprint,actual.fingerprint)
            changed = api.prepare_client_relay_probe(replace(plan,relay_client_address='relay.example'))
            self.assertNotEqual(changed.fingerprint,actual.fingerprint)
            self.assertEqual(changed.address,'relay.example')
            host = self.control.host(self.control.read(), self.host_id)
            route = self.control.cdn_route(self.host_id,'cdn.example')
            with patch.object(api,'read_route_registry',return_value={'bindings':[{'state':'active'}]}), patch.object(api,'prepare_network_probe',return_value=(actual,{})):
                checked = api.prepare_relay_probe(route,host,plan.consumer_id)
                self.assertEqual(checked.binding_id,route['id'])
                self.assertEqual(checked.address,AGENT)
                with self.assertRaises(api.network_probe.ProbeUnavailable):
                    api.prepare_relay_probe({**route,'id':'pro-'+'e'*32},host,plan.consumer_id)

    def test_direct_cdn_worker_captures_once_and_finishes_pinned_relay_after_lost_ack(self):
        identity = 'c'*32
        settings = {'relay_host_id':self.host_id, 'xray_protocol':'trojan'}
        client = {'id':identity[:16], 'protocol':'vless-reality-xhttp', 'settings':settings, 'route_endpoints':{'cdn':'cdn.example'}}
        outbound = {'protocol':'trojan', 'settings':{'servers':[{'address':'cdn.example','port':443,'password':'secret'}]},
                    'streamSettings':{'network':'grpc','tlsSettings':{'serverName':'cdn.example'},'grpcSettings':{'serviceName':'private'}}}
        native_result = {'id':identity[:16], 'config':json.dumps({'outbounds':[outbound]}), 'filename':'cdn.json',
                         'profiles':[{'id':'cdn','config':json.dumps({'outbounds':[outbound]})}]}
        native = Mock(return_value=native_result)
        adapter = SimpleNamespace(DATA_DIR=self.root,relay_control=lambda:self.control,ClientCreate=api.ClientCreate,
                                  create_client_native=native,ComponentRegistry=lambda *_:Mock(),read_clients=lambda:[client])
        payload = {'client':{'name':'Phone','protocol':'vless-reality-xhttp','settings':settings,'vless_routes':['cdn']},'expected_revision':self.control.read()['revision']}
        self.agent.offline_after_write = True
        with patch.object(relay_cdn,'resolve',return_value='104.16.1.2'), self.assertRaises(OSError):
            relay_client_creation.perform(adapter,payload,identity)
        self.assertTrue(relay_client_creation.read_result(adapter,identity)['can_resume'])
        self.agent.offline = self.agent.offline_after_write = False
        self.control.reconcile()
        with patch.object(relay_cdn,'resolve',side_effect=AssertionError('DNS during recovery')):
            relay_client_creation.perform(adapter,{'expected_revision':self.control.read()['revision']},'d'*32,resume_id=identity)
        result = relay_client_creation.read_result(adapter,identity)
        self.assertEqual(result['state'],'ready')
        node = json.loads(result['result']['config'])['outbounds'][0]
        self.assertEqual(node['settings']['servers'][0],{'address':AGENT,'port':20000,'password':'secret'})
        self.assertEqual(node['streamSettings'],outbound['streamSettings'])
        self.assertEqual(native.call_count,1)
        self.assertEqual(len(self.control.read()['routes']),1)

    def test_cdn_relay_preserves_each_transport_in_yaml_export(self):
        for transport in ('websocket', 'xhttp', 'grpc', 'httpupgrade'):
            with self.subTest(transport=transport):
                credential = {'uuid': 'key-value', 'cdn_enabled': True, 'cdn_domain': 'cdn.example',
                              'cdn_path': '/private', 'cdn_transport': transport, 'cdn_xhttp_mode': 'packet-up'}
                connection = {'id': 'cdn', 'name': 'CDN', 'component': 'transport-reality', 'credential': credential,
                              'settings': {'route_mode': 'cdn', 'cdn_domain': 'cdn.example', 'cdn_transport': transport,
                                           'relay_host_id': self.host_id, 'relay_cdn': True}}
                relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
                self.assertEqual({(r['transport'], r['target_port']) for r in self.control.read()['routes']}, {('tcp', 443), ('tcp', 80)})
                source = copy.deepcopy(connection)
                lines = relay_connections.redirect_mihomo_lines(self.control, connection['component'], connection['settings'], manager.render_vless_cdn(credential, 'CDN'))
                node = yaml.safe_load('proxies:\n'+'\n'.join(lines))['proxies'][0]
                self.assertEqual((node['server'], node['port'], node['servername']), ('cdn.example', 443, 'cdn.example'))
                self.assertEqual(node['network'], 'ws' if transport in {'websocket', 'httpupgrade'} else transport)
                if transport == 'httpupgrade': self.assertTrue(node['ws-opts']['v2ray-http-upgrade'])
                if transport == 'xhttp': self.assertEqual(node['xhttp-opts'], {'path':'/private', 'mode':'packet-up'})
                if transport == 'grpc': self.assertEqual(node['grpc-opts']['grpc-service-name'], 'private')
                self.assertEqual(connection, source)

    def test_cdn_relay_reuses_https_and_acme_ingress_and_rejects_other_port80_consumers(self):
        credential = {'uuid': 'key-value', 'cdn_enabled': True, 'cdn_domain': 'cdn.example', 'cdn_path': '/private', 'cdn_transport': 'websocket'}
        connection = {'id': 'cdn', 'name': 'CDN', 'component': 'transport-reality', 'credential': credential,
                      'settings': {'route_mode': 'cdn', 'cdn_domain': 'cdn.example', 'relay_host_id': self.host_id, 'relay_cdn': True}}
        relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
        rules = self.control.read()['routes']
        self.assertEqual({(r['transport'], r['target_port']) for r in rules}, {('tcp', 443), ('tcp', 80)})
        self.assertTrue(all(20000 <= r['listen_port'] <= 20999 for r in rules))
        self.agent.offline = True
        relay_connections.prepare_mihomo(self.control, [connection], 'd'*32, previous=[connection])
        with self.assertRaises(ValueError):
            relay_connections.selected_endpoint(self.control, connection['settings'], 'udp', 443)
        self.agent.offline = False
        with self.assertRaises(ValueError):
            self.control.ensure(self.host_id, [[('tcp',80)]], 'd'*32)
        with self.assertRaises(ValueError):
            self.control.ensure(self.host_id, [[('udp',80)]], 'd'*32, allow_acme=True)
        unsafe = {'id':'ss','component':'transport-shadowsocks','credential':{'port':80},'settings':{'relay_host_id':self.host_id}}
        with self.assertRaises(ValueError):
            relay_connections.prepare_mihomo(self.control, [connection, unsafe], 'e'*32)

    def test_address_refresh_discovers_changes_without_json_routes_or_registry_mutation(self):
        self.control.ensure(self.host_id, [[('tcp', 443)]], 'c'*32)
        before = self.control.read()
        self.agent.addresses = {'ipv4': [AGENT, '8.8.4.4'], 'ipv6': [], 'domains': ['new.example']}
        self.agent.calls.clear()
        host = self.control.public(addresses_only=True)['hosts'][0]
        self.assertEqual(self.agent.calls, [('GET', '/v1/status')])
        self.assertEqual(host['addresses'], self.agent.addresses)
        self.assertEqual(self.control.read(), before)
        self.assertEqual(host['state'], 'unknown')
        self.assertEqual(host['routes'][0]['state'], 'unknown')
        self.agent.offline = True
        cached = self.control.public(addresses_only=True)['hosts'][0]
        self.assertEqual(cached['addresses'], self.agent.addresses)
        self.assertEqual(cached['state'], 'unknown')
        self.agent.calls.clear()
        self.assertEqual(self.control.public(probe=False)['hosts'][0]['addresses'], self.agent.addresses)
        self.assertEqual(self.agent.calls, [])

    def test_excluding_one_address_preserves_host_routes_and_survives_refresh_and_reconnect(self):
        self.agent.addresses = {'ipv4': [AGENT, '8.8.4.4'], 'ipv6': [], 'domains': []}
        self.control.public()
        self.control.ensure(self.host_id, [[('tcp', 443)]], 'c'*32)
        before = self.control.read()
        self.agent.calls.clear()
        self.control.exclude_address({'host_id': self.host_id, 'address': AGENT, 'expected_revision': before['revision']})
        self.assertEqual(self.agent.calls, [])
        after = self.control.read()
        self.assertEqual(after['routes'], before['routes'])
        self.assertEqual(self.control.endpoint(self.host_id, 'tcp', 443)[0], AGENT)
        for probe in (False, True):
            host = self.control.public(probe=probe)['hosts'][0]
            self.assertEqual(host['excluded_addresses'], [AGENT])
            self.assertIn('8.8.4.4', host['addresses']['ipv4'])
        self.control.save_host({**after['hosts'][0], 'expected_revision': after['revision']})
        self.assertEqual(self.control.read()['hosts'][0]['excluded_addresses'], [AGENT])
        with self.assertRaises(OperationConflict):
            self.control.exclude_address({'host_id': self.host_id, 'address': '8.8.4.4', 'expected_revision': before['revision']})
        revision = self.control.read()['revision']
        with self.assertRaises(ValueError):
            self.control.exclude_address({'host_id': self.host_id, 'address': '1.1.1.1', 'expected_revision': revision})
        self.control.exclude_address({'host_id': self.host_id, 'address': AGENT, 'restore': True, 'expected_revision': revision})
        self.assertEqual(self.control.public(probe=False)['hosts'][0]['excluded_addresses'], [])

    def test_inventory_rejects_private_addresses_and_api_accepts_public_ipv6_with_pinning(self):
        status=self.agent.request('GET','/v1/status')
        for addresses in ({'ipv4':['10.0.0.1']},{'ipv6':['::1']},{'domains':['bad/host']},{'ipv4':['2606:4700::1111']},{'domains':['relay.example'],'cdn_domains':['other.example']},{'cdn_domains':'relay.example'}):
            with self.subTest(addresses=addresses),self.assertRaises(ValueError):status_snapshot({**status,'addresses':addresses})
        client=RelayClient('https://[2606:4700::1111]:9444',TOKEN,PIN)
        self.assertEqual((client.host,client.port),('2606:4700::1111',9444))
        for url in ('https://[::1]:9443','https://[fc00::1]:9443','https://[::ffff:8.8.8.8]:9443','https://8.8.8.8:20000'):
            with self.subTest(url=url),self.assertRaises(ValueError):RelayClient(url,TOKEN,PIN)

    def test_all_agent_addresses_are_cached_without_live_checks_or_registry_revision_changes(self):
        self.agent.addresses = {'ipv4':[AGENT,'8.8.4.4'],'ipv6':['2606:4700::1111'],'domains':['relay.example','backup.example'],'cdn_domains':['backup.example']}
        revision = self.control.read()['revision']
        self.control.public()
        self.assertEqual(self.control.read()['revision'],revision)
        self.agent.calls.clear()
        inventory=self.control.public(probe=False)
        self.assertEqual(inventory['hosts'][0]['addresses'],self.agent.addresses)
        self.assertEqual(self.agent.calls,[])
        host=self.control.read()['hosts'][0]
        with patch('relay_manager.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(AGENT,0))]):
            self.control.save_host({**host,'client_address':'backup.example','expected_revision':revision})
        self.assertEqual(self.control.read()['hosts'][0]['client_address'],'backup.example')

    def test_selected_ipv6_address_survives_exports_without_changing_other_connections(self):
        self.control.ensure(self.host_id,[[('tcp',443)],[('udp',51820)]],'c'*32)
        settings={'relay_host_id':self.host_id,'relay_address':'2606:4700::1111'}
        result=relay_connections.direct_result(self.control,'vless-reality-xhttp',settings,{'config':'vless://user@195.14.118.112:443?security=reality','filename':'test.txt'})
        self.assertIn('@[2606:4700::1111]:',result['config'])
        wg=relay_connections.direct_result(self.control,'wg',settings,{'config':'[Interface]\n[Peer]\nEndpoint = 195.14.118.112:51820\n','filename':'test.conf'})
        self.assertIn('Endpoint = [2606:4700::1111]:',wg['config'])
        self.assertEqual(self.control.endpoint(self.host_id,'tcp',443)[0],AGENT)
        with self.assertRaises(ValueError):
            self.control.endpoint(self.host_id,'tcp',443,address='::1')

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

    def test_allocator_skips_transient_udp_measurement_ports(self):
        request = self.agent.request
        def observed(method, path, *args, **kwargs):
            result = request(method, path, *args, **kwargs)
            if method == 'GET' and path == '/v1/routes': result['reserved_udp_ports'] = [20000]
            return result
        with patch.object(self.agent, 'request', side_effect=observed):
            self.control.ensure(self.host_id, [[('udp', 443)]], 'a'*32)
        route = self.control.read()['routes'][0]
        self.assertEqual(route['listen_port'], 20001)

    def test_inventory_never_probes_or_returns_saved_readiness(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        registry = self.control.read()
        registry['routes'][0]['check'] = {'state': 'ready', 'consumer_id': 'direct:client',
                                        'checked_at': '2026-10-17T00:00:00Z', 'vantage_point': 'server', 'plan_seal': 'private'}
        self.control.write(registry)
        self.agent.calls.clear()
        self.agent.offline = True
        snapshot = self.control.public(probe=False)
        host = snapshot['hosts'][0]
        route = host['routes'][0]
        self.assertEqual(self.agent.calls, [])
        self.assertEqual(host['state'], 'unknown')
        self.assertEqual(route['state'], 'unknown')
        self.assertFalse(route['target_verified'])
        for key in ('check', 'sent_bytes', 'received_bytes', 'sessions', 'errors'):
            self.assertNotIn(key, route)
        self.assertNotIn('version', host)
        self.assertNotIn('agent_revision', host)
        self.assertNotIn(TOKEN, json.dumps(snapshot))
        self.assertNotIn('private', json.dumps(snapshot))

    def test_protocol_preparation_reuses_owned_rule_without_mutation(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.control.ensure(self.host_id, [[('tcp', 443)], [('tcp', 443)]], 'c'*32)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)
        self.assertEqual(self.control.endpoint(self.host_id, 'tcp', 443), (AGENT, 20001))

    def test_tcp_udp_pair_uses_one_free_port_without_touching_foreign_rules(self):
        foreign = {'id': 'foreign', 'transport': 'udp', 'listen_port': 20000, 'target_ip': ORIGIN, 'target_port': 31337, 'state': 'listening', 'target_verified': False}
        self.agent.items.append(foreign)
        self.control.ensure(self.host_id, [[('tcp', 31000), ('udp', 31000)]], 'c'*32)
        self.assertIn(foreign, self.agent.items)
        self.assertEqual(self.control.endpoint(self.host_id, 'tcp', 31000, paired=True), (AGENT, 20001))
        self.assertEqual(self.control.endpoint(self.host_id, 'udp', 31000, paired=True), (AGENT, 20001))
        self.control.ensure(self.host_id, [[('tcp', 31000), ('udp', 31000)]], 'd'*32)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 2)

    def test_protocol_preparation_recovers_uncertain_pair_by_read_without_replay(self):
        self.agent.offline_after_write = True
        with self.assertRaises(OSError):
            self.control.ensure(self.host_id, [[('tcp', 31000), ('udp', 31000)]], 'c'*32)
        with self.assertRaises(OperationConflict):
            self.control.ensure(self.host_id, [[('tcp', 31000), ('udp', 31000)]], 'd'*32)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)
        self.agent.offline = self.agent.offline_after_write = False
        self.control.reconcile()
        self.control.ensure(self.host_id, [[('tcp', 31000), ('udp', 31000)]], 'e'*32)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 2)

    def test_missing_binding_never_falls_back_to_direct_and_restricted_ports_are_rejected(self):
        with self.assertRaises(OperationConflict):
            self.control.endpoint(self.host_id, 'tcp', 443)
        for port in (22, 80, 8000, 9443, 0, True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                self.control.ensure(self.host_id, [[('tcp', port)]], 'c'*32)
        self.assertFalse(any(method == 'PUT' for method, _ in self.agent.calls))

    def test_edit_can_preserve_domain_or_explicitly_return_to_agent_ip(self):
        host = self.control.read()['hosts'][0]
        payload = {**host, 'client_address': 'relay.example', 'expected_revision': self.control.read()['revision']}
        with patch('relay_manager.socket.getaddrinfo', return_value=[(2, 1, 6, '', (AGENT, 0))]):
            self.control.save_host(payload)
            self.control.save_host({**host, 'client_address': None, 'expected_revision': self.control.read()['revision']})
        self.assertEqual(self.control.read()['hosts'][0]['client_address'], 'relay.example')
        self.control.save_host({**host, 'client_address': '', 'expected_revision': self.control.read()['revision']})
        self.assertEqual(self.control.read()['hosts'][0]['client_address'], AGENT)

    def test_client_domain_requires_exact_agent_dns_and_does_not_expose_token(self):
        host = self.control.read()['hosts'][0]
        with patch('relay_manager.socket.getaddrinfo', return_value=[(2, 1, 6, '', (AGENT, 0))]):
            self.control.save_host({**host, 'client_address': 'Relay.Example', 'expected_revision': self.control.read()['revision']})
        self.assertEqual(self.control.public()['hosts'][0]['client_address'], 'relay.example')
        self.assertNotIn(TOKEN, json.dumps(self.control.public()))
        with patch('relay_manager.socket.getaddrinfo', side_effect=AssertionError('No recurring DNS gate')):
            self.control.ensure(self.host_id, [[('tcp', 443)]], 'c'*32)
        self.assertTrue(any(method == 'PUT' for method, _ in self.agent.calls))

    def test_advertised_hostname_still_requires_dns_to_match_the_selected_agent(self):
        self.agent.addresses = {'ipv4': [AGENT], 'ipv6': [], 'domains': ['relay.example']}
        self.control.public()
        host = self.control.read()['hosts'][0]
        original = copy.deepcopy(self.control.read())
        for answers in ([ORIGIN], [AGENT, '8.8.8.8'], []):
            with self.subTest(answers=answers), patch('relay_manager.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 0)) for ip in answers]):
                with self.assertRaises(ValueError):
                    self.control.save_host({**host, 'client_address': 'relay.example', 'expected_revision': original['revision']})
                self.assertEqual(self.control.read(), original)
        with patch('relay_manager.socket.getaddrinfo', side_effect=socket.gaierror('unavailable')):
            with self.assertRaises(ValueError):self.control.validate_client_address(self.host_id, 'relay.example')

    def test_new_connection_rejects_origin_or_wrong_domain_before_preparing_routes(self):
        self.agent.addresses = {'ipv4': [AGENT], 'ipv6': [], 'domains': ['relay.example']}
        self.control.public()
        original = copy.deepcopy(self.control.read())
        for address in (ORIGIN, 'relay.example'):
            connection = {'id': 'new', 'component': 'transport-tuic', 'credential': {'port': 10443},
                          'settings': {'relay_host_id': self.host_id, 'relay_address': address}}
            self.agent.calls.clear()
            with self.subTest(address=address), patch('relay_manager.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ORIGIN, 0))]):
                with self.assertRaises(ValueError):relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
            self.assertFalse(any(method != 'GET' for method, _ in self.agent.calls))
            self.assertEqual(self.control.read(), original)

    def test_saved_connection_and_export_do_not_add_a_recurring_dns_gate(self):
        self.agent.addresses = {'ipv4': [AGENT], 'ipv6': [], 'domains': ['relay.example']}
        self.control.public()
        connection = {'id': 'saved', 'component': 'transport-tuic', 'credential': {'port': 10443},
                      'settings': {'relay_host_id': self.host_id, 'relay_address': 'relay.example'}}
        with patch('relay_manager.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (AGENT, 0))]):
            relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
        with patch('relay_manager.socket.getaddrinfo', side_effect=AssertionError('No DNS during saved export/edit')):
            relay_connections.prepare_mihomo(self.control, [connection], 'd'*32, previous=[connection])
            self.assertEqual(self.control.endpoint(self.host_id, 'udp', 10443, address='relay.example')[0], 'relay.example')

    def test_orphan_origin_port_is_reserved_but_current_owner_can_keep_it(self):
        self.control.ensure(self.host_id, [[('tcp', 31000)]], 'c'*32)
        with patch.object(port_allocation, 'DATA_ROOT', self.root), patch.object(port_allocation, 'CONFIG_ROOT', self.root/'etc'), patch.object(port_allocation, 'ENV_FILE', self.root/'environment'):
            self.assertIn(31000, port_allocation.reserved_ports({'tcp'}))
            self.assertEqual(port_allocation.allocate('new-client', 31000, {'tcp'}, count=2, snapshot=lambda: set()), 31001)
            port_allocation.save_claim('original-client', 31000, {'tcp'})
            self.assertEqual(port_allocation.allocate('original-client', 31000, {'tcp'}, fixed=True, snapshot=lambda: set()), 31000)

    def test_automatic_direct_exports_keep_keys_and_tls_identity_for_native_protocols(self):
        cases = [
            ('wg', '[Interface]\nPrivateKey = key-value\n[Peer]\nEndpoint = '+ORIGIN+':51820\n', 'key-value'),
            ('awg', '[Interface]\nPrivateKey = key-value\nJc = 6\n[Peer]\nEndpoint = '+ORIGIN+':51822\n', 'Jc = 6'),
            ('openvpn', 'client\nproto tcp-client\nremote '+ORIGIN+' 1194\n<key>key-value</key>\n', '<key>key-value</key>'),
            ('vless-reality-xhttp', 'vless://key-value@origin.example:443?security=tls&sni=origin.example&type=ws&host=origin.example', 'sni=origin.example'),
            ('shadowsocks', 'ss://key-value@'+ORIGIN+':31000#name', 'key-value'),
            ('hysteria2', 'proxies:\n- name: test\n  type: hysteria2\n  server: '+ORIGIN+'\n  port: 8443\n  password: key-value\n  sni: origin.example\n', 'origin.example'),
            ('tuic', json.dumps({'outbounds': [{'type': 'tuic', 'server': ORIGIN, 'server_port': 8444, 'password': 'key-value', 'tls': {'enabled': True, 'server_name': 'origin.example'}}]}), 'origin.example'),
            ('trojan', json.dumps({'outbounds': [{'type': 'trojan', 'server': ORIGIN, 'server_port': 8445, 'password': 'key-value', 'tls': {'enabled': True, 'server_name': 'origin.example'}}]}), 'origin.example'),
        ]
        settings = {'relay_host_id': self.host_id, 'shadowsocks_mode': 'tcp_and_udp'}
        for protocol, config, retained in cases:
            with self.subTest(protocol=protocol):
                source = {'id': 'client', 'config': config, 'filename': 'client.conf'}
                self.control.ensure(self.host_id, relay_connections.direct_groups(protocol, settings, source), 'c'*32)
                result = relay_connections.direct_result(self.control, protocol, settings, source)
                self.assertIn(AGENT, result['config'])
                self.assertIn(retained, result['config'])
                self.assertEqual(source['config'], config)
        with self.assertRaises(ValueError):
            relay_connections.direct_groups('ikev2', settings, {'config': ''})

    def test_mihomo_yaml_redirect_only_selected_socket_preserving_host_and_sni(self):
        connection = {'id': 'cdn', 'name': 'CDN', 'component': 'transport-reality', 'settings': {'relay_host_id': self.host_id, 'route_mode': 'cdn'}, 'credential': {'uuid': 'key-value', 'cdn_enabled': True, 'cdn_domain': 'origin.example', 'cdn_path': '/private', 'cdn_transport': 'websocket'}}
        relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
        lines = manager.render_vless_cdn(connection['credential'], 'CDN')
        changed = relay_connections.redirect_mihomo_lines(self.control, connection['component'], connection['settings'], lines)
        self.assertIn('    server: "'+AGENT+'"', changed)
        self.assertIn('    servername: "origin.example"', changed)
        self.assertIn('        Host: "origin.example"', changed)
        before = sum(method == 'PUT' for method, _ in self.agent.calls)
        self.agent.offline = True
        relay_connections.prepare_mihomo(self.control, [{**connection, 'name': 'Renamed'}], 'd'*32, previous=[connection])
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), before)

    @patch('relay_manager.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (AGENT, 0))])
    def test_mihomo_tls_relay_domain_preserves_certificate_identity_in_yaml_export(self, _dns):
        self.agent.addresses = {'ipv4': [AGENT], 'ipv6': [], 'domains': ['relay.example', 'backup.example']}
        self.control.public()
        credential = {'uuid': 'key-value', 'route_mode': 'tls', 'tls_enabled': True, 'tls_domain': 'origin.example',
                      'tls_path': '/private', 'tls_transport': 'websocket', 'cdn_enabled': False}
        connection = {'id': 'tls', 'name': 'TLS', 'component': 'transport-reality',
                      'settings': {'relay_host_id': self.host_id, 'relay_address': 'relay.example', 'route_mode': 'tls'},
                      'credential': credential}
        original = copy.deepcopy(connection)
        relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
        for address in ('relay.example', 'backup.example'):
            selected = {**connection, 'settings': {**connection['settings'], 'relay_address': address}}
            with self.subTest(address=address):
                lines = relay_connections.redirect_mihomo_lines(self.control, connection['component'], selected['settings'], manager.render_vless_tls(credential, 'TLS'))
                node = yaml.safe_load('proxies:\n'+'\n'.join(lines))['proxies'][0]
                self.assertEqual((node['server'], node['port']), (address, 20000))
                self.assertEqual(node['servername'], 'origin.example')
                self.assertEqual(node['ws-opts']['headers']['Host'], 'origin.example')
                self.agent.calls.clear()
                relay_connections.prepare_mihomo(self.control, [selected], 'd'*32, previous=[connection])
                self.assertEqual(self.agent.calls, [])
        self.assertEqual(connection, original)

    @patch('relay_manager.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (AGENT, 0))])
    def test_mihomo_udp_relay_domain_keeps_each_protocol_socket_and_identity(self, _dns):
        self.agent.addresses = {'ipv4': [AGENT], 'ipv6': [], 'domains': ['relay.example']}
        self.control.public()
        base = {'server_address': ORIGIN, 'ip': '10.0.0.2/32', 'private_key': 'private', 'server_public_key': 'public', 'mtu': 1400,
                'password': 'password', 'uuid': '00000000-0000-4000-8000-000000000001', 'method': 'aes-128-gcm',
                'up_mbps': 100, 'down_mbps': 100, 'sni': 'cert.example', 'skip_cert_verify': False,
                'amnezia': {key: 1 for key in ('jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4')}}
        for index, component in enumerate(sorted(relay_connections.UDP_COMPONENTS | {'transport-shadowsocks'})):
            with self.subTest(component=component):
                credential = {**base, 'port': 31000+index}
                connection = {'id': component, 'name': component, 'component': component, 'credential': credential,
                              'settings': {'relay_host_id': self.host_id, 'relay_address': 'relay.example'}}
                original = copy.deepcopy(connection)
                relay_connections.prepare_mihomo(self.control, [connection], 'c'*32)
                address, port = self.control.endpoint(self.host_id, 'udp', credential['port'], paired=component == 'transport-shadowsocks', address='relay.example')
                lines = relay_connections.redirect_mihomo_lines(self.control, component, connection['settings'], manager.render_proxy(component, credential, component))
                proxy = yaml.safe_load('proxies:\n'+'\n'.join(lines))['proxies'][0]
                self.assertEqual((proxy['server'], proxy['port']), (address, port))
                if component in ('transport-tuic', 'transport-hysteria2'):
                    self.assertEqual(proxy['sni'], 'cert.example')
                    self.assertFalse(proxy['skip-cert-verify'])
                self.assertEqual(connection, original)

    def test_direct_worker_retains_native_result_and_resume_never_creates_another_client(self):
        identity = 'c'*32
        settings = {'relay_host_id': self.host_id}
        clients = []
        def native(payload, *, client_identity):
            clients.append({'id': client_identity, 'protocol': 'wg', 'settings': settings})
            return {'id': client_identity, 'filename': 'phone.conf', 'config': '[Interface]\nPrivateKey = key-value\n[Peer]\nEndpoint = '+ORIGIN+':51820\n'}
        native = Mock(side_effect=native)
        adapter = SimpleNamespace(DATA_DIR=self.root, relay_control=lambda: self.control, ClientCreate=api.ClientCreate, create_client_native=native, ComponentRegistry=lambda *_: Mock(), read_clients=lambda: clients)
        self.agent.offline_after_write = True
        payload = {'client': {'name': 'Phone', 'protocol': 'wg', 'settings': settings}, 'expected_revision': self.control.read()['revision']}
        with self.assertRaises(OSError):
            relay_client_creation.perform(adapter, payload, identity)
        pending = relay_client_creation.read_result(adapter, identity)
        self.assertTrue(pending['can_resume'])
        self.assertNotIn('key-value', json.dumps(pending))
        self.agent.offline = self.agent.offline_after_write = False
        self.control.reconcile()
        relay_client_creation.perform(adapter, {'expected_revision': self.control.read()['revision']}, 'd'*32, resume_id=identity)
        result = relay_client_creation.read_result(adapter, identity)
        self.assertEqual(result['state'], 'ready')
        self.assertIn(AGENT+':20000', result['result']['config'])
        self.assertIn('key-value', result['result']['config'])
        self.assertEqual(native.call_count, 1)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)
        clients.clear()
        path = relay_client_creation.result_path(adapter, identity)
        stored = json.loads(path.read_text()); stored['stage'] = 'native-created'; path.write_text(json.dumps(stored))
        clients.append({'id':stored['client_id'],'protocol':'wg','settings':{**settings,'relay_address':'relay.example'}})
        with self.assertRaises(OperationConflict):
            relay_client_creation.perform(adapter, {'expected_revision':self.control.read()['revision']}, 'f'*32, resume_id=identity)
        clients.clear()
        with self.assertRaises(OperationConflict):
            relay_client_creation.perform(adapter, {'expected_revision': self.control.read()['revision']}, 'e'*32, resume_id=identity)

    def test_direct_worker_never_repeats_an_interrupted_native_creation(self):
        adapter = SimpleNamespace(DATA_DIR=self.root, relay_control=lambda: self.control)
        path = relay_client_creation.result_path(adapter, 'c'*32)
        path.parent.mkdir(parents=True); path.write_text(json.dumps({'stage': 'creating-native'}))
        with self.assertRaises(OperationConflict):
            relay_client_creation.perform(adapter, {'expected_revision': self.control.read()['revision']}, 'd'*32, resume_id='c'*32)

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

    def unknown_put(self):
        original = self.agent.request
        def lost_before_apply(method, path, *args, **kwargs):
            if method == 'PUT':
                self.agent.calls.append((method, path))
                raise TimeoutError('lost before apply')
            return original(method, path, *args, **kwargs)
        with patch.object(self.agent, 'request', side_effect=lost_before_apply), self.assertRaises(OperationConflict):
            self.control.mutate_route(self.payload(), 'b'*32)
        return json.loads(self.control.pending.read_text())

    def test_explicit_resume_recovers_absent_put_with_exact_original_identity(self):
        intent = self.unknown_put()
        with patch.object(self.agent, 'request', wraps=self.agent.request) as request:
            self.control.resume('b'*32)
        writes = [call for call in request.call_args_list if call.args[0] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].args[1], '/v1/routes/'+intent['route']['id'])
        self.assertEqual(writes[0].kwargs['revision'], intent['agent_revision'])
        self.assertEqual(self.control.read()['routes'][0]['id'], intent['route']['id'])
        self.assertFalse(self.control.pending.exists())

    def test_resume_readback_finds_delayed_apply_without_another_write(self):
        intent = self.unknown_put()
        self.agent.request('PUT', '/v1/routes/'+intent['route']['id'], {key:intent['route'][key] for key in ('transport','listen_port','target_ip','target_port')}, revision=0)
        self.agent.calls.clear()
        self.control.resume('b'*32)
        self.assertTrue(all(method == 'GET' for method, _ in self.agent.calls))
        self.assertFalse(self.control.pending.exists())

    def test_resume_does_not_rebase_over_changed_revision_or_replay_deleted_intent(self):
        self.unknown_put()
        self.agent.revision = 1
        self.agent.calls.clear()
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        self.assertFalse(self.control.pending.exists())
        self.assertTrue(all(method == 'GET' for method, _ in self.agent.calls))
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)

    def test_resume_keeps_unknown_on_another_lost_reply_and_never_loops(self):
        self.unknown_put()
        self.agent.request = Mock(side_effect=[{'revision':0,'items':[]}, TimeoutError('lost'), {'revision':0,'items':[]}])
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        self.assertEqual([call.args[0] for call in self.agent.request.call_args_list], ['GET','PUT','GET'])
        self.assertTrue(self.control.pending.exists())

    def test_resume_rejects_wrong_intent_offline_drift_and_regressed_revision(self):
        intent = self.unknown_put()
        self.agent.calls.clear()
        with self.assertRaises(OperationConflict):
            self.control.resume('c'*32)
        self.assertEqual(self.agent.calls, [])
        self.agent.offline = True
        with self.assertRaises(OSError):
            self.control.resume('b'*32)
        self.agent.offline = False
        self.agent.items = [{**intent['route'], 'target_port':444, 'state':'listening', 'target_verified':False}]
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        self.assertTrue(self.control.pending.exists())

    def test_resume_error_does_not_treat_retry_rejection_as_original_rejection(self):
        self.unknown_put()
        self.agent.reject = True
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        intent = json.loads(self.control.pending.read_text())
        self.assertFalse(intent['rejected'])
        self.assertEqual(intent['last_error'], 'agent_http_409')
        public = self.control.public(probe=False)
        self.assertEqual(public['recovery']['host_id'], self.host_id)
        self.assertNotIn(TOKEN, json.dumps(public))

    def test_resume_delete_is_explicit_and_uses_original_revision(self):
        self.control.mutate_route(self.payload(), 'a'*32)
        original = self.agent.request
        with patch.object(self.agent, 'request', side_effect=lambda method,path,*args,**kwargs: (_ for _ in ()).throw(TimeoutError()) if method == 'DELETE' else original(method,path,*args,**kwargs)):
            with self.assertRaises(OperationConflict):
                self.control.mutate_route({**self.payload(),'route_id':'pro-'+'a'*32}, 'b'*32, delete=True)
        self.agent.calls.clear()
        self.control.resume('b'*32)
        self.assertEqual(sum(method == 'DELETE' for method,_ in self.agent.calls), 1)
        self.assertEqual(self.control.read()['routes'], [])
        self.assertFalse(self.control.pending.exists())

    def test_resume_refuses_agent_revision_rollback_and_origin_change(self):
        intent = self.unknown_put()
        intent['agent_revision'] = 2
        self.control.pending.write_text(json.dumps(intent))
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        self.assertTrue(self.control.pending.exists())
        self.control.origin = '1.1.1.1'
        self.agent.calls.clear()
        with self.assertRaises(OperationConflict):
            self.control.resume('b'*32)
        self.assertEqual(self.agent.calls, [])

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

    def delete_host(self):
        self.control.delete_host({'host_id': self.host_id, 'expected_revision': self.control.read()['revision']}, 'e'*32)

    def test_host_cascade_removes_owned_routes_preserves_foreign_and_saved_consumers(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.control.mutate_route(self.payload(20002), 'c'*32)
        foreign = {**self.agent.items[0], 'id': 'foreign', 'listen_port': 20003}
        self.agent.items.append(foreign)
        settings = {'relay_host_id': self.host_id}
        original = copy.deepcopy(settings)
        fallback = {'state': 'current', 'reason': '', 'addresses': [], 'bindings': []}
        self.assertEqual(relay_connections.consumer_state(self.control, settings, fallback), fallback)
        self.agent.lose_ack = True
        self.delete_host()
        self.assertEqual(self.agent.items, [foreign])
        self.assertEqual(self.control.read()['hosts'], [])
        self.assertEqual(self.control.read()['routes'], [])
        self.assertEqual(sum(method == 'DELETE' for method, _ in self.agent.calls), 2)
        self.assertEqual(relay_connections.consumer_state(self.control, settings, fallback)['state'], 'stale')
        self.assertEqual(settings, original)

    def test_cascade_lost_response_retains_host_until_readback_then_explicit_continuation(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.control.mutate_route(self.payload(20002), 'c'*32)
        self.agent.offline_after_write = True
        with self.assertRaises(OSError):
            self.delete_host()
        self.assertTrue(self.control.pending.exists())
        self.assertEqual(len(self.control.read()['hosts']), 1)
        with self.assertRaises(OperationConflict):
            self.delete_host()
        self.agent.offline = self.agent.offline_after_write = False
        self.control.reconcile()
        self.assertEqual(len(self.control.read()['routes']), 1)
        self.assertEqual(sum(method == 'DELETE' for method, _ in self.agent.calls), 1)
        self.delete_host()
        self.assertEqual(self.control.read()['hosts'], [])
        self.assertEqual(sum(method == 'DELETE' for method, _ in self.agent.calls), 2)

    def test_cascade_preflights_drift_and_does_not_delete_any_route(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.control.mutate_route(self.payload(20002), 'c'*32)
        self.agent.items[-1]['target_port'] = 31337
        with self.assertRaises(OperationConflict):
            self.delete_host()
        self.assertFalse(any(method == 'DELETE' for method, _ in self.agent.calls))
        self.assertEqual(len(self.control.read()['routes']), 2)

    def test_cascade_revision_conflict_and_offline_agent_keep_registry(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        before = self.control.read()
        with self.assertRaises(OperationConflict):
            self.control.delete_host({'host_id': self.host_id, 'expected_revision': '0'*32}, 'e'*32)
        self.agent.offline = True
        with self.assertRaises(OSError):
            self.delete_host()
        self.assertEqual(self.control.read(), before)

    def test_missing_remote_rule_can_be_cleaned_by_cascade(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.agent.items.clear()
        self.delete_host()
        self.assertEqual(self.control.read()['hosts'], [])
        self.assertEqual(self.control.read()['routes'], [])

    def test_empty_host_can_be_removed_offline(self):
        self.agent.offline = True
        self.delete_host()
        self.assertEqual(self.control.read()['hosts'], [])

    def test_rejected_cascade_retains_host_and_route_without_claiming_success(self):
        self.control.mutate_route(self.payload(), 'b'*32)
        self.agent.reject = True
        with self.assertRaises(OperationConflict):
            self.delete_host()
        self.assertEqual(len(self.control.read()['hosts']), 1)
        self.assertEqual(len(self.control.read()['routes']), 1)
        self.assertFalse(self.control.pending.exists())

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

    def test_all_64_routes_with_diagnostics_fit_bounded_agent_response(self):
        from relay_diagnostics import ERROR_CODES
        counter = 2**53 - 1
        diagnostics = {'sent_packets': counter, 'received_packets': counter, 'dropped_packets': counter,
                       'error_counts': {key: counter for key in ERROR_CODES}, 'last_error': 'udp_buffer_full',
                       'last_error_at': 1793300000, 'last_sent_at': 1793300000, 'last_received_at': 1793300000,
                       'return_state': 'flowing', 'no_return_sessions': 0}
        routes = [{'id': f'foreign-route-{index:050d}', 'transport': 'udp', 'listen_port': 20000 + index,
                   'target_ip': ORIGIN, 'target_port': 443, 'state': 'listening', 'target_verified': False,
                   'sent_bytes': counter, 'received_bytes': counter, 'errors': counter, 'sessions': 128,
                   'diagnostics': diagnostics} for index in range(64)]
        raw = json.dumps({'revision': 64, 'items': routes}).encode()
        self.assertGreater(len(raw), 65536)
        connection = Mock()
        connection.sock.getpeercert.return_value = b'certificate'
        response = connection.getresponse.return_value
        response.status = 200
        response.read.side_effect = lambda limit: raw[:limit]
        with patch('relay_client.http.client.HTTPSConnection', return_value=connection):
            client = RelayClient(f'https://{AGENT}:9443', TOKEN, hashlib.sha256(b'certificate').hexdigest())
            self.assertEqual(len(route_snapshot(client.request('GET', '/v1/routes'))['items']), 64)
            response.read.side_effect = lambda limit: b' ' * limit
            with self.assertRaises(AgentAPIError):
                client.request('GET', '/v1/routes')
        connection.close.assert_called()

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
        endpoints = [('GET', '/network/relays'), ('POST', '/network/relays/id/update'), ('GET', '/network/relays?view=inventory'), ('GET', '/network/relays?view=addresses'), ('PUT', '/network/relays'), ('PUT', '/network/relays/reconnect'), ('DELETE', '/network/relays/id'),
                     ('DELETE', '/network/relays/id/address?address=8.8.4.4'), ('DELETE', '/network/relays/id/address?address=8.8.4.4&restore=true'),
                     ('POST', '/clients/relay'), ('GET', '/clients/relay/id'), ('POST', '/clients/relay/id/resume'),
                     ('POST', '/network/relays/id/routes'), ('DELETE', '/network/relays/id/routes/route?agent_revision=0'),
                     ('POST', '/network/relays/reconcile'), ('POST', '/network/relays/resume'), ('POST', '/network/relays/id/routes/route/export'),
                     ('POST', '/network/relays/id/routes/route/check?consumer_id=direct:client')]
        for method, path in endpoints:
            with self.subTest(path=path):
                response = self.client.request(method, '/api'+path)
                self.assertEqual(response.status_code, 401)

    def test_inventory_skips_live_collectors_and_is_not_cached(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        control = Mock()
        control.public.return_value = {'revision': '0'*32, 'hosts': [], 'pending': False, 'origin_ip': ORIGIN}
        with patch.object(api, 'relay_control', return_value=control), patch.object(api, 'relay_origin_hosts', side_effect=AssertionError('origin discovery')), patch.object(api, 'relay_targets', side_effect=AssertionError('live targets')), patch.object(api, 'network_consumers', side_effect=AssertionError('live consumers')), patch.object(api, 'prepare_relay_probe', side_effect=AssertionError('live evidence')):
            response = self.client.get('/api/network/relays?view=inventory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        control.public.assert_called_once_with(probe=False)
        control.read.assert_not_called()
        self.assertEqual(response.json()['targets'], [])
        self.assertEqual(response.json()['consumers'], [])
        self.assertEqual(self.client.get('/api/network/relays?view=invalid').status_code, 422)

    def test_invalid_inventory_is_unavailable(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        with patch.object(api, 'relay_control', side_effect=ValueError('invalid registry')):
            self.assertEqual(self.client.get('/api/network/relays?view=inventory').status_code, 503)

    def test_address_refresh_skips_live_collectors(self):
        api.app.dependency_overrides[api.require_token] = lambda: None
        control = Mock()
        control.public.return_value = {'revision': '0'*32, 'hosts': [], 'pending': False, 'origin_ip': ORIGIN}
        with patch.object(api, 'relay_control', return_value=control), patch.object(api, 'relay_origin_hosts', side_effect=AssertionError('origin discovery')), patch.object(api, 'relay_targets', side_effect=AssertionError('live targets')), patch.object(api, 'network_consumers', side_effect=AssertionError('live consumers')):
            response = self.client.get('/api/network/relays?view=addresses')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        control.public.assert_called_once_with(addresses_only=True)
        control.read.assert_not_called()

    def test_cdn_native_copy_retains_gateway_sni_and_http_host(self):
        with patch.object(api, 'PUBLIC_IPV4', ORIGIN), patch.object(api, 'PUBLIC_DOMAIN', 'panel.example'), patch.object(api, 'PUBLIC_ENDPOINT', ORIGIN), patch.object(api.cdn_security, 'read_routes', return_value=[{'domain': 'cdn.example'}]), patch.object(api, 'VLESS_ENV', self.root/'absent.env'):
            hosts = api.relay_origin_hosts()
        original = 'proxies:\n- name: phone\n  type: vless\n  server: cdn.example\n  port: 443\n  uuid: secret\n  tls: true\n  servername: cdn.example\n  ws-opts:\n    path: /private-path\n    headers:\n      Host: cdn.example\n'
        route = {'transport':'tcp','target_port':443,'listen_port':20000}
        with self.assertRaises(ValueError):
            relay_export.rewrite(original,route,AGENT,hosts)
        output, count = relay_export.rewrite(original, {**route,'cdn_domain':'cdn.example'}, AGENT, hosts)
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
        self.assertEqual(self.client.delete('/api/network/relays/id/address?address=8.8.4.4').status_code, 409)
        with patch.object(api.subprocess, 'run', return_value=Mock(returncode=0)):
            response = self.client.post('/api/network/relays/reconcile', headers={'X-Operation-ID': 'e'*32})
        self.assertEqual(response.status_code, 202, response.text)

    def test_explicit_resume_admission_is_validated_and_available_during_relay_recovery(self):
        (self.root/'relay').mkdir()
        (self.root/'relay/pending.json').write_text('{}')
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.assertEqual(self.client.post('/api/network/relays/resume', json={'pending_operation_id':'invalid'}).status_code, 422)
        with patch.object(api.subprocess, 'run', return_value=Mock(returncode=0)):
            response = self.client.post('/api/network/relays/resume', json={'pending_operation_id':'b'*32}, headers={'X-Operation-ID':'f'*32})
        self.assertEqual(response.status_code, 202, response.text)
        require_recovery_clear(self.root, 'relay-resume')
        with self.assertRaises(OperationConflict):
            require_recovery_clear(self.root, 'relay-host-save')


if __name__ == '__main__':
    unittest.main()
