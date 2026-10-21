import base64
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
import zipfile

from tests.api.support import api, manager
import relay_device_test
import relay_measurement
import relay_test_cleanup
import relay_test_peer
from network_probe import ProbePlan, ProbeUnavailable
from schemas import RelayDeviceReport


class LossTests(unittest.TestCase):
    def observations(self):
        rows = list(range(100))
        return ({'state': 'completed', 'sent_sequences': rows, 'received_sequences': rows, 'rtt_ms': 12},
                {'received_sequences': rows, 'forwarded_sequences': rows, 'returned_sequences': rows, 'sent_sequences': rows, 'local_drops': 0, 'socket_errors': 0, 'upstream_rtt_ms': 7},
                {'received_sequences': rows, 'sent_sequences': rows})

    def test_path_is_verified_only_by_all_observers(self):
        result = relay_measurement.analyse(*self.observations())
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['loss_percent'], 0)
        self.assertEqual(result['scope'], 'diagnostic_udp_path')

    def test_each_leg_has_separate_loss_and_inconsistent_reports_are_unknown(self):
        client, agent, origin = self.observations()
        client['received_sequences'] = list(range(96))
        agent['received_sequences'] = agent['forwarded_sequences'] = list(range(99))
        origin['received_sequences'] = origin['sent_sequences'] = list(range(98))
        agent['returned_sequences'] = agent['sent_sequences'] = list(range(97))
        result = relay_measurement.analyse(client, agent, origin)
        self.assertEqual([result[key] for key in ('client_to_relay_lost', 'relay_to_vps_lost', 'vps_to_relay_lost', 'relay_to_client_lost')], [1, 1, 1, 1])
        self.assertEqual(result['loss_percent'], 4)
        client['received_sequences'] = [99]
        self.assertEqual(relay_measurement.analyse(client, agent, origin)['reason'], 'measurement_inconsistent')

    def test_zero_reply_is_localized_without_blame_or_http_success(self):
        client, agent, origin = self.observations()
        client['received_sequences'] = []
        for key in ('received_sequences', 'forwarded_sequences', 'returned_sequences', 'sent_sequences'): agent[key] = []
        origin['received_sequences'] = origin['sent_sequences'] = []
        self.assertEqual(relay_measurement.analyse(client, agent, origin)['reason'], 'client_to_relay_no_packets')
        agent['received_sequences'] = agent['forwarded_sequences'] = list(range(100))
        self.assertEqual(relay_measurement.analyse(client, agent, origin)['reason'], 'relay_to_vps_no_reply')

    def test_duplicates_bad_numbers_and_untrusted_rtt_do_not_render(self):
        for values in ([1, 1], [-1], [100], [True], ['1']):
            with self.assertRaises(ValueError): relay_measurement.sequences(values)
        client, agent, origin = self.observations()
        agent['upstream_rtt_ms'] = {'error': 'private remote detail'}
        with self.assertRaises(ValueError): relay_measurement.analyse(client, agent, origin)


