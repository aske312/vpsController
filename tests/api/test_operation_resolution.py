import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from fastapi.testclient import TestClient
from fastapi import HTTPException
from tests.api.support import api
import application_operation as operations
import operation_resolution as resolution
import assistant_control
import application_diagnostics
import observability_control


class OperationResolutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.identity = 'a' * 32

    def write(self, folder='operations', **fields):
        path = self.root / folder / (self.identity + '.json')
        value = {'id': self.identity, 'state': 'failed', 'result': 'exit-code', 'message': 'Original failure', **fields}
        operations.atomic_json(path, value)
        return path, value

    def test_resolution_is_durable_reversible_and_preserves_result_and_retention(self):
        path, original = self.write()
        modified = path.stat().st_mtime_ns
        result = resolution.set_resolved(self.root, self.identity, 'system', True)
        self.assertTrue(result['resolved_at'])
        saved = operations.read(path)
        self.assertEqual(saved, {**original, **result})
        self.assertEqual(path.stat().st_mtime_ns, modified)
        self.assertEqual(resolution.set_resolved(self.root, self.identity, 'system', True), result)
        resolution.set_resolved(self.root, self.identity, 'system', False)
        self.assertEqual(operations.read(path), {**original, 'resolved_at': None})

    def test_sources_are_isolated_and_only_failed_terminal_results_can_be_resolved(self):
        system, original = self.write()
        for source, folder in [('mihomo', 'mihomo/operations'), ('cdn', 'cdn-operations')]:
            path, _ = self.write(folder)
            resolution.set_resolved(self.root, self.identity, source, True)
            self.assertTrue(operations.read(path)['resolved_at'])
            self.assertEqual(operations.read(system), original)
        for state in ('running', 'queued', 'unknown', 'cancelled', 'succeeded'):
            path, value = self.write(state=state, result='success')
            with self.assertRaises(operations.OperationConflict):
                resolution.set_resolved(self.root, self.identity, 'system', True)
            self.assertEqual(operations.read(path), value)

    def test_missing_invalid_or_corrupt_records_are_not_replaced(self):
        with self.assertRaises(LookupError):
            resolution.set_resolved(self.root, self.identity, 'system', True)
        for identity, source in [('../escape', 'system'), (self.identity, '../escape')]:
            with self.assertRaises(ValueError):
                resolution.set_resolved(self.root, identity, source, True)
        path, _ = self.write()
        path.write_text('{')
        with self.assertRaises(operations.OperationConflict):
            resolution.set_resolved(self.root, self.identity, 'system', True)
        self.assertEqual(path.read_text(), '{')

    def test_api_requires_authentication_and_validates_payload(self):
        client = TestClient(api.app)
        endpoint = f'/api/application/operations/{self.identity}/resolution'
        self.assertEqual(client.put(endpoint, json={'resolved': True}).status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        with patch.object(api, 'DATA_DIR', self.root):
            self.write()
            self.assertEqual(client.put(endpoint, json={'resolved': 'yes'}).status_code, 422)
            self.assertEqual(client.put(endpoint + '?source=../', json={'resolved': True}).status_code, 422)
            self.assertEqual(client.put(endpoint, json={'resolved': True}).status_code, 200)
            self.assertTrue(operations.read(self.root / 'operations' / (self.identity + '.json'))['resolved_at'])

    def test_history_exposes_resolution_without_changing_original_failure(self):
        self.write()
        resolution.set_resolved(self.root, self.identity, 'system', True)
        with patch.object(api, 'DATA_DIR', self.root), patch.object(api, 'ACTION_FILE', self.root / 'application-action.json'), patch.object(api.cdn_operation, 'DIRECTORY', self.root / 'cdn-operations'):
            item = api.application_operations(None)['items'][0]
        self.assertEqual(item['state'], 'failed')
        self.assertEqual(item['message'], 'Original failure')
        self.assertTrue(item['resolved_at'])

    def test_history_includes_records_beyond_100_from_each_source(self):
        for index in range(105):
            for folder in ('operations', 'mihomo/operations', 'cdn-operations'):
                # Separate identities prevent delegated-worker deduplication.
                offset = {'operations': 0, 'mihomo/operations': 1000, 'cdn-operations': 2000}[folder]
                key = f'{index + offset:032x}'
                operations.atomic_json(self.root / folder / (key + '.json'), {'id': key, 'action': 'fixture-update', 'state': 'failed', 'started_at': '2026-11-19T12:00:00Z'})
        with patch.object(api, 'DATA_DIR', self.root), patch.object(api, 'ACTION_FILE', self.root / 'application-action.json'), patch.object(api.cdn_operation, 'DIRECTORY', self.root / 'cdn-operations'):
            items = api.application_operations(None)['items']
        self.assertEqual(len(items), 315)
        for source in ('system', 'mihomo', 'cdn'):
            self.assertEqual(sum(item['source'] == source for item in items), 105)

    def offer(self, actor='panel'):
        report = {'authoritative_result': {'operation_id': self.identity, 'source': 'system', 'context': {'outcome': 'error'}},
                  'timeline': [{'stage': 'complete'}]}
        return resolution.review_offer(SimpleNamespace(DATA_DIR=self.root), actor, report)

    def test_assistant_requires_review_receipt_and_retains_its_checks_without_rewriting_result(self):
        path, original = self.write()
        settings = {'control_token': 'signing-key', 'providers': [{'api_key': 'private-fixture'}], 'accounts': []}
        env = SimpleNamespace(DATA_DIR=self.root)
        with patch.object(assistant_control, 'read', return_value=settings):
            offer = self.offer()
            body = {key: offer[key] for key in ('operation_id', 'source', 'review_token')}
            body.update(resolved=True, review='Checked the origin and API: recovered. private-fixture')
            result = observability_control.call(env, {'method': 'POST', 'path': '/operation-resolution', 'body': body}, 'panel')
        self.assertTrue(result['resolved_at'])
        self.assertNotIn('private-fixture', result['resolution_review']['note'])
        self.assertEqual(result['resolution_review']['by'], 'assistant')
        saved = operations.read(path)
        self.assertEqual(saved['state'], original['state'])
        self.assertEqual(saved['result'], original['result'])
        resolution.set_resolved(self.root, self.identity, 'system', False)
        self.assertNotIn('resolution_review', operations.read(path))
        import operation_log
        events = operation_log.get(self.root, 'system', self.identity)['events']
        self.assertEqual(events[-2]['stage'], 'resolution')
        self.assertIn('Checked the origin', events[-2]['message'])
        self.assertEqual(events[-1]['stage'], 'resolution')

    def test_agent_cannot_acknowledge_without_permission_or_fresh_matching_review(self):
        path, original = self.write()
        settings = {'control_token': 'signing-key', 'accounts': [{'telegram_id': 1, 'role': 'owner'}, {'telegram_id': 2, 'role': 'owner'}]}
        env = SimpleNamespace(DATA_DIR=self.root)
        with patch.object(assistant_control, 'read', return_value=settings):
            offer = self.offer('1')
            body = {key: offer[key] for key in ('operation_id', 'source', 'review_token')}
            body.update(review='Inspected error stages and checked current status.')
            for actor, change in [('unknown', {}), ('2', {}), ('1', {'review_token': 'forged-invalid-token'}), ('1', {'review': ' ' * 20})]:
                with self.assertRaises(HTTPException) as caught:
                    resolution.assistant_resolve(env, actor, {**body, **change})
                self.assertIn(caught.exception.status_code, (403, 409))
            with patch.object(resolution.time, 'time', return_value=10**12):
                with self.assertRaises(HTTPException) as caught: resolution.assistant_resolve(env, '1', body)
                self.assertEqual(caught.exception.status_code, 409)
            operations.atomic_json(path, {**original, 'message': 'new failure evidence'})
            with self.assertRaises(HTTPException) as caught: resolution.assistant_resolve(env, '1', body)
            self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn('resolved_at', operations.read(path))

    def test_timeline_exposes_retained_evidence_and_review_offer_to_the_agent(self):
        self.write(started_at='2026-11-19T12:00:00Z')
        import operation_log
        operation_log.append(self.root, {'id': self.identity, 'state': 'failed', 'message': 'failed stage'})
        env = SimpleNamespace(DATA_DIR=self.root, observe_service=lambda _: {})
        with patch.object(assistant_control, 'read', return_value={'control_token': 'signing-key', 'accounts': []}):
            report = application_diagnostics.call(env, 'operation_timeline', {'body': {'operation_id': self.identity}}, 'panel')
        self.assertTrue(report['resolution_review']['review_token'])
        self.assertEqual(report['authoritative_result']['state'], 'failed')
        self.assertTrue(any(row['source'] == 'retained_operation_evidence' for row in report['observations']))
