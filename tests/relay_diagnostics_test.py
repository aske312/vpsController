"""Bounded, private diagnostics and admission explanations without changing traffic."""
import asyncio
from datetime import datetime, timezone
import errno
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'protocol-images/relay-agent'))
spec = importlib.util.spec_from_file_location('relay_diagnostics_runtime', ROOT/'protocol-images/relay-agent/agent.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class LogTests(unittest.TestCase):
    def test_opaque_route_reference_is_keyed_generation_specific_and_stable(self):
        config = {'id':'pro-'+'a'*32, 'transport':'tcp', 'listen_port':20001,
                  'target_ip':'8.8.8.8', 'target_port':443}
        key = bytes.fromhex('11'*32)
        ref = relay.route_reference(key, config)
        self.assertEqual(ref, relay.route_reference(key, dict(config)))
        self.assertRegex(ref, r'^[0-9a-f]{32}$')
        self.assertNotEqual(ref, relay.route_reference(bytes.fromhex('22'*32), config))
        self.assertNotEqual(ref, relay.route_reference(key, {**config, 'target_port':8443}))
        self.assertNotEqual(ref, relay.route_reference(key, {**config, 'auth':{'credential_id':'f'*32}}))
        token_key=relay.hashlib.sha256(('s'*48).encode()).digest()
        self.assertEqual(relay.route_reference(token_key, config),'52d37c98e7fec7a5e3a81478794ac7f1')

    def test_session_diagnostics_never_copy_network_identities_or_payload_and_are_bounded(self):
        row = {'route_ref':'a'*32, 'session_ref':'b'*32, 'transport':'tcp', 'phase':'transferring',
               'sent_bytes':123, 'client_write_buffer_bytes':456,
               'peer':'8.8.8.8', 'route_id':'pro-sensitive', 'profile':'customer', 'password':'secret',
               'payload':'private content', 'upstream_read_buffer_bytes':True}
        with tempfile.TemporaryDirectory() as folder:
            log = relay.DiagnosticLog(folder)
            self.assertTrue(log.write('memory_growth', {'rss_bytes':999}, sessions=[row]*100))
            raw = next(log.folder.iterdir()).read_text()
            for sentinel in ('8.8.8.8','pro-sensitive','customer','secret','private content'):
                self.assertNotIn(sentinel, raw)
            sessions = json.loads(raw)['sessions']
            self.assertEqual(len(sessions),16)
            self.assertEqual(sessions[0]['client_write_buffer_bytes'],456)
            self.assertNotIn('upstream_read_buffer_bytes',sessions[0])
            self.assertEqual(relay.private_sessions([{**row,'session_ref':'ip=8.8.8.8'}]),[])

    def test_daily_rotation_and_expiry_bound_all_history_and_preserve_other_files(self):
        now = [datetime(2026, 11, 15, 12, tzinfo=timezone.utc).timestamp()]
        with tempfile.TemporaryDirectory() as folder:
            log = relay.DiagnosticLog(folder, clock=lambda:now[0], max_bytes=512)
            for day in range(10):
                now[0] = datetime(2026, 11, 15+day, 12, tzinfo=timezone.utc).timestamp()
                for index in range(40):
                    self.assertTrue(log.write('snapshot', {'rss_bytes':index}))
                files = list(log.folder.iterdir())
                self.assertLessEqual(len(files), 14)
                self.assertTrue(all(p.stat().st_size <= 512 for p in files))
                self.assertLessEqual(sum(p.stat().st_size for p in files), 7*2*512)
            sentinel = log.folder/'unrelated.json'
            sentinel.write_text('preserve')
            old = log.folder/'relay-2000-01-01.jsonl'
            old.write_text('old record with a fresh filesystem timestamp')
            now[0] += 8*86400
            log.prune()  # Same operation used by the timer; does not require a running Agent.
            self.assertEqual(list(log.folder.iterdir()), [sentinel])

    def test_journal_schema_excludes_credentials_addresses_and_untrusted_text(self):
        with tempfile.TemporaryDirectory() as folder:
            log = relay.DiagnosticLog(folder)
            self.assertTrue(log.write('admission_rejected', {'rss_bytes':123, 'password':'secret', 'pid':'secret', 'peer':'8.8.8.8'},
                                      {'live_memory':2, 'secret':99}))
            self.assertFalse(log.write('token=secret', {}))
            path = next(log.folder.iterdir())
            raw = path.read_text()
            self.assertNotIn('secret', raw); self.assertNotIn('8.8.8.8', raw)
            row = json.loads(raw)
            self.assertEqual(row['rss_bytes'],123)
            self.assertEqual(row['rejections'],{'live_memory':2})
            if os.name=='posix':
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertEqual(log.folder.stat().st_mode & 0o777,0o700)

    def test_disk_full_is_bounded_and_does_not_escape_to_the_relay(self):
        with tempfile.TemporaryDirectory() as folder:
            log = relay.DiagnosticLog(folder)
            with patch.object(relay.os,'open',side_effect=OSError(errno.ENOSPC,'full')):
                self.assertFalse(log.write('snapshot',{'rss_bytes':123}))
            self.assertEqual(log.failures,1)
            self.assertTrue(log.write('snapshot',{'log_write_failures':log.failures}))

    def test_symlinks_are_not_followed_by_writes_or_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);outside=root/'preserve';outside.write_text('original')
            log=relay.DiagnosticLog(root/'state');log.folder.mkdir(parents=True)
            day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
            try:(log.folder/f'relay-{day}.jsonl').symlink_to(outside)
            except OSError:self.skipTest('Symlinks unavailable')
            self.assertFalse(log.write('snapshot',{}));log.prune()
            self.assertEqual(outside.read_text(),'original')

    def test_numeric_sampler_handles_cgroup_layouts_and_missing_processes(self):
        for unified in (True,False):
            with self.subTest(unified=unified),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);proc=root/'proc';groups=root/'groups'
                (proc/'self/fd').mkdir(parents=True);(proc/'123').mkdir()
                (proc/'self/status').write_text('VmRSS: 100 kB\nVmHWM: 200 kB\nThreads: 3\n')
                (proc/'123/status').write_text('VmRSS: 50 kB\nVmHWM: 80 kB\n')
                (proc/'self/environ').write_text('secret');(proc/'self/cmdline').write_text('secret')
                (proc/'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 500 kB\n')
                (proc/'net').mkdir()
                (proc/'net/snmp').write_text('Udp: InErrors RcvbufErrors SndbufErrors\nUdp: 9 7 2\n')
                (proc/'self/cgroup').write_text('0::/parent/agent\n' if unified else '2:memory:/parent/agent\n')
                group=groups/('parent/agent' if unified else 'memory/parent/agent');group.mkdir(parents=True)
                names=('memory.current','memory.peak','memory.max') if unified else ('memory.usage_in_bytes','memory.max_usage_in_bytes','memory.limit_in_bytes')
                for name,value in zip(names,('1234','5678','9999')):(group/name).write_text(value)
                result=relay.diagnostic_memory([123,999],proc,groups)
                self.assertEqual((result['rss_bytes'],result['child_rss_bytes']),(102400,51200))
                self.assertEqual(result['cgroup_current_bytes'],1234)
                self.assertEqual(result['cgroup_peak_bytes'],5678)
                self.assertEqual(result['host_available_bytes'],512000)
                self.assertEqual(result['host_udp_receive_errors'],7)
                self.assertEqual(result['host_udp_send_errors'],2)
                self.assertNotIn('secret',json.dumps(result))


