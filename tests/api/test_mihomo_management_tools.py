"""Mihomo convenience tools prepare scoped changes without rewriting other data."""
import copy
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.api import test_assistant_control as fixtures
from tests.api.support import manager
import routing_policy

runner = fixtures.runner
real_vps_call = runner.vps_call


class ManagementToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = {'groups': [
            {'id': 'media-id', 'name': 'Media', 'type': 'fallback', 'members': ['*'], 'strategy': 'consistent-hashing'},
            {'id': 'work-id', 'name': 'Work', 'type': 'select', 'members': ['group:Media'], 'strategy': 'consistent-hashing'},
        ], 'rules': [{'kind': 'domain', 'value': 'media.test', 'target': 'Media'}]}
        self.snapshot = {'revision': 'a' * 64, 'values': {'structured_policy': json.dumps(self.policy), 'interval': 30, 'health_timeout': 3000, 'max_failed_times': 2},
                         'structured_policy_schema': routing_policy.Policy.model_json_schema()}
        self.profile = {'id': 'own', 'revision': 'b' * 64, 'name': 'Own', 'common_device_id': 'common',
                        'routing': {'structured_policy_selection': '{"work-id":false}'}, 'devices': [
                            {'id': 'common', 'name': 'Common', 'scope': 'common', 'routing': {'structured_policy_selection': '{"work-id":false}'}},
                            {'id': 'phone', 'name': 'Phone', 'hwid_hash': 'private-hwid', 'routing': {'route_dns': True}, 'personal_rule_ids': ['personal']},
                        ]}
        self.calls = []
        async def gateway(settings, actor, name, **arguments):
            self.assertEqual(name, 'mihomo_api')
            self.assertEqual(actor, '11')
            self.calls.append(copy.deepcopy(arguments))
            if arguments['method'] == 'GET':
                if arguments['path'] == '/profiles': return {'items': [copy.deepcopy(self.profile)]}
                return copy.deepcopy(self.snapshot)
            return {'confirmation_required': True}
        self.enterContext(patch.object(runner, 'vps_call', side_effect=gateway))

    async def call(self, command, **arguments):
        return await runner.mihomo_manage({}, '11', command, **arguments)

    def writes(self): return [call for call in self.calls if call['method'] != 'GET']

    async def test_relay_block_is_explained_to_the_model_instead_of_becoming_generic_rejection(self):
        response = runner.httpx.Response(409, json={'detail': {'code': 'relay_not_verified', 'message': 'PRIVATE DEBUG'}})
        @asynccontextmanager
        async def client(**kwargs):
            yield SimpleNamespace(post=AsyncMock(return_value=response))
        settings = {'control_token': 'fixture', 'control_token_scope': {'module': 'mihomo-mcp', 'actor': '11'}}
        with patch.object(runner, 'allowed', return_value=True), patch.object(runner.httpx, 'AsyncClient', client):
            result = await real_vps_call(settings, '11', 'mihomo_api', method='GET', path='/profiles/own/config', body={})
        self.assertEqual(result['error'], 'relay_not_verified')
        self.assertIn('relay_read', result['instruction'])
        self.assertIn('mihomo_source', result['instruction'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_source_pages_and_copy_use_bounded_reads_and_a_server_reference(self):
        self.assertTrue(runner.changing_tool('mihomo_profile_copy', {}))
        self.assertFalse(runner.changing_tool('mihomo_source', {}))
        await self.call('source', section='connections', profile_id='own', device_id='phone', offset=20, limit=20)
        self.assertEqual(self.calls[0]['path'], '/profiles/own/source?section=connections&offset=20&limit=20&device_id=phone')
        result = await self.call('profile_copy', profile_id='own', device_id='phone', expected_revision='b' * 64, name='New')
        self.assertTrue(result['confirmation_required'])
        self.assertEqual(self.writes()[0]['body'], {'name': 'New', 'copy_from': {
            'profile_id': 'own', 'device_id': 'phone', 'expected_revision': 'b' * 64, 'copy_routing': False}})
        self.assertNotIn('connections', self.writes()[0]['body'])
        self.calls.clear()
        self.assertEqual((await self.call('source', limit=True))['error'], 'tool_validation_error')
        self.assertEqual((await self.call('profile_copy', profile_id='own', device_id='phone', expected_revision='stale', name='New'))['error'], 'tool_validation_error')
        self.assertEqual(self.writes(), [])

    async def test_create_prepares_connections_and_rules_together_with_explicit_default_device(self):
        result = await self.call('profile_create', name='For user', connections=[{'component': 'transport-wg', 'settings': {'mtu': 1400}}],
                                 routing={'structured_policy': self.policy})
        self.assertTrue(result['confirmation_required'])
        write = self.writes()[0]
        self.assertEqual((write['method'], write['path']), ('POST', '/profiles'))
        self.assertEqual(write['body']['connections'][0]['device_id'], write['body']['devices'][0]['id'])
        self.assertEqual(json.loads(write['body']['routing']['structured_policy']), self.policy)

    async def test_delete_and_rotate_require_fresh_profile_revision(self):
        for command, method, path in [('profile_delete', 'DELETE', '/profiles/own'),
                                      ('subscription_rotate', 'POST', '/profiles/own/subscription/rotate')]:
            self.calls.clear()
            self.assertEqual((await self.call(command, profile_id='own', expected_revision='f' * 64))['error'], 'tool_validation_error')
            self.assertEqual(self.writes(), [])
            result = await self.call(command, profile_id='own', expected_revision='b' * 64)
            self.assertTrue(result['confirmation_required'])
            self.assertEqual(self.writes()[0], {'method': method, 'path': path, 'body': {'expected_revision': 'b' * 64}})

    async def test_account_assignment_uses_owner_scoped_api_without_changing_roles(self):
        await self.call('accounts')
        self.assertEqual(self.calls[0]['path'], '/accounts')
        await self.call('profile_assign', telegram_id=123, profile_id='own')
        self.assertEqual(self.writes()[0], {'method': 'PATCH', 'path': '/accounts/123/profile', 'body': {'profile_id': 'own'}})
        self.assertEqual((await self.call('profile_assign', telegram_id=True, profile_id='own'))['error'], 'tool_validation_error')

    async def test_settings_normalize_policy_objects_and_reject_invalid_rule_kinds_before_preparing(self):
        result = await self.call('settings', section='routing', method='PATCH', values={'structured_policy': self.policy}, expected_revision='a' * 64)
        self.assertTrue(result['confirmation_required'])
        self.assertEqual(json.loads(self.writes()[0]['body']['values']['structured_policy']), self.policy)
        self.calls.clear()
        self.policy['rules'][0]['kind'] = 'DOMAIN-KEYWORD'
        rejected = await self.call('settings', section='routing', method='PATCH', values={'structured_policy': self.policy}, expected_revision='a' * 64)
        self.assertEqual(rejected['error'], 'tool_validation_error')
        self.assertEqual(self.writes(), [])

    async def test_group_list_explains_effective_inherited_checks(self):
        result = await self.call('groups')
        self.assertEqual(result['groups'][0]['effective_health']['timeout'], 3000)
        self.assertEqual(result['groups'][0]['rule_count'], 1)
        self.assertIsNone(result['groups'][1]['effective_health'])
        self.assertEqual(self.writes(), [])

    async def test_group_health_update_preserves_ids_rules_and_other_groups(self):
        result = await self.call('group_update', group_id='media-id', expected_revision='a' * 64,
                                 timeout=5000, interval=300, max_failed_times=1, url='https://health.test/generate_204')
        self.assertTrue(result['confirmation_required'])
        write = self.writes()[0]
        self.assertEqual(write['path'], '/routing/settings')
        policy = json.loads(write['body']['values']['structured_policy'])
        self.assertEqual(policy['rules'], self.policy['rules'])
        self.assertEqual(policy['groups'][1], self.policy['groups'][1])
        self.assertEqual(policy['groups'][0]['id'], 'media-id')
        config = routing_policy.groups(json.dumps(policy), [({'id': 'c'}, 'Node', None, None)], {})
        self.assertEqual(config[0]['timeout'], 5000)
        self.assertEqual(config[0]['interval'], 300)
        self.assertEqual(config[0]['max-failed-times'], 1)

    async def test_rename_keeps_identity_and_rewrites_only_its_references(self):
        await self.call('group_update', group_id='media-id', expected_revision='a' * 64, name='Video')
        policy = json.loads(self.writes()[0]['body']['values']['structured_policy'])
        self.assertEqual(policy['groups'][0]['id'], 'media-id')
        self.assertEqual(policy['rules'][0]['target'], 'Video')
        self.assertEqual(policy['groups'][1]['members'], ['group:Video'])

    async def test_invalid_fields_cycles_and_stale_revisions_never_prepare_a_write(self):
        for arguments in ({'timeout': 5}, {'timeout': True}, {'interval': 0}, {'strategy': 'random'}, {'url': 'file:///secret'},
                          {'url': 'https://[broken'}, {'url': 'https://health.test:99999/'},
                          {'members': ['group:Work']}, {'members': ['group:missing']}, {'name': 'DIRECT'},
                          {'expected_revision': 'c' * 64}):
            with self.subTest(arguments=arguments):
                result = await self.call('group_update', **{'group_id': 'media-id', 'expected_revision': 'a' * 64, **arguments})
                self.assertEqual(result['error'], 'tool_validation_error')
        self.assertEqual(self.writes(), [])

    async def test_creation_and_health_reset_are_explicit_and_preserve_strategy(self):
        result = await self.call('group_update', expected_revision='a' * 64, name='Balance', group_type='load-balance', strategy='round-robin')
        self.assertTrue(result['confirmation_required'])
        policy = json.loads(self.writes()[0]['body']['values']['structured_policy'])
        self.assertTrue(policy['groups'][-1]['id'])
        self.assertEqual(policy['groups'][-1]['strategy'], 'round-robin')
        self.policy['groups'][0].update(timeout=5000, interval=300)
        self.snapshot['values']['structured_policy'] = json.dumps(self.policy)
        await self.call('group_update', group_id='media-id', expected_revision='a' * 64, inherit_health=True)
        policy = json.loads(self.writes()[-1]['body']['values']['structured_policy'])
        self.assertNotIn('timeout', policy['groups'][0])
        self.assertNotIn('interval', policy['groups'][0])
        self.assertEqual(policy['groups'][0]['strategy'], 'consistent-hashing')

    async def test_delete_blocks_referenced_group_and_preserves_remaining_rules(self):
        self.assertEqual((await self.call('group_delete', group_id='media-id', expected_revision='a' * 64))['error'], 'tool_validation_error')
        self.assertEqual(self.writes(), [])
        self.assertTrue((await self.call('group_delete', group_id='work-id', expected_revision='a' * 64))['confirmation_required'])
        policy = json.loads(self.writes()[0]['body']['values']['structured_policy'])
        self.assertEqual([group['id'] for group in policy['groups']], ['media-id'])
        self.assertEqual(policy['rules'], self.policy['rules'])

    async def test_device_selection_preserves_other_devices_and_does_not_send_hwid(self):
        result = await self.call('group_select', profile_id='own', device_id='phone', expected_revision='b' * 64, selection={'media-id': False})
        self.assertTrue(result['confirmation_required'])
        body = self.writes()[0]['body']
        self.assertNotIn('routing', body)
        phone = body['devices'][1]
        self.assertEqual(phone['personal_rule_ids'], ['personal'])
        self.assertTrue(phone['routing']['route_dns'])
        self.assertEqual(json.loads(phone['routing']['structured_policy_selection']), {'media-id': False})
        self.assertNotIn('hwid_hash', phone)
        self.assertEqual(body['devices'][0]['routing'], self.profile['devices'][0]['routing'])

    async def test_common_selection_updates_both_profile_and_common_device(self):
        await self.call('group_select', profile_id='own', expected_revision='b' * 64, selection={'media-id': False})
        body = self.writes()[0]['body']
        self.assertEqual(json.loads(body['routing']['structured_policy_selection']), {'work-id': False, 'media-id': False})
        self.assertEqual(body['routing']['structured_policy_selection'], body['devices'][0]['routing']['structured_policy_selection'])
        self.assertEqual((await self.call('group_select', profile_id='own', expected_revision='b' * 64, selection={'missing': True}))['error'], 'tool_validation_error')
        self.assertEqual(len(self.writes()), 1)

    async def test_typed_settings_and_profile_changes_keep_the_revision(self):
        self.assertTrue((await self.call('settings', section='dns', method='PATCH', values={'ipv6': True}, expected_revision='a' * 64))['confirmation_required'])
        self.assertEqual(self.writes()[0]['body'], {'values': {'ipv6': True}, 'expected_revision': 'a' * 64})
        await self.call('profile_update', profile_id='own', expected_revision='b' * 64, changes={'name': 'New'})
        self.assertEqual(self.writes()[1]['body'], {'name': 'New', 'expected_revision': 'b' * 64})
        self.assertEqual((await self.call('read', view='config', profile_id='../own'))['error'], 'tool_validation_error')
