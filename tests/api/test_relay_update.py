import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.api.support import api
import relay_update
from schemas import RelayAgentUpdate


class RelayUpdateTests(unittest.TestCase):
    def test_discovery_requires_matching_release_assets_and_metadata_checksums(self):
        metadata = json.dumps({'version':'1.4.0','digest':'sha256:'+'a'*64}).encode()
        def asset(name, digest): return {'name':name,'browser_download_url':relay_update.PREFIX+name,'digest':digest}
        release = {'tag_name':'agent-latest','assets':[asset('relay-agent.tar.gz','sha256:'+'a'*64),asset('relay-agent-version.json','sha256:'+hashlib.sha256(metadata).hexdigest())]}
        with patch.object(relay_update,'fetch',side_effect=[json.dumps(release).encode(),metadata]):
            self.assertEqual(relay_update.latest(force=True)['version'],'1.4.0')
        with patch.object(relay_update,'fetch',side_effect=[json.dumps(release).encode(),b'corrupted']):
            self.assertIsNone(relay_update.latest(force=True))
        release['assets'][0]['browser_download_url']='https://untrusted.example/package'
        with patch.object(relay_update,'fetch',return_value=json.dumps(release).encode()):
            self.assertIsNone(relay_update.latest(force=True))

    def setup_context(self, root):
        status = {'api_version':1,'version':'1.4.0','revision':0,'routes':0,'failed_routes':0,'max_routes':64,'address_family':'ipv4','transports':['tcp','udp'],'listen_ports':{'min':20000,'max':20999},'capabilities':['manual-update-v1']}
        client = Mock(); client.request.return_value=status
        control = SimpleNamespace(checked=lambda _: {},host=lambda *_:{'id':'host'},client=lambda _:client)
        context = SimpleNamespace(DATA_DIR=root,relay_control=lambda:control)
        release = {'version':'1.5.0','digest':'sha256:'+'a'*64}
        return context, client, status, release

    def test_lost_ack_is_not_replayed_and_running_version_confirms_result(self):
        with tempfile.TemporaryDirectory() as folder:
            context, client, status, release = self.setup_context(Path(folder))
            identity = 'b'*32; payload = {**release,'host_id':'host','expected_revision':'revision'}
            client.request.side_effect=[status,OSError('lost response')]
            with patch.object(relay_update,'latest',return_value=release), self.assertRaises(ValueError):
                relay_update.perform(context,payload,identity)
            self.assertEqual(json.loads(relay_update.path(context,'host').read_text())['state'],'unknown')
            relay_update.perform(context,payload,identity)
            self.assertEqual(sum(call.args[0]=='PUT' for call in client.request.call_args_list),1)
            host = {'id':'host','state':'connected','version':'1.5.0'}
            with patch.object(relay_update,'latest',return_value=release): relay_update.annotate(context,[host])
            self.assertEqual(host['agent_update']['state'],'updated')
            self.assertEqual(host['agent_release']['state'],'current')

    def test_only_newer_published_version_and_capable_agent_can_be_updated(self):
        with tempfile.TemporaryDirectory() as folder:
            context, client, status, release = self.setup_context(Path(folder))
            payload = {**release,'host_id':'host','expected_revision':'revision'}
            for changes, offered in [({'capabilities':[]},release), ({'version':'1.5.0'},release), ({},{**release,'digest':'sha256:'+'c'*64}), ({},None)]:
                client.request.return_value={**status,**changes}
                with patch.object(relay_update,'latest',return_value=offered),self.assertRaises(ValueError): relay_update.perform(context,payload,'b'*32)
            self.assertFalse(relay_update.path(context,'host').exists())
            self.assertTrue(all(call.args[0]=='GET' for call in client.request.call_args_list))

    def test_manual_report_does_not_prove_running_version_and_disconnect_keeps_polling(self):
        with tempfile.TemporaryDirectory() as folder:
            context, _, _, release = self.setup_context(Path(folder))
            intent = {**release,'id':'b'*32,'state':'queued','requested_at':time.time()}
            relay_update.atomic_json(relay_update.path(context,'host'),intent)
            for connection in ['unknown','connected']:
                host = {'id':'host','state':connection,'version':'1.4.0','updates':{'manual':{**intent,'state':'updated'}}}
                with patch.object(relay_update,'latest',return_value=release): relay_update.annotate(context,[host])
                self.assertEqual(host['agent_update']['state'],'unknown')

    def test_request_schema_does_not_accept_urls_or_commands(self):
        value = {'version':'1.5.0','digest':'sha256:'+'a'*64,'expected_revision':'b'*32}
        RelayAgentUpdate.model_validate(value)
        for extra in [{'command':'reboot'},{'url':'https://untrusted.example'}]:
            with self.assertRaises(ValueError): RelayAgentUpdate.model_validate({**value,**extra})

    def test_missing_request_is_only_confirmed_by_authenticated_read_404(self):
        with tempfile.TemporaryDirectory() as folder:
            context, client, _, _ = self.setup_context(Path(folder))
            context.relay_control().read=lambda: {}
            client.request.side_effect=relay_update.AgentAPIError(404,'missing')
            self.assertTrue(relay_update.missing_request(context,'host','b'*32))
            client.request.side_effect=OSError('offline')
            self.assertFalse(relay_update.missing_request(context,'host','b'*32))
            self.assertTrue(all(call.args[0]=='GET' for call in client.request.call_args_list))


if __name__ == '__main__': unittest.main()