class AdmissionTests(unittest.TestCase):
    def test_distinct_rejection_reasons_preserve_live_slots(self):
        for reason in ('route_sessions','shared_sessions','live_memory','memory_budget'):
            with self.subTest(reason=reason):
                buffers=relay.UDPReceiveBudget(1048576)
                room=[None]
                pressure=relay.MemoryPressure(lambda:room[0],reclaim=lambda:None)
                budget=relay.SessionBudget(1 if reason=='shared_sessions' else 10,1048576,buffers,pressure)
                route=relay.RelayRoute({'transport':'tcp'},limits={'max_sessions_per_route':1 if reason=='route_sessions' else 10},session_budget=budget)
                first=route.admit();self.assertIsNotNone(first)
                if reason=='live_memory':pressure.remaining=0;pressure.sampled_at=pressure.clock();room[0]=0
                if reason=='memory_budget':budget.memory_limit=512*1024
                self.assertIsNone(route.admit());self.assertEqual(budget.rejections,{reason:1})
                self.assertIn(first,route.slots);self.assertEqual(budget.used,1)
                first.release();self.assertEqual(budget.used,0)

    def test_udp_allocation_reports_live_memory_and_buffer_budget_separately(self):
        for reason in ('udp_memory_budget','udp_live_memory'):
            with self.subTest(reason=reason),socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                buffers=relay.UDPReceiveBudget(0 if reason=='udp_memory_budget' else 4*1048576)
                budget=relay.SessionBudget(10)
                buffers.record_rejection=budget.reject
                if reason=='udp_live_memory':buffers.pressure=relay.MemoryPressure(lambda:0,reclaim=lambda:None)
                with self.assertRaises(OSError):buffers.reserve(sock)
                self.assertEqual(budget.rejections,{reason:1})
                self.assertEqual(buffers.used,0)


class ReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_reference_survives_samples_and_close_without_retaining_streams(self):
        route=relay.RelayRoute({'transport':'tcp','id':'private-route'})
        route.diagnostic_ref='a'*32
        with tempfile.TemporaryDirectory() as folder:
            reporter=relay.RelayDiagnostics(folder)
            route.diagnostic_closed=reporter.closed
            first=route.admit();second=route.admit()
            self.assertNotEqual(first.reference,second.reference)
            writer=SimpleNamespace(transport=SimpleNamespace(get_write_buffer_size=lambda:12345))
            first.streams=(SimpleNamespace(_buffer=bytearray(b'secret')),writer,None,None)
            first.phase='transferring';first.sent=900
            before=first.diagnostic()
            self.assertEqual(before['client_read_buffer_bytes'],6)
            self.assertEqual(before['client_write_buffer_bytes'],12345)
            first.release();first.release()
            self.assertIsNone(first.streams)
            self.assertEqual(len(reporter.closed_sessions),1)
            after=reporter.closed_sessions[0]
            self.assertEqual(before['session_ref'],after['session_ref'])
            self.assertEqual(after['phase'],'closed')
            self.assertEqual(after['sent_bytes'],900)
            self.assertEqual(after['route_ref'],route.status()['diagnostics']['route_ref'])
            self.assertEqual(route.status()['diagnostics']['session_refs'],[second.reference])
            second.release()

    async def test_diagnostic_failure_keeps_memory_guard_and_existing_admissions_alive(self):
        room=[64*1048576];collected=[]
        pressure=relay.MemoryPressure(lambda:room[0],reclaim=lambda:collected.append(True))
        budget=relay.SessionBudget(10,pressure=pressure)
        route=relay.RelayRoute({'transport':'tcp'},session_budget=budget)
        first=route.admit()
        with tempfile.TemporaryDirectory() as folder:
            def broken_sample(children):raise OSError('metrics unavailable')
            diagnostics=relay.RelayDiagnostics(folder,sample=broken_sample)
            agent=SimpleNamespace(session_budget=budget,diagnostics=diagnostics,private_transport=SimpleNamespace(processes={}))
            room[0]=0;pressure.sampled_at=None
            task=asyncio.create_task(relay.Agent.watch_memory(agent))
            try:
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertEqual(collected,[True])
                self.assertEqual(diagnostics.log.failures,1)
                self.assertIn(first,route.slots)
                room[0]=None;pressure.sampled_at=None
                second=route.admit();self.assertIsNotNone(second)
                second.release()
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True);first.release()

    async def test_snapshots_growth_reclamation_and_aggregated_rejections_are_throttled(self):
        now=[0.0];rss=[20*1048576]
        with tempfile.TemporaryDirectory() as folder:
            budget=relay.SessionBudget(10,pressure=relay.MemoryPressure(lambda:None))
            buffers=relay.UDPReceiveBudget(1048576)
            agent=SimpleNamespace(session_budget=budget,buffer_budget=buffers,routes={},http_connections=0,
                                  private_transport=SimpleNamespace(processes={}))
            sample=lambda children:{'rss_bytes':rss[0],'rss_peak_bytes':rss[0]}
            log=relay.DiagnosticLog(folder,clock=lambda:1764000000+now[0])
            reporter=relay.RelayDiagnostics(folder,clock=lambda:now[0],sample=sample,log=log)
            def rows():return [json.loads(line) for p in log.folder.glob('relay-*.jsonl*') for line in p.read_text().splitlines()]
            reporter.tick(agent);self.assertEqual([r['event'] for r in rows()],['snapshot'])
            for _ in range(1000):budget.reject('live_memory')
            now[0]=5;rss[0]+=70*1048576;reporter.tick(agent)
            self.assertEqual(next(r['rejections'] for r in rows() if r['event']=='admission_rejected'),{'live_memory':1000})
            self.assertIn('memory_growth',[r['event'] for r in rows()])
            for second in (6,10,15):
                now[0]=second;budget.reject('live_memory');reporter.tick(agent)
            self.assertEqual(sum(r['event']=='admission_rejected' for r in rows()),1)
            now[0]=20;reporter.tick(agent)
            self.assertEqual(sum(r['event']=='admission_rejected' for r in rows()),2)
            budget.pressure.reclaims=1;budget.pressure.reclaim_before=0;budget.pressure.reclaim_after=123
            now[0]=25;rss[0]=10*1048576;reporter.tick(agent)
            self.assertIn('memory_recovery',[r['event'] for r in rows()])
            self.assertEqual(next(r['reclaim_before_bytes'] for r in rows() if r['event']=='memory_reclaimed'),0)
            now[0]=60;reporter.tick(agent)
            self.assertEqual(sum(r['event']=='snapshot' for r in rows()),2)


class ForensicTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_retained_allocation_is_attributed_to_code_without_contents(self):
        profiler=relay.MemoryForensics()
        profiler.start()
        namespace={}
        # A real allocation attributed to a runtime code location, with no local dump.
        exec(compile('def allocate():\n return bytearray(b"sensitive-payload" * 100000)\n',str(ROOT/'protocol-images/relay-agent/agent.py'),'exec'),namespace)
        retained=namespace['allocate']()
        try:
            metrics={'rss_bytes':40*1048576,'host_available_bytes':512*1048576}
            detail=profiler.collect(metrics,[])
            sites=detail['heap_sites']
            self.assertTrue(any(site['size_bytes']>=len(retained) and {'file':'agent.py','line':2} in site['frames'] for site in sites))
            self.assertGreater(metrics['python_traced_bytes'],len(retained))
            self.assertNotIn('sensitive-payload',json.dumps(detail))
            self.assertEqual(detail['heap_skip_reason'],0)
        finally: relay.tracemalloc.stop()

    async def test_low_headroom_and_trace_cap_skip_allocating_snapshots_and_restart_later(self):
        now=[0.0];profiler=relay.MemoryForensics(clock=lambda:now[0])
        profiler.start()
        try:
            with patch.object(relay.tracemalloc,'take_snapshot',side_effect=AssertionError('unsafe snapshot')):
                detail=profiler.collect({'rss_bytes':100,'host_available_bytes':1024},[])
            self.assertEqual(detail['heap_skip_reason'],1)
            now[0]=5
            with patch.object(relay.tracemalloc,'get_tracemalloc_memory',return_value=profiler.TRACE_LIMIT+1):
                detail=profiler.collect({'rss_bytes':100,'host_available_bytes':512*1048576},[])
            self.assertEqual(detail['trace_state'],2)
            self.assertFalse(relay.tracemalloc.is_tracing())
            now[0]=306
            detail=profiler.collect({'rss_bytes':100,'host_available_bytes':512*1048576},[])
            self.assertTrue(relay.tracemalloc.is_tracing())
            self.assertEqual(detail['trace_state'],1)
        finally: relay.tracemalloc.stop()

    async def test_stuck_task_and_scheduled_callbacks_are_bounded_and_correlated(self):
        event=asyncio.Event()
        namespace={'event':event}
        exec(compile('async def stuck():\n await event.wait()\ndef callback():\n pass\n',str(ROOT/'protocol-images/relay-agent/agent.py'),'exec'),namespace)
        task=asyncio.create_task(namespace['stuck'](),name='secret-task-name')
        handles=[asyncio.get_running_loop().call_later(300,namespace['callback']) for _ in range(500)]
        route=relay.RelayRoute({'transport':'tcp'});route.diagnostic_ref='a'*32
        slot=route.admit();slot.tasks=(task,)
        try:
            await asyncio.sleep(0)
            metrics={'rss_bytes':40*1048576}
            detail=relay.MemoryForensics().collect(metrics,[route])
            self.assertGreaterEqual(metrics['loop_scheduled_handles'],500)
            self.assertTrue(any(row['count']>=500 for row in detail['scheduled_callbacks']))
            self.assertTrue(any({'file':'agent.py','line':2} in row['frames'] and
                                {'route_ref':'a'*32,'session_ref':slot.reference} in row['sessions'] for row in detail['tasks']))
            self.assertLessEqual(len(detail['tasks']),16)
            self.assertNotIn('secret-task-name',json.dumps(detail))
        finally:
            for handle in handles:handle.cancel()
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            slot.release();self.assertIsNone(slot.tasks)

    async def test_tls_bio_and_backlog_are_observed_without_copying_payload(self):
        route=relay.RelayRoute({'transport':'tcp'});slot=route.admit()
        protocol=SimpleNamespace(_incoming=SimpleNamespace(pending=123),_outgoing=SimpleNamespace(pending=456),
                                 _write_buffer_size=789,_write_backlog=[b'secret']*4)
        transport=SimpleNamespace(_ssl_protocol=protocol,_paused=True,is_closing=lambda:False,get_write_buffer_size=lambda:0)
        slot.streams=(None,SimpleNamespace(transport=transport),None,None)
        try:
            sample=slot.diagnostic()
            self.assertEqual(sample['client_tls_incoming_bytes'],123)
            self.assertEqual(sample['client_tls_outgoing_bytes'],456)
            self.assertEqual(sample['client_tls_backlog_bytes'],789)
            self.assertEqual(sample['client_tls_backlog_items'],4)
            self.assertEqual(sample['client_transport_read_paused'],1)
            self.assertNotIn('secret',json.dumps(sample))
        finally:slot.release()

    async def test_nested_allowlist_and_separate_rotation_preserve_resource_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            normal=relay.DiagnosticLog(folder,max_bytes=2048)
            detail_log=relay.ForensicLog(folder,max_bytes=2048)
            normal.write('snapshot',{'rss_bytes':123})
            original=next(normal.folder.glob('relay-*')).read_bytes()
            detail={'password':'secret','heap_sites':[{'frames':[{'file':'/private/secret.py','line':1,'locals':'secret'},
                    {'file':'agent.py','line':222}], 'size_bytes':999,'payload':'secret'}]*100,
                    'tasks':[{'frames':[],'count':1,'sessions':[{'route_ref':'secret','session_ref':'secret'}]}]}
            for _ in range(20):self.assertTrue(detail_log.write('memory_detail',{},detail=detail))
            self.assertEqual(next(normal.folder.glob('relay-*')).read_bytes(),original)
            files=list(normal.folder.glob('memory-*'))
            self.assertLessEqual(len(files),2)
            for file in files:
                self.assertLessEqual(file.stat().st_size,2048)
                raw=file.read_text();self.assertNotIn('secret',raw);self.assertNotIn('/private/',raw)
            detail_log.clock=lambda:datetime(2099,1,1,tzinfo=timezone.utc).timestamp()
            detail_log.prune()
            self.assertFalse(list(normal.folder.glob('memory-*')))
            self.assertEqual(next(normal.folder.glob('relay-*')).read_bytes(),original)

    async def test_heap_detail_failure_still_records_resource_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            budget=relay.SessionBudget(10,pressure=relay.MemoryPressure(lambda:None))
            agent=SimpleNamespace(session_budget=budget,buffer_budget=relay.UDPReceiveBudget(1048576),routes={},
                                  http_connections=0,private_transport=SimpleNamespace(processes={}))
            reporter=relay.RelayDiagnostics(folder,sample=lambda _: {'rss_bytes':123})
            with patch.object(reporter.forensics,'collect',side_effect=MemoryError):reporter.tick(agent)
            rows=[json.loads(line) for p in reporter.log.folder.glob('relay-*') for line in p.read_text().splitlines()]
            self.assertEqual(rows[0]['event'],'snapshot')
            self.assertEqual(reporter.log.failures,1)


if __name__=='__main__':unittest.main()
