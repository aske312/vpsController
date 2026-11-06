import copy
import hashlib
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'api'))
import relay_privacy as privacy


class PrivateTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        binary = shutil.which('openssl') or 'D:/Git/usr/bin/openssl.exe'
        subprocess.run([binary, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=endpoint', '-addext', 'subjectAltName=IP:8.8.8.8',
                        '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem')],
                       check=True, capture_output=True)
        cls.certificate = (root/'cert.pem').read_text()
        cls.key = (root/'key.pem').read_text()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def profile(self):
        return {'schema': 1, 'transport': 'hysteria2', 'generation': 'a'*32,
                'relay_ip': '8.8.4.4', 'listen_port': 20009, 'ingress_password': 'a'*48,
                'ingress_certificate': self.certificate, 'ingress_private_key': self.key,
                'origin_ip': '8.8.8.8', 'origin_port': 19443, 'origin_password': 'b'*48,
                'origin_certificate': self.certificate, 'bandwidth_mbps': 1000,
                'targets': [{'ip': '8.8.8.8', 'port': 443, 'network': 'tcp'},
                            {'ip': '9.9.9.9', 'port': 51831, 'network': 'udp'}]}

    def test_destination_acl_preserves_pairs_and_denies_everything_else(self):
        profile = self.profile()
        for config in (privacy.render_relay(profile, '/private'), privacy.render_origin(profile, '/private')):
            self.assertEqual(config['log'], {'disabled': True})
            rules = config['route']['rules']
            self.assertEqual([(r['ip_cidr'], r['port'], r['network']) for r in rules[:-1]],
                             [(['8.8.8.8/32'], [443], 'tcp'), (['9.9.9.9/32'], [51831], 'udp')])
            self.assertEqual(rules[-1]['action'], 'reject')
            self.assertNotIn('final', config['route'])
        self.assertNotIn('insecure', json.dumps(privacy.render_relay(profile, '/private')))

    def test_unsafe_profiles_are_rejected_before_rendering(self):
        changes = [lambda p: p.update(schema=True), lambda p: p.update(origin_ip=p['relay_ip']),
                   lambda p: p.update(relay_ip='127.0.0.1'), lambda p: p.update(listen_port=443),
                   lambda p: p.update(origin_password='short'), lambda p: p.update(unknown='x'),
                   lambda p: p.update(bandwidth_mbps=True), lambda p: p['targets'][0].update(ip='169.254.169.254'),
                   lambda p: p['targets'][0].update(port=p['origin_port']), lambda p: p.update(targets=[])]
        for change in changes:
            with self.subTest(change=change):
                profile = self.profile();change(profile)
                with self.assertRaises(ValueError):privacy.render_relay(profile, '/private')

    def test_outer_proxy_is_pinned_without_insecure_tls_and_contains_no_origin_address(self):
        profile = self.profile();outer = privacy.mihomo_transport(profile)
        self.assertEqual(outer['fingerprint'], hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.certificate)).hexdigest())
        self.assertNotIn('skip-cert-verify', outer)
        self.assertEqual(outer['server'], profile['relay_ip'])
        self.assertNotIn(profile['origin_ip'], json.dumps(outer))
        self.assertEqual(outer['x-nodera-route'], {'role': 'transport', 'hidden': True})

    def test_inner_credentials_and_sni_are_preserved_without_mutating_input(self):
        profile = self.profile();node = {'name': 'TLS', 'type': 'trojan', 'server': 'old.example',
                                        'port': 20001, 'password': 'existing-secret', 'sni': 'private.example'}
        before = copy.deepcopy(node)
        result = privacy.protect_node(node, profile, profile['targets'][0])
        self.assertEqual(node, before)
        self.assertEqual((result['password'], result['sni']), ('existing-secret', 'private.example'))
        self.assertEqual((result['server'], result['port']), ('8.8.8.8', 443))
        self.assertEqual(result['dialer-proxy'], privacy.mihomo_transport(profile)['name'])
        self.assertNotIn('fallback', result)

    def test_existing_dialers_unapproved_targets_and_wrong_network_cannot_bypass_policy(self):
        profile = self.profile();node = {'name': 'TLS', 'type': 'trojan', 'server': 'old.example', 'port': 443}
        cases = [(dict(node, **{'dialer-proxy': 'other'}), profile['targets'][0]),
                 (node, {'ip': '8.8.8.8', 'port': 80, 'network': 'tcp'}), (node, profile['targets'][1])]
        for proxy, endpoint in cases:
            with self.subTest(proxy=proxy, endpoint=endpoint), self.assertRaises(ValueError):
                privacy.protect_node(proxy, profile, endpoint)

    def test_wireguard_mtu_and_shadowsocks_udp_are_explicit(self):
        profile = self.profile()
        wg = privacy.protect_node({'type': 'wireguard', 'mtu': 1420}, profile, profile['targets'][1])
        self.assertEqual(wg['mtu'], 1280)
        with self.assertRaises(ValueError):privacy.protect_node({'type': 'ss', 'udp': True}, profile, profile['targets'][0])
        profile['targets'].append({'ip': '8.8.8.8', 'port': 443, 'network': 'udp'})
        self.assertTrue(privacy.protect_node({'type': 'ss', 'udp': True}, profile, profile['targets'][0])['udp'])


if __name__ == '__main__':unittest.main()