class PeerTests(unittest.TestCase):
    def test_mihomo_allocator_reserves_kernel_only_test_lease(self):
        journal=self.directory/'cleanup.json'
        relay_test_cleanup.atomic(journal,{'peer':{'interface':'mh-wg0','allowed_ip':'10.83.0.2/32'},'peer_removed':False})
        config=self.root/'wg.conf';config.write_text('[Interface]\n')
        with patch.object(manager,'DATA_ROOT',self.root/'mihomo'),patch.object(manager,'profiles',return_value=[]),patch.object(manager,'module_settings',return_value={'subnet':'10.83.0.0/24'}),patch.object(manager,'WG_CONFIG_BY_MODULE',{'transport-wg':config}):
            address,_=manager.next_tunnel_address('transport-wg')
        self.assertEqual(address,'10.83.0.3')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'mihomo'/'settings').mkdir(parents=True)
        self.server_public = base64.b64encode(b's' * 32).decode()
        self.original_public = base64.b64encode(b'o' * 32).decode()
        self.private = base64.b64encode(b'n' * 32).decode(); self.public = base64.b64encode(b'p' * 32).decode()
        self.peers = {self.original_public: '10.83.0.2/32'}; self.calls = []
        self.profiles = [{'id': 'profile', 'connections': [{'component': 'transport-awg', 'credential': {'ip': '10.83.0.2/24'}}]}]
        (self.root/'mihomo'/'profiles.json').write_text(json.dumps(self.profiles))
        (self.root/'mihomo'/'settings'/'transport-awg.json').write_text('{"port":51831}')
        self.directory = self.root/'relay-tests'/('a'*32); self.directory.mkdir(parents=True)
        relay_test_cleanup.atomic(self.directory/'cleanup.json', {'expires_at': time.time()+1200})
        self.context = SimpleNamespace(DATA_DIR=self.root)
        node = {'type': 'wireguard', 'ip': '10.83.0.2', 'private-key': 'original-secret', 'public-key': self.server_public, 'server': '1.1.1.1', 'port': 20001}
        self.plan = ProbePlan('route', 1, 'mihomo:profile:connection', '1.1.1.1', 20001, '', 51831, 'udp', 'fp', node, engine='mihomo')

    def tearDown(self): self.temp.cleanup()

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'ip': return SimpleNamespace(stdout=json.dumps([{'addr_info': [{'family':'inet','local':'10.83.0.1','prefixlen':24}]}]), returncode=0)
        output = ''
        if args[1:] == ['genkey']: output = self.private
        elif args[1:] == ['pubkey']: output = self.public
        elif args[-1] == 'listen-port': output = '51831'
        elif args[-1] == 'public-key': output = self.server_public
        elif args[-1] == 'allowed-ips': output = '\n'.join(key+'\t'+value for key,value in self.peers.items())
        elif args[1] == 'set':
            if args[-1] == 'remove': self.peers.pop(args[4], None)
            elif args[-2] == 'allowed-ips': self.peers[args[4]] = args[-1]
        return SimpleNamespace(stdout=output, returncode=0)

    def test_disposable_peer_never_uses_active_key_or_changes_saved_profiles(self):
        before = (self.root/'mihomo'/'profiles.json').read_bytes()
        with patch.object(relay_test_peer.subprocess, 'run', side_effect=self.command):
            plan = relay_test_peer.reserve_peer(self.context, self.plan, {'component': 'transport-awg'}, self.directory)
            self.assertEqual(plan.client['private-key'], self.private)
            self.assertEqual(plan.client['ip'], '10.83.0.254')
            self.assertEqual(self.peers[self.original_public], '10.83.0.2/32')
            self.assertTrue(relay_test_cleanup.cleanup(self.directory/'cleanup.json'))
        self.assertEqual(self.peers, {self.original_public: '10.83.0.2/32'})
        self.assertEqual((self.root/'mihomo'/'profiles.json').read_bytes(), before)
        self.assertNotIn(self.private, (self.directory/'cleanup.json').read_text())

    def test_changed_peer_is_not_removed_and_cleanup_is_reported_failed(self):
        with patch.object(relay_test_peer.subprocess, 'run', side_effect=self.command):
            relay_test_peer.reserve_peer(self.context, self.plan, {'component': 'transport-awg'}, self.directory)
            self.peers[self.public] = '10.83.0.99/32'
            self.assertFalse(relay_test_cleanup.cleanup(self.directory/'cleanup.json'))
        self.assertEqual(self.peers[self.public], '10.83.0.99/32')
        self.assertIn('peer_cleanup_failed', (self.directory/'cleanup.json').read_text())

    def test_expiry_must_be_scheduled_before_a_peer_can_be_added(self):
        with patch.object(relay_test_peer.subprocess, 'run', side_effect=OSError), self.assertRaises(OSError):
            relay_test_peer.schedule_cleanup(self.context, self.directory)
        self.assertEqual(self.peers, {self.original_public: '10.83.0.2/32'})


