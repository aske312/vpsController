"""Mode changes preserve every supported outbound and produce loadable YAML."""
import base64
from copy import deepcopy
from itertools import product
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT
from tests.api.test_mihomo_rules_runtime import profile
from profile_modes import VPN_SERVICES


def connection_fixture():
    key = base64.b64encode(bytes(range(32))).decode()
    common = {'port': 18443, 'uuid': '00000000-0000-4000-8000-000000000001',
              'password': 'fixture-only', 'skip_cert_verify': False}
    connections = []
    for component in ('shadowsocks', 'wg', 'awg', 'hysteria2', 'tuic'):
        credential = {**common, 'method': 'aes-256-gcm', 'ip': '10.90.0.2/32',
                      'private_key': key, 'server_public_key': key, 'mtu': 1280,
                      'up_mbps': 100, 'down_mbps': 100,
                      'amnezia': dict(jc=4, jmin=40, jmax=70, s1=0, s2=0, h1=1, h2=2, h3=3, h4=4)}
        connections.append({'id': component, 'device_id': 'common',
                            'component': 'transport-' + component, 'credential': credential})
    for route, transports in (('direct', ('raw', 'xhttp', 'grpc')),
                              ('tls', ('raw', 'xhttp', 'grpc', 'websocket', 'httpupgrade')),
                              ('cdn', ('xhttp', 'grpc', 'websocket', 'httpupgrade'))):
        for transport in transports:
            credential = {**common, 'direct_tag': 'mihomo-vless-fixture', 'public_key': base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('='),
                          'short_id': '0123456789abcdef', 'servername': 'example.com',
                          'transport': transport, 'path': '/fixture', 'route_mode': route,
                          'cdn_enabled': route == 'cdn', 'cdn_domain': 'example.com',
                          'cdn_transport': transport, 'cdn_path': '/fixture',
                          'tls_domain': 'example.com', 'tls_transport': transport, 'tls_path': '/fixture'}
            connections.append({'id': route + '-' + transport, 'device_id': 'common',
                                'component': 'transport-reality', 'settings': {'route_mode': route},
                                'credential': credential})
    item = profile()
    item['connections'] = connections
    return item


class ProfileModeProtocolMatrixTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='127.0.0.1'))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))
        self.enterContext(patch.object(manager, 'routing_settings', return_value={}))

    def configs(self):
        item = connection_fixture()
        original = deepcopy(item)
        baseline = yaml.safe_load(manager.render_profile(item))['proxies']
        self.assertEqual(len(baseline), 17)
        self.assertEqual({row['type'] for row in baseline}, {'vless', 'ss', 'wireguard', 'hysteria2', 'tuic'})
        for capture, scope, route_dns, strategy in product(('tun', 'proxy'), ('all', 'selected'),
                                                          (False, True), ('', 'select', 'fallback', 'url-test')):
            routing = dict(connection_mode=capture, routing_scope=scope, route_dns=route_dns,
                           strategy=strategy, vpn_services=','.join(VPN_SERVICES))
            if not strategy:
                del routing['strategy']  # The editor deletes the override for "Общие".
            candidate = {**deepcopy(item), 'routing': routing}
            config = yaml.safe_load(manager.render_profile(candidate))
            self.assertEqual(config['proxies'], baseline, routing)
            self.assertEqual(config['mode'], 'rule')
            self.assertEqual(config['tun']['enable'], capture == 'tun')
            self.assertEqual(config['rules'][-1], 'MATCH,' + ('DIRECT' if scope == 'selected' else manager.MIHOMO_PROXY_GROUP))
            group = next(row for row in config['proxy-groups'] if row['name'] == manager.MIHOMO_PROXY_GROUP)
            self.assertEqual(group['type'], strategy or 'fallback')
            self.assertEqual(set(group['proxies']), {row['name'] for row in baseline})
            self.assertEqual(candidate['connections'], original['connections'])
            self.assertEqual(item, original)
            yield routing, config

    def test_all_connections_survive_capture_scope_dns_and_strategy_changes(self):
        self.assertEqual(sum(1 for _ in self.configs()), 32)

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires real Mihomo')
    def test_real_core_accepts_every_mode_with_all_connection_types(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.yaml'
            for routing, config in self.configs():
                with self.subTest(**routing):
                    path.write_text(yaml.safe_dump(config), encoding='utf-8')
                    result = subprocess.run([os.environ['PRIVACY_MIHOMO_BIN'], '-t', '-d', folder, '-f', str(path)],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
