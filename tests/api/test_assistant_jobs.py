import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from tests.api.support import api, ROOT, load_module
import assistant_jobs as jobs
import assistant_control as control
import application_operation as operations
runner = load_module('stream_test_runner', 'protocol-images/ai-agent/runner.py')

class ChatJobsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(control, 'read', return_value={'revision': 'r', 'model': 'm'}))
        self.launch = self.enterContext(patch.object(jobs.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)))
        self.enterContext(patch.object(api, 'observe_service', return_value={'active': True, 'state': 'active', 'unit_present': True}))
        self.payload = control.Chat(prompt='private question', request_id='a'*32, model='m')
    def test_accept_replay_and_conflicting_content_never_launch_twice(self):
        first=jobs.submit(api,self.payload)
        self.assertEqual(first['state'],'queued');self.assertNotIn('payload',first)
        self.assertEqual(jobs.submit(api,self.payload)['id'],first['id'])
        with self.assertRaises(HTTPException) as caught:
            jobs.submit(api,self.payload.model_copy(update={'prompt':'another'}))
        self.assertEqual(caught.exception.status_code,409);self.assertEqual(self.launch.call_count,1)
        args=self.launch.call_args.args[0]
        self.assertIn('--property=RuntimeMaxSec=6min',args)
        self.assertNotIn('private question',' '.join(args))
    def test_lost_launch_reply_is_not_replayed_and_can_finish(self):
        self.launch.side_effect=subprocess.TimeoutExpired('systemd-run',12)
        jobs.submit(api,self.payload);jobs.submit(api,self.payload)
        self.assertEqual(self.launch.call_count,1)
        result={'ok':True,'result':{'answer':'answer','tools_used':[]}}
        with patch.object(control,'chat_request',return_value=result) as chat:
            jobs.execute(api,self.payload.request_id);jobs.execute(api,self.payload.request_id)
        self.assertEqual(chat.call_count,1)
        value=jobs.get(api,self.payload.request_id)
        self.assertEqual(value['output'],result);self.assertNotIn('payload',operations.read(jobs.path(api,self.payload.request_id)))
        from observability import Store
        log=Store(self.root).read(60,limit=20)
        self.assertNotIn('private question',json.dumps(log));self.assertNotIn('answer',json.dumps(log))
    def test_actor_authorization_and_scope_apply_to_results(self):
        jobs.submit(api,self.payload)
        with patch.object(control.roles,'require') as require:
            with self.assertRaises(HTTPException) as caught: jobs.get(api,self.payload.request_id,'11')
            self.assertEqual(caught.exception.status_code,404);require.assert_called_with({'revision':'r','model':'m'},'11','chat')
        with self.assertRaises(HTTPException):jobs.get(api,'../secret')
    def test_worker_errors_are_durable_and_do_not_expose_raw_exceptions(self):
        jobs.submit(api,self.payload)
        with patch.object(control,'chat_request',side_effect=RuntimeError('sk-private-token')):jobs.execute(api,self.payload.request_id)
        value=jobs.get(api,self.payload.request_id)
        self.assertEqual(value['error']['code'],'internal_error');self.assertNotIn('sk-private',json.dumps(value))
        self.assertIn('duration_ms',value)
    def test_settings_change_prevents_generation(self):
        jobs.submit(api,self.payload)
        with patch.object(control,'read',return_value={'revision':'changed'}),patch.object(control,'chat_request') as chat:
            jobs.execute(api,self.payload.request_id)
        chat.assert_not_called();self.assertEqual(jobs.get(api,self.payload.request_id)['state'],'failed')
    def test_stopped_worker_is_unknown_not_restarted(self):
        jobs.submit(api,self.payload)
        file=jobs.path(api,self.payload.request_id);record=operations.read(file);record['created_at']-=40;operations.atomic_json(file,record)
        with patch.object(api,'observe_service',return_value={'unit_present':False,'state':'inactive'}): value=jobs.get(api,self.payload.request_id)
        self.assertEqual(value['state'],'unknown');self.assertEqual(self.launch.call_count,1)
        with patch.object(control,'chat_request') as chat:jobs.execute(api,self.payload.request_id)
        chat.assert_not_called()
    def test_terminal_retention_and_private_file_modes(self):
        jobs.submit(api,self.payload);jobs.update(api,self.payload.request_id,state='failed',error={'code':'test'})
        file=jobs.path(api,self.payload.request_id);record=operations.read(file);record['updated_at']-=jobs.RETENTION+1;operations.atomic_json(file,record)
        with self.assertRaises(HTTPException) as caught:jobs.get(api,self.payload.request_id)
        self.assertEqual(caught.exception.status_code,410)
        if os.name=='posix':self.assertEqual(file.stat().st_mode & 0o777,0o600)
    def test_runtime_event_reader_preserves_job_context_across_thread(self):
        import assistant_runtime
        import sys
        jobs.submit(api,self.payload)
        token=jobs.current_job.set((api,self.payload.request_id))
        try:
            script="import sys,json; json.load(sys.stdin); print(json.dumps({'event':'progress','stage':'receiving','text':'part'}),flush=True); print(json.dumps({'ok':True,'result':{'answer':'ok'}}),flush=True)"
            with subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True) as process:
                result,code=assistant_runtime.communicate(process,{'private':'not logged'},5)
            self.assertEqual(code,0);self.assertTrue(result['ok'])
            self.assertEqual(jobs.get(api,self.payload.request_id)['partial'],'part')
        finally:jobs.current_job.reset(token)
    def test_runtime_reader_deadline_kills_worker_without_replay(self):
        import assistant_runtime
        import sys
        with subprocess.Popen([sys.executable,'-c','import sys,json,time;json.load(sys.stdin);time.sleep(5)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True) as process:
            with self.assertRaises(HTTPException) as caught:assistant_runtime.communicate(process,{},.1)
            self.assertEqual(caught.exception.error_code,'runtime_timeout');self.assertIsNotNone(process.poll())
    @unittest.skipUnless(os.name=='posix','Real cross-process flock requires POSIX')
    def test_provider_admission_is_shared_across_processes(self):
        import assistant_runtime
        import hashlib
        import sys
        directory=self.root/'assistant/runtime-locks';directory.mkdir(parents=True)
        file=directory/(hashlib.sha256(b'provider').hexdigest()+'.lock')
        script="import sys,fcntl,time;f=open(sys.argv[1],'a+b');fcntl.flock(f,fcntl.LOCK_EX);print('ready',flush=True);time.sleep(5)"
        with subprocess.Popen([sys.executable,'-c',script,str(file)],stdout=subprocess.PIPE,text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(),'ready')
                with self.assertRaises(HTTPException):
                    with assistant_runtime.admission(api,'provider',wait_seconds=.1):self.fail('lock bypassed')
                with assistant_runtime.admission(api,'another',wait_seconds=.1):pass
            finally:process.terminate();process.wait(5)
    def test_option_defaults_off_and_rejects_non_boolean(self):
        self.assertFalse(self.payload.stream)
        with self.assertRaises(ValueError):control.Chat(prompt='test',stream='yes')
    def test_sse_disconnect_does_not_cancel_the_job_and_terminal_is_replayable(self):
        jobs.submit(api,self.payload)
        async def take(disconnected):
            request=types.SimpleNamespace(is_disconnected=lambda:async_value(disconnected))
            stream=jobs.events(api,self.payload.request_id,'panel',request)
            try:return await anext(stream)
            finally:await stream.aclose()
        self.assertIn('queued',asyncio.run(take(False)))
        with self.assertRaises(StopAsyncIteration):asyncio.run(take(True))
        jobs.update(api,self.payload.request_id,state='done',output={'result':{'answer':'ok'}})
        self.assertIn('done',asyncio.run(take(False)));self.assertEqual(self.launch.call_count,1)

async def async_value(value):return value

class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def response(self,chunks,done=True):
        async def lines():
            for chunk in chunks:
                yield 'data: '+json.dumps(chunk);yield ''
            if done:yield 'data: [DONE]';yield ''
        return types.SimpleNamespace(aiter_lines=lines)
    async def test_text_deltas_and_usage_assemble(self):
        response=self.response([{'choices':[{'delta':{'content':'При'}}]}, {'choices':[{'delta':{'content':'вет'},'finish_reason':'stop'}]}, {'choices':[],'usage':{'prompt_tokens':5,'completion_tokens':2}}])
        value=await runner.stream_message(response)
        self.assertEqual(value['content'],'Привет');self.assertEqual(value['_usage']['prompt_tokens'],5)
    async def test_tool_fragments_assemble_only_after_completion(self):
        value=await runner.stream_message(self.response([
          {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call','function':{'name':'read','arguments':'{"q":'}}]}}]},
          {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"v"}'}}]},'finish_reason':'tool_calls'}]}]))
        self.assertEqual(value['tool_calls'][0]['function'],{'name':'read','arguments':'{"q":"v"}'})
        with self.assertRaises(runner.ProviderFailure):await runner.stream_message(self.response([{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call','function':{'name':'write','arguments':'{'}}]}}]}],False))
    async def test_truncation_and_provider_errors_do_not_become_success(self):
        for chunks,done in [([{'choices':[{'delta':{'content':'partial'}}]}],False),([{'error':{'message':'private key'}}],True),([{'choices':[{'delta':{},'finish_reason':'length'}]}],True)]:
            with self.subTest(chunks=chunks),self.assertRaises(runner.ProviderFailure):await runner.stream_message(self.response(chunks,done))
    async def test_safe_diagnostics_distinguish_dns_tls_timeout_and_provider_http(self):
        import socket
        import ssl
        import httpx
        tls=httpx.ConnectError('private URL/key');tls.__cause__=ssl.SSLError('private certificate data')
        for error,code in [(socket.gaierror('private hostname'),'provider_dns_error'),(tls,'provider_tls_error'),
                           (httpx.ReadTimeout('private URL/key'),'provider_read_timeout'),
                           (runner.ProviderFailure('provider_http_503','AI-провайдер вернул HTTP 503'),'provider_http_503')]:
            self.assertEqual(runner.error_code(error),code);self.assertNotIn('private',runner.safe_error(error))
    async def test_nonstream_and_stream_use_explicit_timeouts_and_never_retry(self):
        import httpx
        settings={'provider_url':'https://provider.example/v1','api_key':'key','model':'m'}
        response=types.SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':'ok'}}]})
        class Client:
            calls=[]
            def __init__(self,**kwargs):self.kwargs=kwargs
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def post(self,*args,**kwargs):self.calls.append(kwargs);return response
        with patch.object(runner.httpx,'AsyncClient',Client),patch.object(asyncio.get_running_loop(),'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            self.assertEqual((await runner.provider_request(settings,[],[]))['content'],'ok')
        self.assertEqual(len(Client.calls),1);self.assertNotIn('stream',Client.calls[0]['json'])
        self.assertNotIn('private',runner.safe_error(httpx.ReadTimeout('private url key')))

if __name__=='__main__':unittest.main()
