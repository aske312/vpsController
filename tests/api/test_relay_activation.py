import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from tests.api.support import api, manager, ROOT
from tests.api.test_relay_manager import FakeAgent, ORIGIN, AGENT, TOKEN, PIN
from relay_client import AgentAPIError
from relay_manager import RelayManager
from application_operation import OperationConflict
import relay_activation
import relay_connections


class ActivationAgent(FakeAgent):
    def __init__(self):
        super().__init__();self.enabled=True;self.receipts={};self.not_applied=False

    def request(self,method,path,body=None,revision=None):
        if path.startswith('/v1/activation/'):
            self.calls.append((method,path));identity=path.rsplit('/',1)[1]
            if self.offline:raise OSError('offline')
            if method=='GET':
                if identity not in self.receipts:raise AgentAPIError(404,'not found')
                return copy.deepcopy(self.receipts[identity])
            if self.not_applied:raise OSError('request not delivered')
            if identity not in self.receipts:
                if revision!=self.revision:raise AgentAPIError(409,'revision changed')
                self.enabled=body['enabled'];self.revision+=1
                self.receipts[identity]={'id':identity,'enabled':self.enabled,'observed_enabled':self.enabled,
                                         'state':'applied','revision':self.revision}
            if self.lose_ack:raise OSError('response lost')
            return copy.deepcopy(self.receipts[identity])
        value=super().request(method,path,body,revision)
        if path=='/v1/status':value.update(enabled=self.enabled,capabilities=value['capabilities']+['activation-v1'])
        if path=='/v1/routes' and not self.enabled:
            for row in value['items']:row.update(state='disabled',sessions=0)
        return value


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.agent=ActivationAgent()
        self.control=RelayManager(self.root,ORIGIN,lambda *_:self.agent)
        self.host='b'*32
        self.route={'id':'pro-'+'c'*32,'host_id':self.host,'transport':'tcp','listen_port':20005,'target_ip':ORIGIN,'target_port':443}
        self.agent.items=[{k:v for k,v in self.route.items() if k!='host_id'}|{'state':'listening','sessions':0,'target_verified':False}]
        self.control.write({'schema':1,'hosts':[{'id':self.host,'name':'Relay','agent_url':'https://'+AGENT+':9443','token':TOKEN,'certificate_sha256':PIN,'api_version':1}], 'routes':[self.route]})

    def switch(self,enabled,identity='d'*32):
        relay_activation.perform(self.control,{'host_id':self.host,'expected_revision':self.control.read()['revision'],'enabled':enabled},identity)

    def test_disable_retains_inventory_settings_and_bindings_then_reenable_restores(self):
        before=copy.deepcopy(self.control.read());self.switch(False)
        after=self.control.read();self.assertEqual(after['routes'],before['routes'])
        self.assertEqual({k:v for k,v in after['hosts'][0].items() if k!='enabled'},before['hosts'][0])
        self.assertFalse(after['hosts'][0]['enabled']);self.assertFalse(self.control.pending.exists())
        for probe in (True,False):
            host=self.control.public(probe=probe)['hosts'][0]
            self.assertFalse(host['enabled']);self.assertEqual(host['id'],self.host)
            if probe:self.assertEqual(host['routes'][0]['state'],'disabled')
        settings={'relay_host_id':self.host}
        state=relay_connections.consumer_state(self.control,settings,{'state':'current','reason':'','addresses':['saved.example'],'bindings':['saved-binding']})
        self.assertEqual(state['state'],'disabled');self.assertEqual(state['bindings'],['saved-binding'])
        self.assertEqual(self.control.endpoint(self.host,'tcp',443),(AGENT,20005))
        with self.assertRaises(ValueError):self.control.validate_client_address(self.host)
        self.switch(True,'e'*32)
        self.assertTrue(self.control.read()['hosts'][0]['enabled']);self.assertEqual(self.control.read()['routes'],before['routes'])

    def test_lost_ack_is_resolved_by_read_with_one_put(self):
        self.agent.lose_ack=True;self.switch(False)
        self.assertEqual(sum(method=='PUT' for method,_ in self.agent.calls),1)
        self.assertFalse(self.control.pending.exists());self.assertFalse(self.agent.enabled)

    def test_profile_save_and_export_preserve_disabled_route_without_reprovisioning(self):
        connection={'id':'saved','component':'transport-reality','settings':{'relay_host_id':self.host,'route_mode':'direct'},
                    'credential':{'port':443,'direct_tag':'fixture'}}
        lines=['    server: origin.example','    port: 443','    password: fixture']
        before=relay_connections.redirect_mihomo_lines(self.control,connection['component'],connection['settings'],lines)
        self.switch(False);calls=list(self.agent.calls)
        normalized=copy.deepcopy(connection);normalized['settings'].update(relay_address='',relay_cdn=False,relay_cdn_mode='after_cdn',cdn_domain='')
        relay_connections.prepare_mihomo(self.control,[normalized],'f'*32,previous=[copy.deepcopy(connection)])
        self.assertEqual(self.agent.calls,calls)
        self.assertEqual(relay_connections.redirect_mihomo_lines(self.control,connection['component'],connection['settings'],lines),before)
        changed=copy.deepcopy(connection);changed['credential']['port']=8443
        with self.assertRaises(ValueError):
            relay_connections.prepare_mihomo(self.control,[changed],'f'*32,previous=[connection])

    def test_pending_activation_does_not_claim_traffic_is_already_off(self):
        self.agent.not_applied=True
        with self.assertRaises(OperationConflict):self.switch(False)
        fallback={'state':'current','addresses':['saved.example'],'bindings':['saved-binding']}
        state=relay_connections.consumer_state(self.control,{'relay_host_id':self.host},fallback)
        self.assertEqual(state['state'],'unknown');self.assertEqual(state['bindings'],fallback['bindings'])
        self.assertTrue(self.agent.enabled)

    def test_unknown_command_is_not_replayed_and_explicit_resume_reuses_identity(self):
        self.agent.not_applied=True
        with self.assertRaises(OperationConflict):self.switch(False)
        self.assertTrue(self.control.pending.exists());self.assertFalse(self.control.read()['hosts'][0]['enabled'])
        with self.assertRaises(OperationConflict):self.control.reconcile()
        self.assertEqual(sum(method=='PUT' for method,_ in self.agent.calls),1)
        recovery=self.control.public(probe=False)['recovery']
        self.assertEqual(recovery['action'],'activation');self.assertFalse(recovery['enabled'])
        self.agent.not_applied=False;self.control.resume('d'*32)
        puts=[path for method,path in self.agent.calls if method=='PUT']
        self.assertEqual(puts,['/v1/activation/'+'d'*32]*2)
        self.assertFalse(self.control.pending.exists());self.assertFalse(self.agent.enabled)

    def test_intervening_revision_fences_an_undelivered_command(self):
        self.agent.not_applied=True
        with self.assertRaises(OperationConflict):self.switch(False)
        self.agent.revision+=1
        with self.assertRaises(OperationConflict):self.control.reconcile()
        self.assertFalse(self.control.pending.exists());self.assertTrue(self.control.read()['hosts'][0]['enabled'])
        self.assertEqual(sum(method=='PUT' for method,_ in self.agent.calls),1)

    def test_old_agent_and_stale_registry_fail_before_writes(self):
        before=self.control.file.read_bytes()
        with self.assertRaises(OperationConflict):
            relay_activation.perform(self.control,{'host_id':self.host,'expected_revision':'0'*32,'enabled':False},'d'*32)
        request=self.agent.request
        def old(method,path,body=None,revision=None):
            value=request(method,path,body,revision)
            if path=='/v1/status':value['capabilities']=[];value.pop('enabled',None)
            return value
        with patch.object(self.agent,'request',side_effect=old),self.assertRaises(ValueError):self.switch(False)
        self.assertEqual(self.control.file.read_bytes(),before);self.assertFalse(self.control.pending.exists())

    def test_profile_can_keep_existing_disabled_reference_but_cannot_select_it_anew(self):
        self.switch(False)
        with patch.object(manager,'SUBMODULE_ROOT',ROOT/'protocol-images/mihomo/modules'),patch.object(manager,'module_is_installed',return_value=True),patch.object(manager,'module_is_ready',return_value=True),patch.object(manager,'relay_connection_control',return_value=self.control):
            with self.assertRaises(manager.HTTPException) as failure:
                manager.validate_connection('transport-tuic',{'relay_host_id':self.host})
            self.assertEqual(failure.exception.status_code,409)
            kept=manager.validate_connection('transport-tuic',{'relay_host_id':self.host},allow_disabled_relay=True)
            self.assertEqual(kept['relay_host_id'],self.host)

    def test_activation_http_auth_and_strict_input_use_standard_operation(self):
        client=TestClient(api.app)
        url='/api/network/relays/'+self.host+'/activation'
        self.assertEqual(client.put(url,json={'enabled':False,'expected_revision':'a'*32}).status_code,401)
        old=dict(api.app.dependency_overrides)
        api.app.dependency_overrides[api.require_token]=lambda:None
        api.app.dependency_overrides[api.require_mutation_admission]=lambda:None
        try:
            self.assertEqual(client.put(url,json={'enabled':'false','expected_revision':'a'*32}).status_code,422)
            with patch.object(api,'start_relay_mutation',return_value={'id':'d'*32,'state':'queued'}) as operation:
                self.assertEqual(client.put(url,json={'enabled':False,'expected_revision':'a'*32}).status_code,202)
                self.assertEqual(operation.call_args.args[0],'relay-host-activation')
        finally:api.app.dependency_overrides.clear();api.app.dependency_overrides.update(old)


if __name__=='__main__':unittest.main()
