from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import manager
import mihomo_sources as sources
from relay_private_transport import RelayNotVerified


class ProfileSourceTests(unittest.TestCase):
    def setUp(self):
        self.item = {'id': 'source', 'name': 'Source', 'common_device_id': 'common', 'routing': {'route_dns': True},
                     'subscription_token': 'SOURCE_TOKEN', 'limits': {'days': 5},
                     'devices': [{'id': 'common', 'name': 'Common', 'routing': {}},
                                 {'id': 'phone', 'name': 'Phone', 'hwid_hash': 'a' * 64,
                                  'personal_rule_ids': ['private-rule'], 'routing': {'ipv6': False}}],
                     'connections': [{'id': f'c{i}', 'name': f'Channel {i}', 'component': 'transport-wg',
                                      'device_id': 'phone' if i < 56 else 'common',
                                      'settings': {'mtu': 1400, 'obfs_password': 'SOURCE_PASSWORD'},
                                      'credential': {'private_key': 'SOURCE_PRIVATE'}} for i in range(57)]}
        self.rows = [self.item, {**deepcopy(self.item), 'id': 'other', 'name': 'Other'}]
        self.enterContext(patch.object(manager, 'profiles', side_effect=lambda: self.rows))
        self.enterContext(patch.object(manager, 'normalize_profile', side_effect=deepcopy))

    def payload(self, **patches):
        spec = {'profile_id': 'source', 'device_id': 'phone', 'expected_revision': manager.profile_revision(self.item), **patches}
        return manager.ProfileCreate(name='Target', copy_from=spec)

    def test_complete_pages_are_filtered_bounded_and_never_render_yaml_or_expose_credentials(self):
        original = deepcopy(self.rows)
        collected, offset = [], 0
        with patch.object(manager, 'render_client_profile', side_effect=RelayNotVerified('private')):
            while offset is not None:
                result = sources.source(manager, 'source', 'phone', 'connections', offset, 20)
                self.assertEqual(result['total'], 56)
                text = json.dumps(result)
                self.assertLess(len(text), 96000)
                self.assertNotIn('SOURCE_', text)
                self.assertNotIn('credential', json.dumps(result['items']))
                collected.extend(row['id'] for row in result['items'])
                offset = result['next_offset']
        self.assertEqual(collected, [f'c{i}' for i in range(56)])
        self.assertEqual(self.rows, original)

    def test_catalog_and_device_pages_do_not_include_connections_hwid_or_tokens(self):
        catalog = sources.catalog(manager, 0, 1)
        self.assertEqual(catalog['total'], 2)
        self.assertEqual(catalog['next_offset'], 1)
        self.assertNotIn('SOURCE_', json.dumps(catalog))
        devices = sources.source(manager, 'source', None, 'devices', 0, 20)
        self.assertEqual([device['id'] for device in devices['items']], ['common', 'phone'])
        self.assertNotIn('hwid_hash', json.dumps(devices))
        self.assertNotIn('private-rule', json.dumps(devices))

    def test_copy_resolves_only_selected_device_without_identity_secrets_rules_or_limits(self):
        result = sources.resolve_copy(manager, self.payload(connection_ids=['c2', 'c4']))
        self.assertIsNone(result.copy_from)
        self.assertEqual([connection.id for connection in result.connections], ['c2', 'c4'])
        self.assertTrue(all(connection.device_id == 'profile-common' for connection in result.connections))
        self.assertEqual(result.routing, {})
        self.assertIsNone(result.limits)
        self.assertIsNone(result.devices[0].hwid_hash)
        self.assertEqual(result.devices[0].personal_rule_ids, [])
        self.assertNotIn('SOURCE_PRIVATE', result.model_dump_json())
        self.assertNotIn('SOURCE_TOKEN', result.model_dump_json())
        self.assertEqual(sources.resolve_copy(manager, self.payload(copy_routing=True)).routing,
                         {'route_dns': True, 'ipv6': False})

    def test_source_changes_between_preparation_and_execution_reject_the_copy(self):
        payload = self.payload()
        self.item['name'] = 'Changed after confirmation'
        with self.assertRaises(HTTPException) as caught:
            sources.resolve_copy(manager, payload)
        self.assertEqual(caught.exception.status_code, 409)

    def test_copy_routing_preserves_profile_and_device_group_overrides(self):
        self.item['routing']['structured_policy_selection'] = '{"media":false,"work":false}'
        self.item['devices'][1]['routing']['structured_policy_selection'] = '{"work":true}'
        copied = sources.resolve_copy(manager, self.payload(copy_routing=True))
        self.assertEqual(json.loads(copied.routing['structured_policy_selection']), {'media': False, 'work': True})

    def test_next_source_page_rejects_a_different_profile_revision(self):
        with patch.dict(manager.app.dependency_overrides, {manager.auth_required: lambda: None}):
            client = TestClient(manager.app)
            first = client.get('/api/mihomo/profiles/source/source?device_id=phone&limit=10').json()
            self.item['connections'].pop(0)
            response = client.get('/api/mihomo/profiles/source/source', params={
                'device_id': 'phone', 'offset': first['next_offset'], 'limit': 10,
                'expected_revision': first['profile']['revision']})
            self.assertEqual(response.status_code, 409)

    def test_invalid_device_foreign_connection_and_duplicate_ids_never_copy_partially(self):
        for changes, status in [({'device_id': 'missing'}, 404), ({'connection_ids': ['c56']}, 422),
                                ({'connection_ids': ['c0', 'c0']}, 422)]:
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as caught:
                sources.resolve_copy(manager, self.payload(**changes))
            self.assertEqual(caught.exception.status_code, status)

    def test_unverified_relay_blocks_copy_without_blocking_source_reads(self):
        self.item['connections'][0]['settings']['relay_host_id'] = 'a' * 32
        with patch.object(manager, 'relay_connection_control'), patch('relay_private_transport.active', side_effect=RelayNotVerified('private')):
            self.assertEqual(sources.source(manager, 'source', 'phone', 'connections', 0, 20)['total'], 56)
            with self.assertRaises(RelayNotVerified):
                sources.resolve_copy(manager, self.payload())

    def test_routes_validate_pages_and_relay_errors_are_typed(self):
        with patch.dict(manager.app.dependency_overrides, {manager.auth_required: lambda: None}):
            client = TestClient(manager.app)
            self.assertEqual(client.get('/api/mihomo/profiles/source/source?device_id=phone&limit=20').status_code, 200)
            for query in ('limit=21', 'offset=-1', 'section=unsupported'):
                self.assertEqual(client.get('/api/mihomo/profiles/source/source?' + query).status_code, 422)
            with patch.object(manager, 'render_client_profile', side_effect=RelayNotVerified('PRIVATE DEBUG')):
                response = client.get('/api/mihomo/profiles/source/config?device_id=phone')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['code'], 'relay_not_verified')
                self.assertNotIn('PRIVATE DEBUG', response.text)

    def test_two_created_copies_receive_fresh_credentials_and_retries_are_idempotent(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, 'DATA_ROOT', root / 'mihomo'))
        self.enterContext(patch.object(manager, 'profile_runtime_transaction', return_value=nullcontext()))
        self.enterContext(patch.object(manager, 'validate_connection_inputs', side_effect=lambda rows: [row.model_dump() for row in rows]))
        for name in ('validate_cdn_delivery', 'validate_personal_rule_ids', 'validate_client_capabilities', 'write_action'):
            self.enterContext(patch.object(manager, name))
        self.enterContext(patch.object(manager, 'validate_routing', side_effect=lambda value, current: deepcopy(value)))
        self.enterContext(patch.object(manager, 'profile_response', side_effect=deepcopy))
        self.enterContext(patch.object(manager, 'sync_legacy_profile_fields'))
        self.enterContext(patch.object(manager, 'save_profiles', side_effect=lambda rows: self.rows.__setitem__(slice(None), deepcopy(rows))))
        def provision(identity, definitions, **kwargs):
            self.assertNotIn('SOURCE_PRIVATE', json.dumps(definitions))
            return [{**row, 'credential': {'private_key': 'NEW_' + identity}} for row in definitions]
        provisioner = self.enterContext(patch.object(manager, 'provision_connections', side_effect=provision))
        original = deepcopy(self.item)
        payload = self.payload(connection_ids=['c0', 'c1'])
        payload.operation_id = '1' * 32
        first = manager.create_profile(payload)
        self.assertEqual(manager.create_profile(payload)['id'], first['id'])
        payload = self.payload(connection_ids=['c0', 'c1'])
        payload.name, payload.operation_id = 'Second', '2' * 32
        second = manager.create_profile(payload)
        self.assertEqual(provisioner.call_count, 2)
        self.assertNotEqual(first['subscription_token'], second['subscription_token'])
        self.assertNotEqual(first['connections'][0]['credential'], second['connections'][0]['credential'])
        self.assertEqual(self.rows[0], original)
        stale = self.payload()
        self.rows[0]['name'] = 'Changed before worker execution'
        with self.assertRaises(HTTPException) as caught:
            manager.create_profile(stale)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(provisioner.call_count, 2)
