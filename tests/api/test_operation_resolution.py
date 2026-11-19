import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
import application_operation as operations
import operation_resolution as resolution


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
