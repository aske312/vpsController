"""Relay failures survive restarts without rolling back native profiles."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.api.support import manager
from tests.api.test_profile_revision import profile
import application_operation
import relay_profile_status


class RelayProfileStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.control = SimpleNamespace(root=self.root / 'relay', pending=self.root / 'relay/pending.json', require_clear=Mock())
        self.host = 'a' * 32
        self.control.read = Mock(return_value={'hosts': [{'id': self.host}]})
        self.connections = [{'id': 'c', 'component': 'transport-wg', 'settings': {'relay_host_id': self.host}, 'credential': {'port': 31000}}]

    def test_failure_is_durable_and_retry_clears_it_without_secrets(self):
        with patch.object(relay_profile_status.relay_connections, 'prepare_mihomo', side_effect=OSError('secret-token')):
            result = relay_profile_status.prepare(self.control, 'abcdef123456', self.connections, 'b' * 32)
        self.assertEqual(result, [{'host_id': self.host, 'reason': 'preparation_failed'}])
        self.assertEqual(relay_profile_status.failures(self.control, 'abcdef123456'), result)
        self.assertNotIn('secret-token', relay_profile_status.path(self.control, 'abcdef123456').read_text())
        with patch.object(relay_profile_status.relay_connections, 'prepare_mihomo') as prepare:
            self.assertEqual(relay_profile_status.prepare(self.control, 'abcdef123456', self.connections, 'c' * 32), [])
        prepare.assert_called_once()
        self.assertEqual(relay_profile_status.failures(self.control, 'abcdef123456'), [])

    def test_uncertain_mutation_is_not_replayed_and_profile_admission_stays_open(self):
        self.control.pending.parent.mkdir(parents=True)
        self.control.pending.write_text('{"unchanged": true}')
        self.control.require_clear.side_effect = application_operation.OperationConflict('pending')
        with patch.object(relay_profile_status.relay_connections, 'prepare_mihomo') as prepare:
            result = relay_profile_status.prepare(self.control, 'abcdef123456', self.connections, 'c' * 32)
        prepare.assert_not_called()
        self.assertEqual(result[0]['reason'], 'recovery_required')
        self.assertEqual(self.control.pending.read_text(), '{"unchanged": true}')
        for action in ('mihomo-profile-create', 'mihomo-profile-update'):
            application_operation.require_recovery_clear(self.root, action)
        with self.assertRaises(application_operation.OperationConflict):
            application_operation.require_recovery_clear(self.root, 'mihomo-profile-relay-prepare')
        (self.root / 'dns-recovery.json').write_text('{}')
        with self.assertRaises(application_operation.OperationConflict):
            application_operation.require_recovery_clear(self.root, 'mihomo-profile-create')

    def test_partial_device_provisioning_keeps_other_hosts_failures(self):
        other = 'd' * 32
        application_operation.atomic_json(relay_profile_status.path(self.control, 'abcdef123456'),
                                          {'failures': [{'host_id': other, 'reason': 'preparation_failed'}]})
        with patch.object(relay_profile_status.relay_connections, 'prepare_mihomo'):
            result = relay_profile_status.prepare(self.control, 'abcdef123456', self.connections, 'c' * 32, partial=True)
        self.assertEqual(result, [{'host_id': other, 'reason': 'preparation_failed'}])
        with patch.object(relay_profile_status.relay_connections, 'prepare_mihomo'):
            self.assertEqual(relay_profile_status.prepare(self.control, 'abcdef123456', self.connections, 'c' * 32), [])

    def test_relay_failure_does_not_rollback_profile_update_or_replace_credentials(self):
        item = profile()
        item['connections'] = deepcopy(self.connections)
        item['connections'][0]['device_id'] = 'common'
        old = deepcopy(item['connections'])
        definition = {key: value for key, value in old[0].items() if key != 'credential'}
        with (patch.object(manager, 'profiles', return_value=[item]),
              patch.object(manager, 'profile_runtime_transaction', return_value=nullcontext()),
              patch.object(manager, 'preflight_client_export_update'),
              patch.object(manager, 'validate_connection_inputs', return_value=[definition]),
              patch.object(manager, 'relay_connection_control', return_value=self.control),
              patch.object(relay_profile_status.relay_connections, 'prepare_mihomo', side_effect=OSError()),
              patch.object(manager, 'reconcile_profile_encryption'),
              patch.object(manager, 'save_profiles') as save, patch.object(manager, 'write_action'),
              patch.object(manager, 'verified_profile_clients', return_value=[])):
            manager.update_profile('one', manager.ProfileUpdate(connections=[definition]))
        save.assert_called_once()
        self.assertEqual(item['connections'], old)
        self.assertEqual(relay_profile_status.failures(self.control, 'one')[0]['host_id'], self.host)

    def test_new_native_credentials_are_kept_if_relay_is_offline(self):
        definitions = [{key: value for key, value in self.connections[0].items() if key != 'credential'}]
        with (patch.object(manager, 'relay_connection_control', return_value=self.control),
              patch.object(relay_profile_status.relay_connections, 'prepare_mihomo', side_effect=OSError()),
              patch.object(manager, 'provision', return_value={'port': 31000}) as provision,
              patch.object(manager, 'deprovision') as remove):
            result = manager.provision_connections('abcdef123456', definitions)
        provision.assert_called_once()
        remove.assert_not_called()
        self.assertEqual(result, self.connections)
        self.assertTrue(relay_profile_status.failures(self.control, 'abcdef123456'))

    def test_selected_relay_endpoint_never_falls_back_to_origin(self):
        control = SimpleNamespace(endpoint=Mock(side_effect=ValueError('missing route')))
        with patch.object(manager, 'relay_connection_control', return_value=control):
            with self.assertRaises(ValueError):
                manager.relay_client_lines(self.connections[0], ['    server: origin', '    port: 31000'])
