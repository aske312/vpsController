import copy
import hashlib
import json
import unittest
from unittest.mock import patch
import yaml
from tests.api import test_relay_manager as fixtures
import relay_ingress
from application_operation import OperationConflict


class SecurityTests(unittest.TestCase):
    setUp = fixtures.RelayManagementTests.setUp

    def test_new_controller_is_bound_and_legacy_registration_is_rejected(self):
        self.assertEqual(self.agent.controller, fixtures.ORIGIN)
        status = self.agent.request('GET', '/v1/status')
        legacy = {key: value for key, value in status.items() if key not in ('security', 'capabilities')}
        with self.assertRaises(ValueError): relay_ingress.bind_controller(self.agent, legacy, fixtures.ORIGIN, required=True)
        relay_ingress.bind_controller(self.agent, legacy, fixtures.ORIGIN)

    def test_lost_binding_ack_is_read_once_without_replaying_put(self):
        self.agent.controller = None
        status = self.agent.request('GET', '/v1/status')
        original = self.agent.request
        def lost(method, path, body=None, revision=None):
            result = original(method, path, body, revision)
            if method == 'PUT': raise OSError('lost reply')
            return result
        with patch.object(self.agent, 'request', side_effect=lost):
            relay_ingress.bind_controller(self.agent, status, fixtures.ORIGIN, required=True)
        self.assertEqual(sum(method == 'PUT' for method, _ in self.agent.calls), 1)

    def test_secure_cdn_exports_hidden_pinned_tcp_carrier_without_public_credentials(self):
        with patch('relay_cdn.resolve', return_value='104.16.1.2'):
            self.control.ensure_cdn(self.host_id, 'cdn.example', 'd'*32)
        route = self.control.read()['routes'][0]
        self.assertEqual(route['auth']['type'], 'tls-connect')
        secret = relay_ingress.credential(self.control, route)
        body = relay_ingress.wire_config(self.control, route)
        self.assertEqual(body['auth']['password_sha256'], hashlib.sha256(secret['password'].encode()).hexdigest())
        for public in (self.control.public(), self.control.public(probe=False), json.loads(self.control.pending.read_text()) if self.control.pending.exists() else {}):
            self.assertNotIn(secret['password'], json.dumps(public))
            self.assertNotIn(body['auth']['password_sha256'], json.dumps(public))
        selection = {'settings': {'relay_host_id': self.host_id}}
        config = {'proxies': [{'name': 'CDN', 'type': 'vless', 'server': fixtures.AGENT, 'port': route['listen_port'],
                              'uuid': 'fixture', 'tls': True, 'servername': 'cdn.example'}],
                  'proxy-groups': [{'name': 'Select', 'type': 'select', 'proxies': ['CDN']}]}
        result = yaml.safe_load(relay_ingress.protect_subscription(self.control, yaml.safe_dump(config), {'CDN': selection}))
        node, helper = result['proxies']
        self.assertEqual((node['server'], node['port'], node['servername']), ('104.16.1.2', 443, 'cdn.example'))
        self.assertEqual(node['dialer-proxy'], helper['name'])
        self.assertEqual(helper['password'], secret['password'])
        self.assertEqual(helper['type'], 'http')
        self.assertTrue(helper['tls'])
        self.assertEqual(result['proxy-groups'], config['proxy-groups'])
        saved = copy.deepcopy(route)
        saved['auth']['credential_id'] = 'f'*32
        with self.assertRaises(OperationConflict): relay_ingress.credential(self.control, saved)

    def test_udp_proxies_do_not_use_tcp_carrier_with_the_same_listen_port(self):
        with patch('relay_cdn.resolve', return_value='104.16.1.2'):
            self.control.ensure_cdn(self.host_id, 'cdn.example', 'd'*32)
        route = self.control.read()['routes'][0]
        nodes = [{'name': kind, 'type': kind, 'server': fixtures.AGENT,
                  'port': route['listen_port']} for kind in ('wireguard', 'hysteria2', 'tuic')]
        config = {'proxies': nodes}
        selections = {node['name']: {'settings': {'relay_host_id': self.host_id}} for node in nodes}
        result = yaml.safe_load(relay_ingress.protect_subscription(self.control, yaml.safe_dump(config), selections))
        self.assertEqual(result, config)

    def test_legacy_cdn_migration_adds_a_secure_entry_and_keeps_delivered_ports_alive(self):
        with patch('relay_cdn.resolve', return_value='104.16.1.2'):
            self.control.ensure_cdn(self.host_id, 'cdn.example', 'c'*32)
        registry = self.control.read()
        original = registry['routes'][0]
        original.pop('auth')
        self.agent.items[0].pop('auth')
        self.control.write(registry)
        self.control.ensure_cdn(self.host_id, 'cdn.example', 'd'*32)
        routes = self.control.read()['routes']
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0], original)
        self.assertNotEqual(routes[0]['listen_port'], routes[1]['listen_port'])
        self.assertIn('auth', routes[1])
        self.assertEqual(self.control.cdn_route(self.host_id, 'cdn.example')['id'], routes[1]['id'])
        self.assertEqual(len(self.agent.items), 2)


if __name__ == '__main__': unittest.main()
