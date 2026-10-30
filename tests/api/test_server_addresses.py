import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import api, manager
import server_addresses as addresses
from fastapi.testclient import TestClient
from application_operation import OperationConflict

A, B, C, V6 = '45.66.163.179', '45.66.163.181', '45.66.163.183', '2606:4700::1111'


def interface(name, values, **extra):
    return {'ifname': name, 'flags': ['UP'], 'addr_info': [{'local': value, 'scope': 'global', **extra} for value in values]}


class ServerAddressesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.found = {A: ['ens3'], B: ['ens3'], C: ['ens3'], V6: ['ens3']}
        self.enterContext(patch.object(addresses, 'detect', side_effect=lambda: self.found))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(manager, 'DATA_ROOT', self.root/'mihomo'))

    def configure(self, address, role, revision=None, change_id='a'*32):
        return addresses.configure(self.root, address, role, revision or addresses.policy(self.root)['revision'], change_id, (A, V6))

    def test_all_public_interface_addresses_are_canonical_deduplicated_and_filtered(self):
        rows = [interface('ens3', [A, B, C, V6, '127.0.0.1', '10.0.0.1', '100.64.0.1', '169.254.1.1', '224.0.0.1', 'fc00::1', 'fe80::1', 'ff02::1', '2001:db8::1']), interface('eth1', [A]),
                {**interface('down0', ['8.8.8.8']), 'flags': []}, interface('tentative0', ['2606:4700::1001'], tentative=True), interface('old0', ['9.9.9.9'], flags=['deprecated'])]
        self.assertEqual(addresses.parse_interfaces(rows), {A: ['ens3','eth1'], B: ['ens3'], C: ['ens3'], V6: ['ens3']})
        for value in [None, {}, [{'ifname': 'x'}], [interface('x', ['bad']), {'ifname': 'x', 'flags': ['UP'], 'addr_info': [None]}]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                addresses.parse_interfaces(value)

    def test_initial_primary_preserves_configured_address_and_reads_do_not_write_policy(self):
        result = addresses.inventory(self.root, (C, V6))
        self.assertEqual({row['address'] for row in result['items']}, set(self.found))
        self.assertEqual([row['address'] for row in result['items'] if row['role'] == 'primary'], [C, V6])
        self.assertFalse((self.root/'server-addresses.json').exists())

    def test_atomic_primary_change_demotes_only_same_family_and_keeps_exact_receipt(self):
        result = self.configure(B, 'primary')
        roles = {row['address']: row['role'] for row in result['items']}
        self.assertEqual(roles, {A:'reserve',B:'primary',C:'additional',V6:'primary'})
        before = (self.root/'server-addresses.json').read_bytes()
        # A lost HTTP acknowledgment can be reconciled without another PUT.
        observed = addresses.inventory(self.root, (A,V6), change_id='a'*32)
        self.assertEqual(observed['change'], result['change'])
        replayed = addresses.configure(self.root,B,'primary','0'*32,'a'*32,(A,V6))
        self.assertEqual({key: replayed[key] for key in ('revision','change','items')}, {key: observed[key] for key in ('revision','change','items')})
        self.assertEqual((self.root/'server-addresses.json').read_bytes(), before)
        with self.assertRaises(OperationConflict):
            self.configure(C,'primary',revision='0'*32,change_id='b'*32)
        with self.assertRaises(OperationConflict):
            self.configure(C,'primary',change_id='a'*32)

    def test_primary_cannot_be_removed_and_unassigned_addresses_cannot_be_selected(self):
        for address, role, error in [(A,'reserve',ValueError), ('8.8.8.8','primary',OperationConflict), ('10.0.0.1','primary',ValueError)]:
            with self.subTest(address=address), self.assertRaises(error):
                self.configure(address,role)
        self.assertFalse((self.root/'server-addresses.json').exists())

    def test_discovery_failure_or_missing_primary_preserves_roles_and_blocks_new_export(self):
        self.configure(B,'primary')
        before = (self.root/'server-addresses.json').read_bytes()
        self.found = {A:['ens3']}
        result = addresses.inventory(self.root,(A,))
        row = next(row for row in result['items'] if row['address'] == B)
        self.assertEqual((row['role'],row['presence']),('primary','missing'))
        with self.assertRaises(ValueError):
            addresses.primary_for_new_config(self.root,A)
        with patch.object(addresses,'detect',side_effect=OSError('unavailable')):
            result = addresses.inventory(self.root,(A,))
            self.assertEqual(result['discovery']['state'],'error')
            self.assertEqual(next(row for row in result['items'] if row['address']==B)['role'],'primary')
            with self.assertRaises(OperationConflict):
                self.configure(A,'primary',change_id='b'*32)
        self.assertEqual((self.root/'server-addresses.json').read_bytes(),before)

    def test_configured_nat_address_is_not_falsely_confirmed_as_assigned(self):
        self.found = {}
        result = addresses.inventory(self.root,(A,))
        self.assertEqual((result['items'][0]['presence'],result['items'][0]['role']),('unknown','primary'))
        with self.assertRaises(OperationConflict):
            self.configure(A,'reserve')
        self.assertEqual(addresses.primary_for_new_config(self.root,A),A)

    def test_corrupt_policy_never_resets_to_empty(self):
        path = self.root/'server-addresses.json'
        for value in ['broken',json.dumps({'revision':'0'*32,'roles':{A:'primary',B:'primary'}})]:
            path.write_text(value)
            with self.assertRaises(ValueError):
                addresses.inventory(self.root,(A,))
            with self.assertRaises(ValueError):
                addresses.primary_for_new_config(self.root,A)
            self.assertEqual(path.read_text(),value)

    def test_api_requires_auth_revision_and_known_role_and_obeys_global_admission(self):
        self.enterContext(patch.object(api,'ADMIN_USER','admin'))
        self.enterContext(patch.object(api,'ADMIN_PASSWORD','test-password'))
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/network/addresses').status_code,401)
        self.assertEqual(client.put('/api/network/addresses',json={}).status_code,401)
        auth=('admin','test-password')
        payload={'address':B,'role':'primary','expected_revision':'0'*32,'operation_id':'a'*32}
        self.assertEqual(client.put('/api/network/addresses',auth=auth,json={**payload,'role':'invalid'}).status_code,422)
        self.assertEqual(client.put('/api/network/addresses',auth=auth,json={**payload,'expected_revision':'old'}).status_code,422)
        result=client.put('/api/network/addresses',auth=auth,json=payload)
        self.assertEqual(result.status_code,200,result.text)
        observed=client.get('/api/network/addresses?change_id='+'a'*32,auth=auth).json()
        self.assertEqual(observed['change']['id'],'a'*32)
        self.assertEqual(client.put('/api/network/addresses',auth=auth,json={**payload,'operation_id':'b'*32}).status_code,409)
        (self.root/'relay').mkdir(); (self.root/'relay/pending.json').write_text('{}')
        self.assertEqual(client.put('/api/network/addresses',auth=auth,json={**payload,'expected_revision':observed['revision'],'operation_id':'c'*32}).status_code,409)

    def test_primary_affects_only_new_direct_and_mihomo_credentials(self):
        self.assertEqual(api.new_client_endpoint(A),A)
        self.configure(B,'primary')
        self.assertEqual(api.new_client_endpoint(A),B)
        original = {'port':1234,'method':'aes-128-gcm','password':'retained'}
        with patch.object(manager,'add_ss_credential',return_value=dict(original)):
            new = manager.provision('profile','transport-shadowsocks','new')
        self.assertEqual(new['server_address'],B)
        self.configure(C,'primary',change_id='b'*32)
        self.assertEqual(api.new_client_endpoint(A),C)
        self.assertEqual(new['server_address'],B)
        with patch.object(manager,'public_endpoint',return_value=A):
            import yaml
            saved=yaml.safe_load('proxies:\n'+'\n'.join(manager.render_proxy('transport-shadowsocks',new,'saved')))['proxies'][0]
            legacy=yaml.safe_load('proxies:\n'+'\n'.join(manager.render_proxy('transport-shadowsocks',original,'legacy')))['proxies'][0]
        self.assertEqual(saved['server'],B); self.assertEqual(legacy['server'],A)
        self.assertEqual(original,{'port':1234,'method':'aes-128-gcm','password':'retained'})

    def test_mihomo_checks_address_before_provisioning_and_preserves_yaml_endpoint(self):
        self.configure(B,'primary')
        self.found = {A:['ens3']}
        with patch.object(manager,'add_ss_credential') as provision:
            with self.assertRaises(manager.HTTPException) as raised:
                manager.provision('profile','transport-shadowsocks','new')
            self.assertEqual(raised.exception.status_code,409)
            provision.assert_not_called()
        import yaml
        credential={'server_address':B,'port':1234,'method':'aes-128-gcm','password':'retained'}
        proxy=yaml.safe_load('proxies:\n'+'\n'.join(manager.render_proxy('transport-shadowsocks',credential,'Saved')))['proxies'][0]
        self.assertEqual((proxy['server'],proxy['password']),(B,'retained'))

    def test_new_wg_awg_exports_use_primary_without_rewriting_existing_clients(self):
        import ipaddress
        self.configure(B,'primary')
        path=self.root/'tunnel.conf'
        path.write_text('[Interface]\nListenPort = 51820\nMTU = 1280\n')
        self.enterContext(patch.object(api,'WG_CONFIG',path))
        self.enterContext(patch.object(api,'AWG_CONFIG',path))
        self.enterContext(patch.object(api,'PUBLIC_IP_ENDPOINT',A))
        self.enterContext(patch.object(api,'key',return_value='retained-generated-key'))
        self.enterContext(patch.object(api,'next_address',return_value=ipaddress.ip_address('10.73.0.2')))
        self.enterContext(patch.object(api,'append_peer'))
        self.enterContext(patch.object(api,'run_with_input'))
        self.enterContext(patch.object(api,'run',return_value='server-public-key'))
        self.enterContext(patch.object(api,'current_env_value',side_effect=lambda key,value: value))
        old={'id':'old','protocol':'wg','route_endpoint':A,'public_key':'old-key'}
        self.enterContext(patch.object(api,'read_clients',side_effect=lambda:[dict(old)]))
        save=self.enterContext(patch.object(api,'write_clients'))
        for protocol in ('wg','awg'):
            result=api.create_client_native(api.ClientCreate(name='New',protocol=protocol))
            self.assertIn('Endpoint = '+B+':51820',result['config'])
            saved=save.call_args.args[0]
            self.assertEqual(saved[0],old)
            self.assertEqual(saved[1]['route_endpoint'],B)
            self.assertEqual('ListenPort = 53020' in result['config'],protocol=='awg')

    def test_nat_primary_and_new_origin_addresses_remain_compatible_with_relay(self):
        self.found={B:['ens3']}
        self.configure(B,'reserve')
        with patch.object(api,'PUBLIC_IP_ENDPOINT',A):
            self.assertEqual(api.new_client_endpoint(''),A)
        self.assertIn(B,api.relay_origin_hosts())

    def test_saved_reality_ip_does_not_replace_tls_identity_in_yaml_export(self):
        import yaml
        credential={'server_address':B,'port':1234,'uuid':'00000000-0000-4000-8000-000000000001','direct_tag':'owned','transport':'raw','servername':'cover.example','public_key':'public-key','short_id':'0123456789abcdef'}
        proxy=yaml.safe_load('proxies:\n'+'\n'.join(manager.render_proxy('transport-reality',credential,'test')))['proxies'][0]
        self.assertEqual((proxy['server'],proxy['servername']),(B,'cover.example'))
