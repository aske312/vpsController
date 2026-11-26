import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
import relay_correlation as correlation
import relay_diagnostics


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = {'id':'h'*32, 'token':'s'*48}
        self.route = {'id':'pro-'+'a'*32, 'host_id':self.host['id'], 'transport':'tcp',
                      'listen_port':20001, 'target_ip':'8.8.8.8', 'target_port':443}
        self.registry = {'hosts':[self.host], 'routes':[self.route]}
        self.control = SimpleNamespace(root=self.root/'relay', origin='8.8.8.8', read=lambda:self.registry)
        self.ref = correlation.route_reference(self.host['token'], self.route)
        self.channel = {'id':'mihomo:p:c', 'label':'private connection', 'route_ids':[self.route['id']]}
        self.profiles = [{'id':'p', 'name':'private customer', 'connections':[{'id':'c','device_id':'private device'}]}]
        self.summaries = [{'id':'p','name':'private customer','channels':[self.channel]}]
        self.enterContext(patch('relay_connections.profile_summaries', side_effect=lambda *args:self.summaries))

    def test_cross_runtime_reference_vector_and_generation_separation(self):
        self.assertEqual(self.ref, '52d37c98e7fec7a5e3a81478794ac7f1')
        self.assertNotEqual(self.ref,correlation.route_reference('t'*48,self.route))
        self.assertNotEqual(self.ref,correlation.route_reference(self.host['token'],{**self.route,'target_port':8443}))

    def test_resolution_is_local_and_never_claims_a_client_was_authenticated(self):
        result = correlation.resolve(self.control,self.host['id'],self.ref,self.profiles)
        self.assertEqual(result['route_id'],self.route['id'])
        self.assertEqual(result['candidates'][0]['device_id'],'private device')
        self.assertEqual(result['client_attribution'],'not_proven')
        self.summaries[0]['channels'].append({**self.channel,'id':'mihomo:p:other'})
        result = correlation.resolve(self.control,self.host['id'],self.ref,self.profiles)
        self.assertTrue(result['shared_route'])
        self.assertEqual(len(result['candidates']),2)
        self.assertIsNone(correlation.resolve(self.control,'other-host',self.ref,self.profiles))

    def test_controller_archive_survives_deletion_and_records_binding_changes(self):
        now=time.time()
        correlation.remember(self.control,self.profiles,now=now)
        path=self.control.root/'diagnostic-bindings.json'
        original=path.read_bytes()
        correlation.remember(self.control,self.profiles,now=now+1)
        self.assertEqual(path.read_bytes(),original)
        self.summaries[0]['name']='new owner'
        correlation.remember(self.control,self.profiles,now=now+2)
        self.registry['routes']=[]
        result=correlation.resolve(self.control,self.host['id'],self.ref,[])
        self.assertEqual(result['scope'],'archived_configuration_candidates')
        self.assertEqual(len(result['candidate_history']),2)
        self.assertEqual(result['candidate_history'][0]['candidates'][0]['profile_name'],'private customer')
        self.assertEqual(result['candidates'][0]['profile_name'],'new owner')
        raw=path.read_text(encoding='utf-8')
        self.assertNotIn(self.host['token'],raw)
        self.assertNotIn('8.8.8.8',raw)
        correlation.remember(self.control,[],now=now+8*86400)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['items'],[])

    def test_resolver_is_authenticated_and_responses_are_not_cached(self):
        self.enterContext(patch.object(api,'DATA_DIR',self.root))
        self.enterContext(patch.object(api,'relay_control',return_value=self.control))
        client=TestClient(api.app)
        url=f'/api/network/relays/{self.host["id"]}/diagnostics/{self.ref}'
        self.assertEqual(client.get(url).status_code,401)
        api.app.dependency_overrides[api.require_token]=lambda:None
        self.addCleanup(api.app.dependency_overrides.clear)
        response=client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(client.get(url[:-32]+'f'*32).status_code,404)

    def test_diagnostic_references_are_strictly_validated(self):
        base={'sent_packets':0,'received_packets':0,'dropped_packets':0,'route_ref':'a'*32,'session_refs':['b'*32]}
        self.assertEqual(relay_diagnostics.diagnostics(base)['session_refs'],['b'*32])
        for change in ({'route_ref':'8.8.8.8'},{'session_refs':['b'*32]*2},{'session_refs':['f'*32]*17},
                       {'session_refs':['profile-name']},{'route_ref':None}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                relay_diagnostics.diagnostics({**base,**change})


if __name__=='__main__':unittest.main()
