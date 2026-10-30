import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml
from tests.api.support import api, manager, ROOT
import network_routes

ORIGIN = '195.14.118.112'
DOMAIN = 'tls.example'
UDP_COMPONENTS = ('transport-wg', 'transport-awg', 'transport-hysteria2', 'transport-tuic', 'transport-shadowsocks')


class MihomoEndpointDomainTests(unittest.TestCase):
    def test_first_hop_cdn_relay_can_share_origin_hostname(self):
        settings = {'route_mode':'cdn','cdn_domain':'cdn.example','relay_cdn':True,'relay_cdn_mode':'before_cdn','relay_host_id':'b'*32}
        target = {'component':'transport-reality','settings':settings}
        native = self.root/'native.env'
        native.write_text('CDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n')
        with patch.object(manager,'DIRECT_VLESS_ENV',native), patch.object(manager,'profiles',return_value=[{'connections':[{'component':'transport-reality','settings':{**settings,'relay_host_id':'c'*32}}]}]):
            manager.validate_cdn_delivery([target])

    def test_cdn_relay_hostname_cannot_break_other_origin_or_agent_consumers(self):
        def connection(host='', mode='cdn'):
            return {'component':'transport-reality', 'settings':{'route_mode':mode, 'cdn_domain':'cdn.example',
                    'tls_domain':'cdn.example', 'relay_host_id':host, 'relay_cdn':bool(host), 'relay_cdn_mode':'after_cdn'}}
        target = connection('b'*32)
        native = self.root/'native.env'
        with patch.object(manager, 'DIRECT_VLESS_ENV', native), patch.object(manager, 'profiles', return_value=[{'id':'other','connections':[connection()]}]) as saved:
            with self.assertRaises(manager.HTTPException):
                manager.validate_cdn_delivery([target])
            # Replacing every consumer in this profile is safe; other profiles
            # and retiring/native consumers remain independent owners.
            manager.validate_cdn_delivery([target], 'other')
            saved.return_value = [{'id':'other','connections':[connection('b'*32)]}]
            manager.validate_cdn_delivery([target])
            for existing in (connection('c'*32), connection(mode='tls')):
                saved.return_value = [{'id':'other','connections':[existing]}]
                with self.assertRaises(manager.HTTPException):
                    manager.validate_cdn_delivery([target])
            saved.return_value = [{'id':'same','retiring_connections':[{'connection':connection()}]}]
            with self.assertRaises(manager.HTTPException):
                manager.validate_cdn_delivery([target], 'same')
            saved.return_value = []
            native.write_text('CDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n')
            with self.assertRaises(manager.HTTPException):
                manager.validate_cdn_delivery([target])

    def test_cdn_relay_validation_derives_origin_acl_and_rejects_incompatible_modes(self):
        control = Mock()
        control.addresses.return_value = {'ipv4': ['85.209.155.229', '8.8.4.4'], 'ipv6': [], 'domains': []}
        control.client.return_value.host = '85.209.155.229'
        values = {'route_mode': 'cdn', 'cdn_domain': 'cdn.example', 'cdn_transport': 'websocket',
                  'relay_host_id': 'b'*32, 'relay_cdn': True, 'cdn_relay_ips': '1.1.1.1'}
        with patch.object(manager, 'relay_connection_control', return_value=control), patch.object(manager, 'mihomo_route_endpoint_selectable', return_value=True):
            for transport in ('websocket', 'xhttp', 'grpc', 'httpupgrade'):
                with self.subTest(transport=transport):
                    result = manager.validate_connection('transport-reality', {**values, 'cdn_transport': transport, 'cdn_xhttp_mode': 'packet-up'})
                    self.assertEqual(result['cdn_transport'], transport)
                    self.assertEqual(result['cdn_xhttp_mode'], 'packet-up')
                    self.assertEqual(set(result['cdn_relay_ips'].split(',')), {'85.209.155.229', '8.8.4.4'})
                    first_hop = manager.validate_connection('transport-reality', {**values, 'cdn_transport':transport, 'relay_cdn_mode':'before_cdn'})
                    self.assertNotIn('cdn_relay_ips',first_hop)
            for component, patch_values in [('transport-wg', {}), ('transport-reality', {'relay_host_id': ''}),
                                            ('transport-reality', {'route_mode': 'tls'}), ('transport-reality', {'cdn_transport': 'raw'}),
                                            ('transport-reality', {'relay_cdn': 'yes'})]:
                with self.subTest(component=component, patch=patch_values), self.assertRaises(manager.HTTPException):
                    manager.validate_connection(component, {**values, **patch_values})
            result = manager.validate_connection('transport-reality', {**values, 'relay_cdn': False})
            self.assertNotIn('cdn_relay_ips', result)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.endpoints = self.root / 'network.json'
        self.endpoints.write_text(json.dumps({'udp_relay_domains': [DOMAIN]}))
        for name, value in {'SUBMODULE_ROOT': ROOT / 'protocol-images/mihomo/modules', 'DATA_ROOT': self.root / 'mihomo', 'CONFIG_ROOT': self.root / 'config', 'NETWORK_ENDPOINTS_FILE': self.endpoints}.items():
            self.enterContext(patch.object(manager, name, value))
        self.enterContext(patch.object(manager, 'WG_CONFIG_BY_MODULE', {name:self.root/(name+'.conf') for name in manager.WG_CONFIG_BY_MODULE}))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))
        self.enterContext(patch.object(manager, 'module_is_ready', return_value=True))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value=ORIGIN))
        self.enterContext(patch.object(manager.server_addresses, 'primary_for_new_config', return_value=ORIGIN))
        self.inventory = self.enterContext(patch.object(manager.server_addresses, 'inventory', return_value={'items': []}))
        self.resolve = self.enterContext(patch.object(manager.socket, 'getaddrinfo', return_value=[(2, 2, 17, '', (ORIGIN, 0))]))

    def test_saved_udp_role_and_origin_dns_are_required_for_new_assignments(self):
        for component in UDP_COMPONENTS:
            with self.subTest(component=component):
                result = manager.validate_connection(component, {'udp_domain': DOMAIN.upper()+'.'})
                self.assertEqual(result['udp_domain'], DOMAIN)
        for addresses in ([], [(2, 2, 17, '', ('104.16.0.1', 0))], [(2, 2, 17, '', (ORIGIN, 0)), (2, 2, 17, '', ('8.8.8.8', 0))]):
            self.resolve.return_value = addresses
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection('transport-wg', {'udp_domain': DOMAIN})
        for values in ({'udp_domain': 'unknown.example'}, {'udp_domain': 42}):
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection('transport-wg', values)
        with self.assertRaises(manager.HTTPException):
            manager.validate_connection('transport-reality', {'udp_domain': DOMAIN})
        with patch.object(manager, 'relay_connection_control'):
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection('transport-wg', {'udp_domain': DOMAIN, 'relay_host_id': 'a'*32})

    def test_domain_can_use_another_ip_present_on_this_server(self):
        self.resolve.return_value = [(2, 2, 17, '', ('195.14.118.113', 0))]
        self.inventory.return_value = {'items': [{'address': '195.14.118.113', 'presence': 'present'}]}
        self.assertEqual(manager.validate_connection('transport-wg', {'udp_domain': DOMAIN})['udp_domain'], DOMAIN)
        self.inventory.return_value['items'][0]['presence'] = 'missing'
        with self.assertRaises(manager.HTTPException):
            manager.validate_connection('transport-wg', {'udp_domain': DOMAIN})

    def test_retired_assignment_is_retained_but_cannot_be_assigned_to_another_address(self):
        registry = network_routes.synchronize({'udp_relay_domains': [DOMAIN]}, {}, [])
        self.endpoints.write_text(json.dumps({'route_registry': registry}))
        saved = {'id': 'saved', 'component': 'transport-wg', 'settings': {'udp_domain': DOMAIN}}
        value = manager.ProfileConnectionInput(id='saved', component='transport-wg', settings={'udp_domain': DOMAIN})
        self.resolve.side_effect = AssertionError('Unchanged saved route must not need DNS')
        self.assertEqual(manager.validate_connection_inputs([value], [saved])[0]['settings']['udp_domain'], DOMAIN)
        with self.assertRaises(manager.HTTPException):
            manager.validate_connection_inputs([value])
        value.settings = {'udp_domain': 'other.example'}
        with self.assertRaises(manager.HTTPException):
            manager.validate_connection_inputs([value], [saved])

    def test_udp_address_reaches_yaml_without_overwriting_sni_or_credentials(self):
        base = {'server_address': ORIGIN, 'port': 51830, 'ip': '10.0.0.2/32', 'private_key': 'private', 'server_public_key': 'public', 'mtu': 1400,
                'password': 'password', 'uuid': '00000000-0000-4000-8000-000000000001', 'method': 'aes-128-gcm',
                'up_mbps': 100, 'down_mbps': 100, 'sni': 'cert.example', 'skip_cert_verify': False,
                'amnezia': {key: 1 for key in ('jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4')}}
        for component in UDP_COMPONENTS:
            original = {'id': component, 'component': component, 'name': component, 'settings': {'udp_domain': DOMAIN}, 'credential': copy.deepcopy(base)}
            snapshot = copy.deepcopy(original)
            effective = manager.effective_client_connections([original])[0]
            self.assertEqual(original, snapshot)
            proxy = yaml.safe_load('proxies:\n'+'\n'.join(manager.render_proxy(component, effective['credential'], component)))['proxies'][0]
            self.assertEqual((proxy['server'], proxy['port']), (DOMAIN, base['port']))
            if component in ('transport-hysteria2', 'transport-tuic'):
                self.assertEqual(proxy['sni'], 'cert.example')
                self.assertFalse(proxy['skip-cert-verify'])
            cleared = manager.effective_client_connections([{**original, 'settings': {'udp_domain': ''}}])[0]
            self.assertEqual(cleared['credential']['server_address'], ORIGIN)

    def test_removing_udp_role_marks_consumer_stale_and_keeps_profile(self):
        data = self.root / 'data'
        (data / 'mihomo').mkdir(parents=True)
        profiles = data / 'mihomo/profiles.json'
        profiles.write_text(json.dumps([{'id': 'profile', 'name': 'Phone', 'connections': [{'id': 'wg', 'component': 'transport-wg', 'settings': {'udp_domain': DOMAIN}}]}]))
        before = profiles.read_bytes()
        with patch.object(api, 'DATA_DIR', data), patch.object(api, 'NETWORK_ENDPOINTS_FILE', self.endpoints), patch.object(api, 'VLESS_ENV', data/'absent.env'), patch.object(api, 'read_clients', return_value=[]):
            registry = network_routes.synchronize({'udp_relay_domains': [DOMAIN]}, {}, api.network_consumers())
        self.assertEqual(network_routes.consumer_state(registry, 'mihomo:profile:wg')['state'], 'stale')
        self.assertEqual(profiles.read_bytes(), before)
