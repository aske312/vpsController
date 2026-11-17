"""Mihomo convenience tools prepare scoped changes without rewriting other data."""
import copy
import json
import unittest
from unittest.mock import patch

from tests.api import test_assistant_control as fixtures
from tests.api.support import manager
import routing_policy

runner = fixtures.runner


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
