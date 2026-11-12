import json
import http.server
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.api.support import api
import application_diagnostics as diagnostics
import application_operation as operations
import assistant_control as control
import diagnostic_context as tracing
import observability
import operation_log
import service_state


class ApplicationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = {'accounts': [], 'providers': [{'id': 'a' * 32, 'provider_url': 'https://provider.example/v1', 'api_key': 'fixture-secret'}]}
        self.enterContext(patch.object(control, 'read', return_value=self.settings))
        self.env = types.SimpleNamespace(DATA_DIR=self.root,
            managed_services=lambda: {'api': {'unit': 'api.service'}},
            observe_service=lambda unit: {'state': 'active', 'runtime': {'state': 'running'}, 'service_type': 'simple'},
            installed_build_commit=lambda: 'b' * 40, expected_application_branch=lambda: 'test-pro', dns_provider_list=lambda: [])

    def event(self, identity=1, **extra):
        return {'at': time.time(), 'request_id': f'{identity:032x}', 'service': 'panel', 'route': '/fixed', 'method': 'GET',
                'status': 500, 'duration_ms': 1, 'reason': 'server_error', 'kind': 'request', **extra}

    def test_summary_is_passive_bounded_and_marks_functional_health_unknown(self):
        with patch.object(diagnostics, 'prime_services'), patch.object(diagnostics, 'integration_probe') as probe:
            result = diagnostics.summary(self.env, diagnostics.SummaryQuery())
        self.assertEqual(result['tool'], 'application_diagnostics')
        self.assertEqual(result['coverage']['mode'], 'passive')
        self.assertEqual(result['missing_data'][-1]['source'], 'functional_health')
        probe.assert_not_called()
        text = json.dumps(result)
        self.assertNotIn('fixture-secret', text)
        self.assertNotIn('provider.example', text)
        self.assertEqual(next(row for row in result['observations'] if row['id'] == 'integrations')['data'][0]['id'], 'ai:' + 'a' * 32)

    def test_timeline_keeps_parent_result_child_stages_and_http_without_replaying(self):
        identity = 'c' * 32
        record = {'id': identity, 'action': 'restart', 'state': 'failed', 'started_at': operations.timestamp()}
        operations.atomic_json(self.root / 'operations' / (identity + '.json'), record)
        operation_log.append(self.root, {**record, 'stage': 'health', 'result': 'failed', 'component': 'api'})
        operations.atomic_json(self.root / 'mihomo/operations' / (identity + '.json'), {**record, 'state': 'succeeded'})
        operation_log.append(self.root / 'mihomo', {**record, 'stage': 'apply', 'result': 'passed'})
        observability.Store(self.root).append(self.event(operation_id=identity))
        result = diagnostics.timeline(self.env, diagnostics.TimelineQuery(operation_id=identity))
        parent = next(row for row in result['observations'] if row['id'] == 'operation:system')
        self.assertEqual(parent['data']['state'], 'failed')
        self.assertTrue(any(row['data'].get('stage') == 'apply' for row in result['timeline']))
        self.assertTrue(any(row['source'] == 'request_history' for row in result['timeline']))
        self.assertEqual([row['at'] for row in result['timeline']], sorted(row['at'] for row in result['timeline']))

    def test_missing_operation_is_not_success(self):
        result = diagnostics.timeline(self.env, diagnostics.TimelineQuery(operation_id='f' * 32))
        self.assertTrue(any(row['reason'] == 'not_found_or_expired' for row in result['missing_data']))
        self.assertEqual(result['confirmed'], [])

    def test_retention_tracks_capacity_expiry_and_filter_coverage(self):
        store = observability.Store(self.root)
        store.configure(observability.Settings(**{**observability.DEFAULTS, 'max_events': 100}))
        for index in range(102): store.append(self.event(index, error_code='timeout', stage='upstream'))
        store.append(self.event(103, at=time.time() - 30 * 86400))
        result = store.read(error_code='timeout', stage='upstream', limit=5)
        self.assertEqual(result['summary']['requests'], 100)
        self.assertTrue(result['storage_coverage']['truncated'])
        self.assertEqual(result['storage_coverage']['capacity'], 2)
        self.assertEqual(result['storage_coverage']['expired'], 1)
        self.assertEqual(store.read(error_code='other')['events'], [])
        store.clear()
        self.assertEqual(store.read()['storage_coverage']['capacity'], 0)

    def test_switching_recording_modes_resets_coverage_without_fabricating_lost_counts(self):
        store = observability.Store(self.root)
        with patch.object(observability.time, 'time', return_value=1000):
            first = store.read()['storage_coverage']
        with patch.object(observability.time, 'time', return_value=2000):
            store.configure(observability.Settings(**{**observability.DEFAULTS, 'errors_only': True}))
        with patch.object(observability.time, 'time', return_value=3000):
            store.configure(observability.Settings(**{**observability.DEFAULTS, 'revision': 1}))
            current = store.read()['storage_coverage']
        self.assertEqual(first['coverage_since'], 1000)
        self.assertEqual(current['coverage_since'], 3000)
        self.assertEqual(current['tracked_since'], 1000)
        self.assertEqual(current['capacity'], 0)

    def test_HTTP_component_and_operation_type_use_route_templates_and_are_filterable(self):
        app = FastAPI()
        app.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='panel')
        @app.post('/api/assistant/models')
        def catalog(): return {'models': ['one']}
        @app.get('/api/assistant/conversations/{identity}')
        def conversation(identity: str): return {}
        with TestClient(app) as client:
            client.post('/api/assistant/models')
            client.get('/api/assistant/conversations/private-person')
        store = observability.Store(self.root)
        result = store.read(component='nodera', operation_type='catalog')
        self.assertEqual(result['summary']['requests'], 1)
        self.assertEqual(result['events'][0]['route'], '/api/assistant/models')
        self.assertEqual(store.read(component='mihomo')['events'], [])
        self.assertNotIn('private-person', json.dumps(store.read()))

    def probe(self, mode='api', addresses='93.184.216.34 STREAM provider.example\n', status=200, payload=None):
        response = MagicMock(status=status)
        response.read.return_value = json.dumps({'data': [{'id': 'model'}]} if payload is None else payload).encode()
        connection = MagicMock()
        connection.getresponse.return_value = response
        self.enterContext(patch.object(diagnostics.subprocess, 'run', return_value=types.SimpleNamespace(stdout=addresses)))
        self.enterContext(patch.object(diagnostics.socket, 'create_connection', return_value=MagicMock()))
        self.enterContext(patch.object(diagnostics.ssl, 'create_default_context', return_value=MagicMock()))
        self.enterContext(patch.object(diagnostics.http.client, 'HTTPSConnection', return_value=connection))
        result = diagnostics.measure_provider(self.settings['providers'][0], mode)
        return result, result['observations'][0]['data'], connection

    def test_integration_reports_layers_without_leaking_secrets_or_claiming_function(self):
        result, stages, connection = self.probe()
        self.assertTrue(all(row['state'] == 'passed' for row in stages.values()))
        self.assertEqual(result['coverage']['functional_operation'], 'not_checked')
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertNotIn('provider.example', json.dumps(result))
        self.assertEqual(connection.request.call_args.args[:2], ('GET', '/v1/models'))

    def test_private_or_mixed_dns_is_rejected_before_connecting(self):
        for addresses in ('127.0.0.1 STREAM local\n', '93.184.216.34 STREAM public\n10.0.0.1 STREAM local\n'):
            with self.subTest(addresses=addresses), patch.object(diagnostics.socket, 'create_connection') as connect:
                self.enterContext(patch.object(diagnostics.subprocess, 'run', return_value=types.SimpleNamespace(stdout=addresses)))
                result = diagnostics.measure_provider(self.settings['providers'][0], 'api')
                self.assertEqual(result['observations'][0]['data']['dns']['error_code'], 'non_public_destination')
                self.assertEqual(result['observations'][0]['data']['tls']['state'], 'not_checked')
                connect.assert_not_called()

    def test_http_failure_and_bad_catalog_are_distinguished(self):
        result, stages, connection = self.probe(status=401)
        self.assertEqual(stages['http']['error_code'], 'authentication_failed')
        self.assertEqual(stages['catalog']['state'], 'not_checked')
        self.assertEqual(connection.request.call_count, 1)
        result, stages, _ = self.probe(payload={'unexpected': 'fixture-secret'})
        self.assertEqual(stages['http']['state'], 'passed')
        self.assertEqual(stages['catalog']['state'], 'failed')
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_tls_and_timeout_errors_do_not_masquerade_as_dns_failure(self):
        self.enterContext(patch.object(diagnostics.subprocess, 'run', return_value=types.SimpleNamespace(stdout='93.184.216.34 STREAM public\n')))
        with patch.object(diagnostics.socket, 'create_connection', side_effect=TimeoutError('private message')):
            result = diagnostics.measure_provider(self.settings['providers'][0], 'api')
        self.assertEqual(result['observations'][0]['data']['tcp']['error_code'], 'timeout')
        self.assertEqual(result['observations'][0]['data']['dns']['state'], 'passed')
        with patch.object(diagnostics.socket, 'create_connection', return_value=MagicMock()), patch.object(diagnostics.ssl, 'create_default_context') as context:
            context.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError('private certificate')
            result = diagnostics.measure_provider(self.settings['providers'][0], 'api')
        self.assertEqual(result['observations'][0]['data']['tls']['error_code'], 'tls_certificate_invalid')
        self.assertNotIn('private certificate', json.dumps(result))

    def test_real_pinned_TLS_and_authenticated_catalog_only(self):
        binary = os.getenv('PRIVACY_OPENSSL_BIN') or shutil.which('openssl')
        if not binary: self.skipTest('Set PRIVACY_OPENSSL_BIN for real TLS integration fixture')
        key, cert = self.root / 'key.pem', self.root / 'cert.pem'
        subprocess.run([binary, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=provider.example',
            '-addext', 'subjectAltName=DNS:provider.example', '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                payload = b'{"data":[{"id":"model"}]}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        create_connection = socket.create_connection
        trusted = ssl.create_default_context(cafile=str(cert))
        def pinned(address, timeout):
            self.assertEqual(address, ('93.184.216.34', 443))
            return create_connection(('127.0.0.1', server.server_port), timeout=timeout)
        try:
            with patch.object(diagnostics.subprocess, 'run', return_value=types.SimpleNamespace(stdout='93.184.216.34 STREAM public\n')), \
                 patch.object(diagnostics.socket, 'create_connection', side_effect=pinned) as connect, \
                 patch.object(diagnostics.ssl, 'create_default_context', return_value=trusted):
                result = diagnostics.measure_provider(self.settings['providers'][0], 'api')
            self.assertEqual(connect.call_count, 1)
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
        self.assertEqual(seen, [('/v1/models', 'Bearer fixture-secret')])
        self.assertEqual(result['observations'][0]['data']['catalog']['state'], 'passed')
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_unknown_inventory_and_permission_denial_prevent_network_activity(self):
        with patch.object(diagnostics, 'measure_provider') as measure:
            with self.assertRaises(HTTPException) as error:
                diagnostics.integration_probe(self.env, diagnostics.IntegrationQuery(integration_id='ai:' + 'f' * 32))
            self.assertEqual(error.exception.status_code, 404)
            for name in ('application_diagnostics', 'operation_timeline', 'integration_probe'):
                with self.assertRaises(HTTPException) as error: control.control_tool(self.env, name, actor='unknown')
                self.assertEqual(error.exception.status_code, 403)
            measure.assert_not_called()

    def test_oneshot_execution_is_applied_and_history_is_explicitly_limited(self):
        observation = service_state._service_observation({'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
            'Type': 'oneshot', 'RemainAfterExit': 'yes', 'Result': 'success', 'ExecMainStatus': '0', 'ExecMainExitTimestamp': 'finished'}, 'now')
        self.assertEqual(observation['execution']['state'], 'applied')
        self.assertEqual(observation['execution']['last_success_at'], 'finished')
        self.assertIsNone(observation['execution']['last_error'])
        finished = service_state._service_observation({'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
            'Type': 'oneshot', 'Result': 'success', 'ExecMainStatus': '0', 'ExecMainExitTimestamp': 'finished'}, 'now')
        self.assertEqual(finished['execution']['state'], 'succeeded')
        unknown = service_state._service_observation({'LoadState': 'loaded', 'ActiveState': 'inactive', 'Type': 'oneshot'}, 'now')
        self.assertNotEqual(unknown['execution']['state'], 'succeeded')

    def test_catalog_has_durable_stages_and_error_response_operation_id(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(control, 'require_installed'))
        provider = self.settings['providers'][0]
        with patch.object(control.assistant_provider, 'catalog', return_value=['one']):
            result = control.model_catalog(api, control.Catalog(provider_url=provider['provider_url'], provider_id=provider['id']))
        report = diagnostics.timeline(self.env, diagnostics.TimelineQuery(operation_id=result['operation_id']))
        self.assertTrue(any(row['data'].get('stage') == 'catalog' for row in report['timeline']))
        self.assertEqual(report['observations'][0]['data']['state'], 'succeeded')
        self.assertEqual(report['observations'][0]['data']['integration_id'], 'ai:' + provider['id'])
        app = FastAPI()
        app.add_middleware(observability.RequestEvidence, root=lambda: self.root, service='panel')
        app.add_exception_handler(HTTPException, api.public_http_exception_handler)
        @app.get('/catalog')
        def catalog(): return control.model_catalog(api, control.Catalog(provider_url=provider['provider_url'], provider_id=provider['id']))
        with patch.object(control.assistant_provider, 'catalog', side_effect=ValueError('fixture-secret')), TestClient(app) as client:
            response = client.get('/catalog')
        self.assertEqual(response.status_code, 502)
        self.assertRegex(response.json()['operation_id'], '^[0-9a-f]{32}$')
        self.assertNotIn('fixture-secret', response.text)
