import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
from tests.api.test_relay_manager import FakeAgent, AGENT, ORIGIN, TOKEN, PIN
from tests.api import test_relay_privacy as pure
from relay_manager import RelayManager
import relay_private_transport as private
import relay_privacy


class PrivateAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.profile = None
        self.lost = False

    def request(self, method, path, body=None, revision=None):
        if path == '/v1/status':
            return {**super().request(method, path), 'capabilities': ['private-transport-v1']}
        if '/private-transports/' not in path:
            return super().request(method, path, body, revision)
        self.calls.append((method, path))
        if method == 'PUT':
            self.profile = copy.deepcopy(body)
            if self.lost: raise OSError('ack lost')
        if method == 'DELETE': self.profile = None
        return {'generation': path.rsplit('/', 1)[1], 'state': 'listening' if self.profile else 'absent',
                **({'transport': 'hysteria2', 'listen_port': self.profile['listen_port']} if self.profile else {})}


class PrivateOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pure.PrivateTransportTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        pure.PrivateTransportTests.tearDownClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.agent = PrivateAgent()
        self.control = RelayManager(Path(self.temp.name), ORIGIN, lambda *_: self.agent)
        self.host = 'b'*32
        self.control.write({'schema': 1, 'hosts': [{'id': self.host, 'name': 'Relay', 'agent_url': 'https://'+AGENT+':9443', 'token': TOKEN, 'certificate_sha256': PIN}],
                            'routes': [{'id': 'pro-'+'c'*32, 'host_id': self.host, 'transport': 'tcp', 'listen_port': 20005, 'target_ip': ORIGIN, 'target_port': 443}]})
        self.api = SimpleNamespace(relay_control=lambda: self.control)
        self.payload = {'host_id': self.host, 'expected_revision': self.control.read()['revision'], 'enabled': True}
        self.enterContext(patch.object(private, 'runtime', return_value=Path('/synthetic/sing-box')))
        self.enterContext(patch.object(private, 'install_origin'))
        self.probe = self.enterContext(patch.object(private, 'probe'))
        self.enterContext(patch.object(private.port_allocation, 'allocate', side_effect=[19443, 34000]))
        def cert(folder, name, address):
            (folder/(name+'.crt')).write_text(pure.PrivateTransportTests.certificate)
            (folder/(name+'.key')).write_text(pure.PrivateTransportTests.key)
        self.enterContext(patch.object(private, 'certificate', side_effect=cert))

    def enable(self):
        self.payload['expected_revision'] = self.control.read()['revision']
        private.perform(self.api, self.payload, 'a'*32)

    def test_only_end_to_end_proof_commits_ready_and_secrets_stay_private(self):
        self.probe.side_effect = lambda *_: self.assertNotEqual(private.record(self.control, self.host)['state'], 'ready')
        self.enable()
        self.assertEqual(private.record(self.control, self.host)['state'], 'ready')
        public = [{'id': self.host}];private.annotate(self.control, public)
        self.assertEqual(set(public[0]['private_transport']), {'state', 'generation', 'verified_at'})
        self.assertEqual(len([row for row in self.agent.calls if row[0] == 'PUT']), 1)
        self.assertNotIn('origin_private_key', self.agent.profile)
        self.assertNotEqual(self.agent.profile['origin_password'], self.agent.profile['ingress_password'])

    def test_lost_ack_and_recheck_do_not_repeat_remote_put(self):
        self.agent.lost = True;self.enable();self.enable()
        self.assertEqual(len([row for row in self.agent.calls if row[0] == 'PUT']), 1)
        self.assertEqual(self.probe.call_count, 2)

    def test_failed_proof_blocks_export_until_explicit_recheck(self):
        self.probe.side_effect = OSError('synthetic failure')
        with self.assertRaises(ValueError): self.enable()
        with self.assertRaises(ValueError): private.active(self.control, self.host)
        self.assertEqual(private.record(self.control, self.host)['state'], 'unknown')
        self.probe.side_effect = None;self.enable()
        self.assertEqual(private.active(self.control, self.host)['generation'], 'a'*32)
        self.assertEqual(len([row for row in self.agent.calls if row[0] == 'PUT']), 1)

    def test_subscription_preserves_keys_adds_one_helper_and_no_group_fallback(self):
        self.enable()
        node = {'name': 'TLS', 'type': 'trojan', 'server': AGENT, 'port': 20005, 'password': 'inner-secret', 'sni': 'inner.example'}
        config = {'proxies': [node, dict(node, name='second')], 'proxy-groups': [{'name': 'VPN', 'type': 'fallback', 'proxies': ['TLS', 'second']}]}
        selections = {name: {'settings': {'relay_host_id': self.host}} for name in ('TLS', 'second')}
        result = yaml.safe_load(relay_privacy.protect_subscription(self.control, yaml.safe_dump(config), selections))
        self.assertEqual(len(result['proxies']), 3)
        self.assertEqual(result['proxy-groups'], config['proxy-groups'])
        self.assertEqual((result['proxies'][0]['password'], result['proxies'][0]['sni']), ('inner-secret', 'inner.example'))
        self.assertEqual((result['proxies'][0]['server'], result['proxies'][0]['port']), (ORIGIN, 443))
        self.assertEqual(result['proxies'][0]['dialer-proxy'], result['proxies'][2]['name'])
        self.assertEqual(result['proxies'][2]['fingerprint'], relay_privacy.fingerprint(pure.PrivateTransportTests.certificate))

    def test_failed_recheck_revokes_previous_readiness(self):
        self.enable()
        self.probe.side_effect = OSError('path failed')
        with self.assertRaises(ValueError): self.enable()
        with self.assertRaises(ValueError): private.active(self.control, self.host)
        self.assertEqual(private.record(self.control, self.host)['state'], 'unknown')

    def test_disable_fences_remote_and_erases_keys_before_raw_export_is_allowed(self):
        self.enable()
        self.payload.update(enabled=False, expected_revision=self.control.read()['revision'])
        with patch.object(private.port_allocation, 'release_prefix') as release:
            private.perform(self.api, self.payload, 'd'*32)
        self.assertIsNone(self.agent.profile)
        self.assertIsNone(private.active(self.control, self.host))
        self.assertFalse((private.root(self.control)/('a'*32)).exists())
        self.assertEqual(private.record(self.control, self.host)['state'], 'disabled')
        release.assert_called_once_with('relay-private:'+'a'*32+':')

    def test_restricted_native_export_and_host_deletion_cannot_bypass_protection(self):
        self.enable()
        import relay_connections
        with self.assertRaises(ValueError): relay_connections.direct_result(self.control, 'wg', {'relay_host_id': self.host}, {'config': ''})
        with self.assertRaises(ValueError): self.control.delete_host({'host_id': self.host}, 'd'*32)

if __name__ == '__main__': unittest.main()