class ClientKitTests(unittest.TestCase):
    def test_remote_cleanup_is_confirmed_by_read_only_observation(self):
        client = Mock()
        control = SimpleNamespace(read=lambda: {}, host=lambda *_: {}, client=lambda _: client)
        context = SimpleNamespace(relay_control=lambda: control)
        measurement = {'id': 'a'*32, 'host_id': 'host', 'route_id': 'route'}
        for state, expected in [('active', False), ('expired', True)]:
            client.request.return_value = {**measurement, 'state': state}
            self.assertEqual(relay_device_test.remote_cleanup_confirmed(context, measurement), expected)
        for error, expected in [(relay_device_test.AgentAPIError(404, 'missing'), True), (OSError('offline'), False)]:
            client.request.side_effect = error
            self.assertEqual(relay_device_test.remote_cleanup_confirmed(context, measurement), expected)
        self.assertTrue(all(call.args[0] == 'GET' for call in client.request.call_args_list))

    def test_recovery_does_not_claim_remote_cleanup_on_connection_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            context = SimpleNamespace(DATA_DIR=Path(folder)); identity = 'a'*32
            root = relay_device_test.directory(context, identity); root.mkdir(parents=True)
            relay_test_cleanup.atomic(root/'cleanup.json', {'expires_at': 0, 'measurement': {'id': identity}})
            relay_test_cleanup.atomic(root/'public.json', {'id': identity, 'state': 'processing', 'expires_at': 0})
            with patch.object(relay_device_test, 'remote_cleanup_confirmed', return_value=False):
                relay_device_test.recover(context)
            self.assertEqual(relay_device_test.read_public(context, identity)['cleanup_state'], 'pending')
            with patch.object(relay_device_test, 'remote_cleanup_confirmed', return_value=True):
                relay_device_test.recover(context)
            self.assertEqual(relay_device_test.read_public(context, identity)['cleanup_state'], 'cleaned')

    def test_worker_accepts_client_report_publishes_scoped_result_and_cleans_kit(self):
        from schemas import RelayDeviceReport
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); identity='a'*32
            directory=root/'relay-tests'/identity; directory.mkdir(parents=True)
            public={'id':identity,'host_id':'host','route_id':'route','consumer_id':'mihomo:profile:connection','state':'preparing','created_at':time.time(),'expires_at':time.time()+600,'vantage_point':'client','verification':'client_report_with_udp_witness'}
            route={'id':'route','host_id':'host','transport':'udp','listen_port':20001,'target_ip':'8.8.8.8','target_port':443}
            remote=Mock(); remote.request.return_value={'items':[{**route,'state':'listening'}]}
            host={'id':'host'}
            control=SimpleNamespace(checked=lambda _: {'routes':[route]},host=lambda *_:host,client=lambda _:remote)
            context=SimpleNamespace(DATA_DIR=root,PUBLIC_IPV4='8.8.8.8',relay_control=lambda:control,relay_manager=api.relay_manager,RelayDeviceReport=RelayDeviceReport)
            node={'type':'tuic','server':'1.1.1.1','port':20001,'password':'synthetic-secret'}
            plan=ProbePlan('route',1,public['consumer_id'],'1.1.1.1',20001,'',443,'udp','fingerprint',node,engine='mihomo')
            context.prepare_relay_media_probe=lambda *_:plan
            relay_test_cleanup.atomic(directory/'public.json',public)
            relay_test_cleanup.atomic(directory/'request.json',{'host_id':'host','route_id':'route','consumer_id':public['consumer_id'],'expected_revision':'revision'})
            relay_test_cleanup.atomic(directory/'cleanup.json',{'expires_at':time.time()+600})
            report={'id':identity,'token':'b'*64,'media':{'state':'ready','reason':'media_verified','verified_bytes':4372373,'range_checks':3,'seconds':10},'measurement':{'state':'completed','sent_sequences':list(range(100)),'received_sequences':list(range(100)),'rtt_ms':12}}
            relay_test_cleanup.atomic(directory/'report.json',report)
            measurement=MagicMock(); measurement.__enter__.return_value=measurement; measurement.__exit__.return_value=False; measurement.cleanup_pending=False; measurement.settings.return_value={'address':'1.1.1.1','port':20002,'key':'c'*64,'id':identity}
            measurement.result.return_value={'state':'ready','reason':'udp_verified','loss_percent':0}
            with patch.object(relay_device_test,'provisioning_lock',return_value=contextlib.nullcontext()), patch.object(relay_test_peer,'disposable_plan',return_value=contextlib.nullcontext(plan)), patch.object(relay_measurement,'Measurement',return_value=measurement), patch.object(relay_device_test.secrets,'token_hex',return_value='b'*64):
                relay_device_test.serve(context,identity)
            result=json.loads((directory/'public.json').read_text())
            self.assertEqual(result['state'],'ready')
            self.assertEqual(result['cleanup_state'],'cleaned')
            self.assertEqual(result['vantage_point'],'client')
            self.assertNotIn('synthetic-secret',json.dumps(result)); self.assertNotIn('b'*64,json.dumps(result))
            for name in ('private.json','kit.zip','request.json','report.json'): self.assertFalse((directory/name).exists())

    def test_report_token_and_route_are_bound_and_cancel_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            context=SimpleNamespace(DATA_DIR=Path(folder)); identity='a'*32
            root=relay_device_test.directory(context,identity); root.mkdir(parents=True)
            public={'id':identity,'host_id':'host','route_id':'route','state':'awaiting_client','expires_at':time.time()+600}
            relay_test_cleanup.atomic(root/'public.json',public); relay_test_cleanup.atomic(root/'private.json',{'token':'b'*64})
            payload={'host_id':'host','route_id':'route','test_id':identity,'report':{'id':identity,'token':'c'*64}}
            with self.assertRaises(ValueError): relay_device_test.accept(context,payload)
            self.assertFalse((root/'report.json').exists())
            with self.assertRaises(ValueError): relay_device_test.cancel(context,{**payload,'host_id':'other'})
            relay_device_test.cancel(context,payload)
            self.assertTrue((root/'cancel.json').exists())

    def test_recovery_cleans_expired_resources_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            context=SimpleNamespace(DATA_DIR=Path(folder)); identity='a'*32
            root=relay_device_test.directory(context,identity); root.mkdir(parents=True)
            relay_test_cleanup.atomic(root/'cleanup.json',{'expires_at':0})
            relay_test_cleanup.atomic(root/'public.json',{'id':identity,'state':'awaiting_client','created_at':0,'expires_at':0})
            (root/'private.json').write_text('private');(root/'kit.zip').write_text('private')
            relay_device_test.recover(context)
            self.assertFalse((root/'private.json').exists());self.assertFalse((root/'kit.zip').exists())
            self.assertEqual(json.loads((root/'public.json').read_text())['state'],'expired')

    def test_kit_contains_no_origin_or_admin_token_and_runs_without_api_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = {'id': 'a'*32, 'token': 'b'*64, 'engine': 'mihomo', 'expires_at': time.time()+600,
                        'client': {'type': 'tuic', 'server':'1.1.1.1', 'password':'profile-secret'}, 'measurement': {'address': '1.1.1.1'}}
            relay_device_test.build_kit(root, settings, '8.8.8.8')
            with zipfile.ZipFile(root/'kit.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'runner.py','test.json','README.txt'})
                runner = archive.read('runner.py').decode()
                self.assertNotIn('import network_probe', runner)
                self.assertNotIn('import yaml', runner)
                self.assertNotIn('8.8.8.8', archive.read('test.json').decode())
            settings['client']['server'] = '8.8.8.8'
            with self.assertRaises(ValueError): relay_device_test.build_kit(root, settings, '8.8.8.8')

    def test_expired_and_wrong_route_kits_cannot_be_downloaded(self):
        with tempfile.TemporaryDirectory() as folder:
            context = SimpleNamespace(DATA_DIR=Path(folder)); identity='a'*32
            root = relay_device_test.directory(context, identity); root.mkdir(parents=True)
            public={'id':identity,'host_id':'host','route_id':'route','state':'awaiting_client','expires_at':time.time()-1}
            relay_test_cleanup.atomic(root/'public.json', public); (root/'kit.zip').write_bytes(b'private')
            with self.assertRaises(ValueError): relay_device_test.kit(context, 'host', 'route', identity)
            public['expires_at']=time.time()+600; relay_test_cleanup.atomic(root/'public.json', public)
            with self.assertRaises(ValueError): relay_device_test.kit(context, 'other', 'route', identity)

    def test_cleanup_deletes_private_kit_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'cleanup.json'; relay_test_cleanup.atomic(path, {'expires_at':0})
            for name in ('private.json','kit.zip','request.json'): (root/name).write_text('private')
            (root/'runtime'/'worker').mkdir(parents=True);(root/'runtime'/'worker'/'client.json').write_text('private')
            self.assertTrue(relay_test_cleanup.cleanup(path)); self.assertTrue(relay_test_cleanup.cleanup(path))
            self.assertEqual({file.name for file in root.iterdir()}, {'cleanup.json'})

    def test_report_contract_rejects_credentials_extra_fields_and_noninteger_sequences(self):
        report={'id':'a'*32,'token':'b'*64,'media':{'state':'failed','reason':'timeout'},'measurement':{'state':'completed','sent_sequences':[0],'received_sequences':[],'rtt_ms':None}}
        RelayDeviceReport.model_validate(report)
        for change in ({'admin_password':'secret'}, {'measurement':{**report['measurement'],'sent_sequences':[True]}}):
            with self.assertRaises(ValueError): RelayDeviceReport.model_validate({**report,**change})


if __name__ == '__main__': unittest.main()
