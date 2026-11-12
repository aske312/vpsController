import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import types
import copy
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.api.support import api, manager
import application_operation as operations
import diagnostic_context as tracing
import observability
import diagnostic_tools as diagnostic
import assistant_mihomo
import assistant_control
import diagnostic_probes


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'ADMIN_PASSWORD': 'fixture-private-password'}))

    def test_cross_service_context_links_child_and_rejects_unsigned_trace(self):
        child = FastAPI()
        child.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='mihomo')
        @child.get('/child')
        def child_route(): return tracing.snapshot()
        parent = FastAPI()
        parent.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='panel')
        with TestClient(child) as downstream:
            @parent.get('/parent')
            def parent_route(): return downstream.get('/child', headers=tracing.outgoing()).json()
            with TestClient(parent) as client:
                response = client.get('/parent', headers={'X-VPS-Trace-ID': 'f' * 32, 'X-Trace-ID': 'f' * 32})
                again = client.get('/parent')
        rows = observability.Store(self.root).read()['events']
        trace = response.headers['x-trace-id']
        self.assertNotEqual(trace, 'f' * 32)
        self.assertNotEqual(again.headers['x-trace-id'], trace)
        linked = [row for row in rows if row['trace_id'] == trace]
        self.assertEqual(len(linked), 2)
        first = next(row for row in linked if row['service'] == 'panel')
        second = next(row for row in linked if row['service'] == 'mihomo')
        self.assertEqual(second['parent_request_id'], first['request_id'])
        self.assertNotEqual(second['request_id'], first['request_id'])
        self.assertNotIn('fixture-private-password', json.dumps(rows))

    def test_proofs_expire_and_cannot_be_modified(self):
        token = tracing.current.set({'trace_id': 'a' * 32, 'request_id': 'b' * 32})
        try: headers = tracing.outgoing()
        finally: tracing.current.reset(token)
        self.assertEqual(tracing.incoming(headers)['trace_id'], 'a' * 32)
        with patch.object(tracing.time, 'time', return_value=time.time() + 121):
            self.assertNotEqual(tracing.incoming(headers)['trace_id'], 'a' * 32)
        headers['X-VPS-Parent-ID'] = 'c' * 32
        self.assertNotEqual(tracing.incoming(headers)['trace_id'], 'a' * 32)
        headers['X-VPS-Trace-At'] = '²'
        headers['X-VPS-Trace-Proof'] = 'é'
        self.assertNotEqual(tracing.incoming(headers)['trace_id'], 'a' * 32)

    def test_operation_retains_trace_in_record_worker_environment_and_completion(self):
        token = tracing.current.set({'trace_id': 'a' * 32, 'request_id': 'b' * 32})
        try:
            launch = self.enterContext(patch.object(operations.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)))
            result = operations.start(self.root, self.root / 'action.json', 'restart', ['fixed-command'], launch)
        finally: tracing.current.reset(token)
        self.assertEqual(result['trace_id'], 'a' * 32)
        self.assertIn('--setenv=VPS_CONTROL_TRACE_ID=' + 'a' * 32, launch.call_args.args[0])
        self.assertIn('--setenv=VPS_CONTROL_PARENT_REQUEST_ID=' + 'b' * 32, launch.call_args.args[0])
        final = operations.write_status(self.root, self.root / 'action.json', 'restart', 'succeeded', 100, 'done', result['started_at'], result['id'])
        self.assertEqual(final['trace_id'], 'a' * 32)

    def test_old_database_migrates_without_losing_events_or_settings(self):
        store = observability.Store(self.root)
        store.directory.mkdir()
        with sqlite3.connect(store.path) as db:
            db.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, at REAL, request_id TEXT, service TEXT, route TEXT, method TEXT, status INTEGER, duration_ms REAL, reason TEXT, profile_id TEXT, kind TEXT)')
            db.execute('INSERT INTO events VALUES (1,?,?,?,?,?,?,?,?,?,?)', (time.time(), 'a' * 32, 'panel', '/old', 'GET', 500, 1, 'server_error', None, 'request'))
        db.close()
        result = store.read()
        self.assertEqual(result['summary']['requests'], 1)
        self.assertIsNone(result['events'][0]['trace_id'])

    def test_command_failure_has_machine_readable_cause_and_no_stderr(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        app = FastAPI()
        app.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='panel')
        app.add_exception_handler(HTTPException, api.public_http_exception_handler)
        @app.get('/timeout')
        def timeout_route(): return api.run('fixed-command', check=True)
        with patch.object(api.subprocess, 'run', side_effect=api.subprocess.TimeoutExpired('private-command', 1)), TestClient(app) as client:
            result = client.get('/timeout')
        self.assertEqual(result.status_code, 504)
        self.assertEqual(result.json()['error_code'], 'command_timeout')
        self.assertEqual(result.json()['stage'], 'execution')
        self.assertTrue(result.json()['retryable'])
        self.assertNotIn('private-command', result.text)
        self.assertEqual(observability.Store(self.root).read()['events'][0]['error_code'], 'command_timeout')

    def test_change_audit_keeps_field_names_and_distinguishes_admission_from_completion(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        store = observability.Store(self.root)
        store.configure(observability.Settings(**{**observability.DEFAULTS, 'errors_only': True}))
        app = FastAPI()
        app.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='panel')
        @app.patch('/api/mihomo/profiles/{profile_id}', status_code=202)
        def update_profile(body: dict): return {'state': 'queued'}
        with TestClient(app) as client:
            response = client.patch('/api/mihomo/profiles/one', json={'name': 'private-person', 'password': 'private-key', 'routing': {'mode': 'proxy', 'private_domain': 'private-domain'}})
        changes = diagnostic.changes(api, diagnostic.ChangesQuery())
        self.assertEqual(changes['observations'][0]['data']['status'], 202)
        self.assertEqual(changes['observations'][0]['data']['changed_fields'], ['name', 'routing', 'routing.mode'])
        self.assertEqual(changes['observations'][0]['data']['trace_id'], response.headers['x-trace-id'])
        self.assertEqual(changes['observations'][0]['data']['target_profile_id'], 'one')
        self.assertTrue(diagnostic.changes(api, diagnostic.ChangesQuery(profile_id='one'))['observations'])
        self.assertFalse(diagnostic.changes(api, diagnostic.ChangesQuery(profile_id='other'))['observations'])
        for secret in ('private-person', 'private-key', 'private-domain', 'password'):
            self.assertNotIn(secret, json.dumps(changes))

    def test_incident_separates_confirmed_failure_correlation_and_unknown_sources(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        now = time.time()
        store = observability.Store(self.root)
        base = {'at': now, 'request_id': 'a' * 32, 'trace_id': 'b' * 32, 'service': 'mihomo', 'route': '/subscription', 'method': 'GET', 'status': 500, 'kind': 'subscription'}
        store.append(base)
        store.append({**base, 'at': now - 10, 'kind': 'change', 'method': 'PATCH', 'status': 202})
        with patch.object(api, 'managed_services', return_value={'api': {'unit': 'fixture-api.service'}}), \
             patch.object(api, 'observe_service', return_value={'runtime': {'state': 'unknown'}}), \
             patch.object(diagnostic, 'prime_services'), \
             patch.object(diagnostic.observability_control, 'connections', return_value={'available': False, 'established_connections': None}):
            result = diagnostic.incident(api, diagnostic.IncidentQuery(request_id='a' * 32))
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['hypotheses'][0]['causation'], 'not_proven')
        self.assertEqual(result['coverage']['trace_id'], 'b' * 32)
        self.assertFalse(any('service_currently' in item['claim'] for item in result['confirmed']))
        self.assertIn('client_network', {item['source'] for item in result['missing_data']})
        self.assertIn('connections', {item['source'] for item in result['missing_data']})

    def test_compare_exposes_differences_without_credentials_or_invented_counters(self):
        profiles = [
            {'id': 'left', 'revision': 'a' * 64, 'routing': {'dns_secure': False}, 'connections': [
                {'id': 'conn', 'component': 'transport-wg', 'settings': {'mtu': 1280}, 'credential': {'private_key': 'private-key'}}]},
            {'id': 'right', 'revision': 'b' * 64, 'routing': {'dns_secure': True}, 'connections': [
                {'id': 'conn', 'component': 'transport-reality', 'credential': {'password': 'private-password', 'subscription_token': 'private-token'}}]},
        ]
        stats = {'items': [{'id': 'left', 'connections': {'conn': {'active': True, 'active_connections': 2}}},
                           {'id': 'right', 'connections': {'conn': {'active': None, 'active_connections': None}}}]}
        def request(method, path, **_): return stats if path == '/stats' else {'items': copy.deepcopy(profiles)}
        with patch.object(assistant_mihomo, 'request', side_effect=request):
            result = diagnostic.compare(api, diagnostic.CompareQuery(left_profile_id='left', right_profile_id='right'))
        self.assertTrue(result['confirmed'])
        self.assertTrue(result['coverage']['configuration_unchanged'])
        self.assertIn('routing.dns_secure', [item['field'] for obs in result['observations'] if obs['id'] == 'differences' for item in obs['data']])
        unknown = next(obs['data'] for obs in result['observations'] if obs['id'] == 'right_telemetry')
        self.assertIsNone(unknown[0]['active_connections'])
        for secret in ('private-key', 'private-password', 'private-token'):
            self.assertNotIn(secret, json.dumps(result))

    def test_compare_cannot_confirm_differences_across_a_configuration_change(self):
        profiles = [{'id': 'left', 'revision': 'a' * 64, 'connections': []}, {'id': 'right', 'revision': 'b' * 64, 'connections': []}]
        changed = copy.deepcopy(profiles)
        changed[0]['revision'] = 'c' * 64
        with patch.object(assistant_mihomo, 'request', side_effect=[{'items': profiles}, {'items': []}, {'items': changed}]):
            result = diagnostic.compare(api, diagnostic.CompareQuery(left_profile_id='left', right_profile_id='right'))
        self.assertFalse(result['confirmed'])
        self.assertFalse(result['coverage']['configuration_unchanged'])
        self.assertEqual(next(row for row in result['observations'] if row['id'] == 'left_telemetry')['state'], 'unknown')

    def test_all_tools_work_through_HTTP_MCP_contract_and_enforce_actor_permissions(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        profiles = [{'id': 'one', 'revision': 'a' * 64, 'connections': []}, {'id': 'two', 'revision': 'b' * 64, 'connections': []}]
        def bridge(method, path, **_): return {'items': [] if path == '/stats' else profiles}
        api.app.dependency_overrides[api.require_assistant_control] = lambda: None
        api.app.dependency_overrides[api.require_token] = lambda: None
        diagnostic_probes._recent.clear()
        try:
            with patch.object(assistant_control, 'read', return_value={'accounts': [], 'mcp': {'vps-controller-mcp': True, 'mihomo-mcp': True}}), \
                 patch.object(api, 'managed_services', return_value={'api': {'unit': 'fixed.service'}}), \
                 patch.object(api, 'observe_service', return_value={'runtime': {'state': 'unknown'}}), \
                 patch.object(diagnostic, 'prime_services'), patch.object(assistant_mihomo, 'request', side_effect=bridge), TestClient(api.app) as client:
                cases = [('vps_changes', {}), ('vps_incident', {'include_connections': False}),
                         ('vps_probe', {'kind': 'service', 'service': 'api'}),
                         ('vps_compare', {'left_profile_id': 'one', 'right_profile_id': 'two'})]
                for name, body in cases:
                    response = client.post('/api/assistant/control-tool', headers={'X-MCP-Actor': 'panel'}, json={'name': name, 'body': body})
                    self.assertEqual(response.status_code, 200, response.text)
                    value = response.json()
                    self.assertEqual(value['tool'], name)
                    for field in ('observations', 'confirmed', 'hypotheses', 'missing_data', 'next_steps', 'coverage'):
                        self.assertIn(field, value)
                    denied = client.post('/api/assistant/control-tool', headers={'X-MCP-Actor': '42'}, json={'name': name, 'body': body})
                    self.assertEqual(denied.status_code, 403, denied.text)
                invalid = client.post('/api/assistant/control-tool', headers={'X-MCP-Actor': 'panel'},
                                      json={'name': 'vps_probe', 'body': {'kind': 'tls', 'url': 'http://169.254.169.254'}})
                self.assertEqual(invalid.status_code, 422)
                invalid_name = client.post('/api/assistant/control-tool', headers={'X-MCP-Actor': 'panel'}, json={'name': 'private-invalid-input'})
                self.assertEqual(invalid_name.status_code, 422)
                self.assertEqual(invalid_name.json()['error_code'], 'invalid_request')
                self.assertNotIn('private-invalid-input', invalid_name.text)
                direct = client.post('/api/observability/diagnose/vps_changes', json={})
                self.assertEqual(direct.status_code, 200)
                self.assertTrue(observability.Store(self.root).probes())
        finally: api.app.dependency_overrides.clear()
