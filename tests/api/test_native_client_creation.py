import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from tests.api.support import api
import application_operation as operations
import native_client_creation as creation


class NativeClientCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.identity = 'a' * 32
        self.payload = api.ClientCreate(name='Test', protocol='wg').model_dump()
        self.adapter = SimpleNamespace(DATA_DIR=self.directory, ClientCreate=api.ClientCreate,
            ComponentRegistry=Mock(), create_client_native=Mock(return_value={
                'id': self.identity[:16], 'config': 'private-export', 'filename': 'test.conf'}))

    def test_lost_response_reads_exact_export_and_does_not_repeat_creation(self):
        creation.perform(self.adapter, self.payload, self.identity)
        creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_called_once()
        for _ in range(2):
            self.assertEqual(creation.read_result(self.adapter, self.identity)['result']['config'], 'private-export')
        self.assertEqual(creation.read_result(self.adapter, 'b' * 32), {'state': 'pending'})
        if os.name == 'posix':
            self.assertEqual(creation.result_path(self.adapter, self.identity).stat().st_mode & 0o777, 0o600)

    def test_crash_after_side_effect_preserves_intent_and_blocks_reexecution(self):
        self.adapter.create_client_native.side_effect = SystemExit('simulated crash')
        with self.assertRaises(SystemExit):
            creation.perform(self.adapter, self.payload, self.identity)
        self.assertEqual(creation.read_result(self.adapter, self.identity), {'state': 'unknown'})
        with self.assertRaises(operations.OperationConflict):
            creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_called_once()

    def test_export_storage_failure_does_not_allow_second_command(self):
        real = creation.atomic_json
        def save(path, value):
            if value['state'] == 'ready':
                raise OSError('disk full')
            real(path, value)
        with patch.object(creation, 'atomic_json', side_effect=save):
            with self.assertRaises(OSError):
                creation.perform(self.adapter, self.payload, self.identity)
        with self.assertRaises(operations.OperationConflict):
            creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_called_once()

    def test_expiration_removes_secret_but_keeps_replay_guard(self):
        with patch.object(creation.time, 'time', return_value=100):
            creation.perform(self.adapter, self.payload, self.identity)
        with patch.object(creation.time, 'time', return_value=100 + creation.RESULT_TTL):
            creation.expire_results(self.directory)
        self.assertEqual(creation.read_result(self.adapter, self.identity), {'state': 'expired'})
        self.assertNotIn('private-export', creation.result_path(self.adapter, self.identity).read_text())
        with self.assertRaises(operations.OperationConflict):
            creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_called_once()

    def test_invalid_identity_and_corrupt_intent_never_create(self):
        with self.assertRaises(ValueError):
            creation.read_result(self.adapter, '../invalid')
        path = creation.result_path(self.adapter, self.identity)
        path.parent.mkdir()
        path.write_text('{broken')
        with self.assertRaises(operations.OperationConflict):
            creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_not_called()

    def test_ownership_rechecked_before_protocol_mutation(self):
        self.adapter.ComponentRegistry.return_value.require_managed.side_effect = ValueError('foreign')
        with self.assertRaises(ValueError):
            creation.perform(self.adapter, self.payload, self.identity)
        self.adapter.create_client_native.assert_not_called()
        self.assertFalse(creation.result_path(self.adapter, self.identity).exists())

    def test_admission_keeps_payload_identity_and_conflicts_on_different_input(self):
        with patch.object(api, 'DATA_DIR', self.directory), patch.object(api, 'ComponentRegistry'), \
                patch.object(api, 'start_application_operation', return_value={'id': self.identity}) as start:
            payload = api.ClientCreate.model_validate(self.payload)
            api.start_native_client(payload, None, self.identity)
            api.start_native_client(payload, None, self.identity)
            with self.assertRaises(api.HTTPException) as error:
                api.start_native_client(api.ClientCreate(name='Other', protocol='wg'), None, self.identity)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(start.call_count, 2)
            self.assertEqual(json.loads((self.directory / 'operation-inputs' / (self.identity+'.json')).read_text()), self.payload)

    def test_export_is_authenticated_and_not_cacheable(self):
        creation.perform(self.adapter, self.payload, self.identity)
        with patch.object(api, 'DATA_DIR', self.directory):
            client = TestClient(api.app)
            path = '/api/clients/native/' + self.identity
            self.assertEqual(client.get(path).status_code, 401)
            with patch.object(api, 'ADMIN_USER', 'test'), patch.object(api, 'ADMIN_PASSWORD', 'test-password'):
                response = client.get(path, auth=('test', 'test-password'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.json()['result']['config'], 'private-export')
