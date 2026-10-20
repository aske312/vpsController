import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT
import network_probe
import xray_protocols
from client_subscription import vless_subscription


class XrayComponentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.root / 'reality/config.json'
        self.config.parent.mkdir()
        self.neighbor = {'tag':'unrelated', 'listen':'127.0.0.1', 'port':19000, 'protocol':'socks', 'settings':{}}
        self.config.write_text(json.dumps({'inbounds':[self.neighbor], 'outbounds':[{'protocol':'freedom'}]}))
        for name, value in {'CONFIG_ROOT':self.root, 'VLESS_CDN_ROUTE_ROOT':self.root/'routes',
                            'SUBMODULE_ROOT':ROOT/'protocol-images/mihomo/modules'}.items():
            self.enterContext(patch.object(manager, name, value))
        for name, value in {'ROUTES':self.root/'routes', 'DIRECT_ENV':self.root/'native.env', 'ENV':self.root/'environment', 'STATE':self.root/'cdn-security.json'}.items():
            self.enterContext(patch.object(manager.cdn_security, name, value))
        for name in ('module_is_installed', 'module_is_ready', 'direct_tls_domain_ready', 'mihomo_route_endpoint_selectable'):
            self.enterContext(patch.object(manager, name, return_value=True))
        self.enterContext(patch.object(manager, 'reality_env', return_value={'PRIVATE_KEY':'private', 'PUBLIC_KEY':'public'}))
        self.enterContext(patch.object(manager, 'next_vless_port', side_effect=lambda config, start, owner: max([20000, *[row['port'] for row in config['inbounds']]])+1))
        self.enterContext(patch.object(manager.port_allocation, 'release_prefix'))
        self.enterContext(patch.object(manager.shutil, 'chown'))
        self.apply = self.enterContext(patch.object(manager, 'apply_reality_config', side_effect=lambda path, value, **_:path.write_text(json.dumps(value))))
        self.dns = {'nameserver':'1.1.1.1', 'fallback':'8.8.8.8'}

    def create(self, kind, transport, route='cdn'):
        settings = {'xray_protocol':kind, 'route_mode':route, f'{route}_domain':f'{route}.example',
                    f'{route}_transport':transport, f'{route}_xhttp_mode':'packet-up'}
        credential = manager.add_reality_credential('profile', 'node', settings, restart_service=False, reload_caddy=False)
        return {'id':'node', 'name':'node', 'component':'transport-reality', 'settings':settings, 'credential':credential}

    def test_protocols_and_transports_provision_export_and_remove_without_touching_neighbors(self):
        for kind in ('vless', 'vmess', 'trojan'):
            for route in ('tls', 'cdn'):
                for transport in ('websocket', 'xhttp', 'grpc', 'httpupgrade'):
                    with self.subTest(protocol=kind, route=route, transport=transport):
                        connection = self.create(kind, transport, route)
                        credential = connection['credential']
                        inbound = json.loads(self.config.read_text())['inbounds'][-1]
                        self.assertEqual(inbound['protocol'], kind)
                        self.assertEqual(inbound['listen'], '127.0.0.1')
                        self.assertEqual(inbound['settings'], xray_protocols.server_settings(kind, xray_protocols.client_identity(credential), 'mihomo-profile-node'))
                        renderer = manager.render_vless_cdn if route == 'cdn' else manager.render_vless_tls
                        proxy = yaml.safe_load('proxies:\n'+'\n'.join(renderer(credential, 'node')))['proxies'][0]
                        self.assertEqual(proxy['type'], kind)
                        for key, value in xray_protocols.mihomo_auth(credential).items(): self.assertEqual(proxy[key], value)
                        if kind == 'trojan': self.assertNotIn('uuid', proxy)
                        configs = manager.build_xray_configs([connection], {}, self.dns, [], '192.0.2.1', lambda:{})
                        outbound = configs[0]['outbounds'][0]
                        self.assertEqual(outbound['protocol'], kind)
                        self.assertEqual(outbound['settings'], xray_protocols.outbound_settings(credential, route+'.example', 443))
                        if transport != 'xhttp':
                            exported = manager.build_singbox_config([connection], {}, self.dns, [], '192.0.2.1', lambda:{}, False)
                            node = next(row for row in exported['outbounds'] if row['type'] == kind)
                            self.assertEqual(node['server_port'], 443)
                            self.assertIn(kind+'://', vless_subscription(configs))
                        manager.remove_reality_credential('profile', credential, restart_service=False, reload_caddy=False)
                        self.assertEqual(json.loads(self.config.read_text())['inbounds'], [self.neighbor])
                        self.assertFalse(list((self.root/'routes').glob('*.json')))

    def test_legacy_default_does_not_change_server_settings_or_identity(self):
        old = manager.validate_connection('transport-reality', {})
        explicit = manager.validate_connection('transport-reality', {'xray_protocol':'vless'})
        self.assertEqual(old, explicit)
        self.assertNotIn('xray_protocol', explicit)
        for kind in ('vmess', 'trojan', 'unknown'):
            with self.subTest(kind=kind), self.assertRaises(manager.HTTPException):
                manager.validate_connection('transport-reality', {'xray_protocol':kind, 'route_mode':'direct'})

    def test_each_protocol_has_its_own_probe_identity_and_never_borrows_a_neighbor(self):
        for kind in ('vless', 'vmess', 'trojan'):
            with self.subTest(protocol=kind):
                connection = self.create(kind, 'grpc')
                binding = {'id':'binding', 'kind':'cdn', 'address':'cdn.example', 'state':'active', 'desired_revision':1}
                refs = [{'id':'mihomo:profile:node', 'owner':'mihomo', 'kind':'cdn', 'address':'cdn.example', 'reference':'snapshot'}]
                descriptor = json.loads((self.root/'routes/profile-node.json').read_text())
                configs = json.loads(self.config.read_text())
                prepare = lambda:network_probe.prepare_mihomo_vless(binding, refs, [{'id':'profile','connections':[connection]}], configs, {'profile-node':descriptor})
                plan = prepare()
                self.assertEqual(plan.client['protocol'], kind)
                self.assertNotIn(xray_protocols.client_identity(connection['credential']), repr(plan)+json.dumps(plan.summary()))
                configs['inbounds'][-1]['protocol'] = 'vmess' if kind == 'vless' else 'vless'
                with self.assertRaises(network_probe.ProbeUnavailable): prepare()
                manager.remove_reality_credential('profile', connection['credential'], restart_service=False, reload_caddy=False)

    def test_vless_privacy_does_not_recreate_or_encrypt_other_xray_protocols(self):
        connection = self.create('trojan', 'websocket')
        connection['device_id'] = 'phone'
        profile = {'connections':[connection]}
        original = copy.deepcopy(profile)
        with patch.object(manager, 'device_routing', return_value={'tunnel_privacy':True}), patch.object(manager, 'vless_encryption_pair') as encryption:
            manager.reconcile_profile_encryption(profile)
        self.assertEqual(profile, original)
        encryption.assert_not_called()

    def test_failed_apply_restores_server_and_removes_new_routes(self):
        original = self.config.read_bytes()
        self.apply.side_effect = RuntimeError('rejected candidate')
        with self.assertRaisesRegex(RuntimeError, 'rejected candidate'):
            self.create('trojan', 'grpc')
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(list((self.root/'routes').glob('*.json')))

    def test_real_xray_and_mihomo_parsers_accept_all_protocol_transport_pairs(self):
        binary = os.getenv('PRIVACY_XRAY_BIN')
        mihomo = os.getenv('PRIVACY_MIHOMO_BIN')
        if not binary or not mihomo: self.skipTest('Set Xray and Mihomo binaries')
        for kind in ('vless', 'vmess', 'trojan'):
            for transport in ('websocket', 'xhttp', 'grpc', 'httpupgrade'):
                with self.subTest(protocol=kind, transport=transport):
                    connection = self.create(kind, transport)
                    checked = subprocess.run([binary,'run','-test','-config',str(self.config)],capture_output=True,text=True,timeout=15)
                    self.assertEqual(checked.returncode, 0, checked.stdout+checked.stderr)
                    proxy = yaml.safe_load('proxies:\n'+'\n'.join(manager.render_vless_cdn(connection['credential'], 'node')))['proxies'][0]
                    path = self.root/'client.yaml'
                    path.write_text(yaml.safe_dump({'mixed-port':0, 'proxies':[proxy], 'rules':['MATCH,node']}))
                    checked = subprocess.run([mihomo,'-t','-d',str(self.root),'-f',str(path)],capture_output=True,text=True,timeout=15)
                    self.assertEqual(checked.returncode, 0, checked.stdout+checked.stderr)
                    manager.remove_reality_credential('profile', connection['credential'], restart_service=False, reload_caddy=False)
