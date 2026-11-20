"""Relay tools observe and prepare; only a human can admit an existing worker."""
import copy
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tests.api import test_assistant_control as fixtures
from tests.api.support import api
import assistant_control as control
import assistant_commands as commands


class RelayToolsTests(unittest.TestCase):
    payload = fixtures.AssistantTests.payload
    save = fixtures.AssistantTests.save

    def setUp(self):
        fixtures.AssistantTests.setUp(self)
        self.save(self.payload())
        self.host_id = 'b' * 32
        self.revision = 'c' * 32
        self.route_id = 'pro-' + 'd' * 32
        self.host = {'id': self.host_id, 'name': 'Relay', 'agent_url': 'https://8.8.8.8:9443',
                     'certificate_sha256': 'e' * 64, 'api_version': 1, 'token': 'secret-relay-token',
                     'client_address': 'relay.example', 'country_code_override': 'DE'}
        self.saved = {'revision': self.revision, 'hosts': [self.host], 'routes': [
            {'host_id': self.host_id, 'id': self.route_id, 'transport': 'tcp', 'listen_port': 20001, 'target_port': 443}]}
        def checked(revision):
            if revision != self.revision: raise control.operations.OperationConflict('Revision changed')
            return copy.deepcopy(self.saved)
        self.controller = SimpleNamespace(checked=checked, read=lambda: copy.deepcopy(self.saved), require_clear=lambda: None)
        self.enterContext(patch.object(api, 'relay_control', return_value=self.controller))
        self.enterContext(patch.object(api, 'network_consumers', return_value=[{'id': 'mihomo:profile:conn'}]))
        self.enterContext(patch.object(api, 'relay_targets', return_value=[{'transport': transport, 'port': 443} for transport in ('tcp', 'udp')]))
        self.real_start = api.start_relay_mutation
        self.started = self.enterContext(patch.object(api, 'start_relay_mutation', side_effect=lambda action, body, identity: {'id': identity, 'state': 'queued'}))
        value = control.read(api)
        value['mcp']['vps-controller-mcp'] = True
        control.store(api, value)
        self.client = TestClient(api.app)
        self.headers = {'Authorization': 'Bearer ' + control.read(api)['control_token'], 'X-MCP-Actor': 'panel'}

    def tool(self, name='relay_prepare', body=None, headers=None):
        return self.client.post('/api/assistant/control-tool', headers=headers or self.headers,
                                json={'name': name, 'body': body or {}})

    def change(self, action='configure_host', **changes):
        return {'action': action, 'host_id': self.host_id, 'expected_revision': self.revision,
                'changes': changes or ({'name': 'Renamed'} if action == 'configure_host' else {})}

    def confirm(self, code, **fields):
        with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
            return self.client.post('/api/assistant/service-confirm', json={'code': code, **fields})

    def pending(self, actor='panel'):
        return control.operations.read(commands.pending_path(api, actor))

    def test_read_uses_existing_snapshot_filters_secrets_and_pages_routes(self):
        snapshot = {'revision': self.revision, 'pending': False, 'hosts': [{**self.host, 'state': 'connected',
            'error': 'secret-relay-token', 'agent_revision': 7, 'routes': self.saved['routes'] * 3,
            'private_transport': {'state': 'ready', 'private_key': 'must-not-return'}}],
            'profiles': [{'id': 'profile', 'name': 'P', 'channels': [
                {'id': 'mihomo:profile:conn', 'host_id': self.host_id, 'label': 'Connection', 'transport': 'tcp', 'route_ids': [self.route_id]}]}]}
        with patch.object(api, 'get_relays', return_value=JSONResponse(snapshot)) as read:
            response = self.tool('relay_read', {'host_id': self.host_id, 'route_offset': 1, 'route_limit': 1})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['revision'], self.revision)
        self.assertEqual(result['hosts'][0]['routes_total'], 3)
        self.assertTrue(result['hosts'][0]['routes_more'])
        self.assertEqual(len(result['hosts'][0]['routes']), 1)
        self.assertEqual(result['profiles'][0]['channels'][0]['id'], 'mihomo:profile:conn')
        self.assertNotIn('secret-relay-token', response.text)
        self.assertNotIn('must-not-return', response.text)
        self.assertNotIn('certificate_sha256', response.text)
        read.assert_called_once_with(None, view='live')
        self.started.assert_not_called()

    def test_read_bounds_complete_json_without_hiding_pagination(self):
        routes = [{**self.saved['routes'][0], 'diagnostics': {'reason': 'x' * 5000, 'messages': ['z' * 1000] * 20}} for _ in range(64)]
        with patch.object(api, 'get_relays', return_value=JSONResponse({'revision': self.revision, 'hosts': [{**self.host, 'routes': routes}]})):
            response = self.tool('relay_read', {'route_limit': 64})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(json.dumps(response.json(), ensure_ascii=False).encode()), 28000)
        self.assertTrue(response.json()['hosts'][0]['routes_more'])

    def test_prepare_only_then_human_confirmation_preserves_endpoint_and_credentials(self):
        response = self.tool(body=self.change(client_address='new-relay.example'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('code', response.json())
        self.assertNotIn('secret-relay-token', response.text)
        self.started.assert_not_called()
        record = self.pending()
        self.assertNotIn('secret-relay-token', json.dumps(record))
        self.assertEqual(self.confirm('0' * 8).status_code, 409)
        confirmed = self.confirm(record['code'])
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.started.call_args.args[0], 'relay-host-save')
        payload = self.started.call_args.args[1]
        self.assertEqual(payload['agent_url'], self.host['agent_url'])
        self.assertEqual(payload['certificate_sha256'], self.host['certificate_sha256'])
        self.assertEqual(payload['client_address'], 'new-relay.example')
        self.assertEqual(payload['token'], '')  # Existing worker retains the saved token.
        self.assertEqual(self.started.call_args.args[2], record['operation_id'])
        self.assertEqual(self.confirm(record['code']).status_code, 200)
        self.started.assert_called_once()

    def test_each_supported_action_uses_its_existing_worker_after_confirmation(self):
        cases = [('create_route', {'transport': 'udp', 'listen_port': 20002, 'target_port': 443, 'agent_revision': 7}, 'relay-route-create'),
                 ('check_route', {'route_id': self.route_id, 'consumer_id': 'mihomo:profile:conn', 'mode': 'media'}, 'relay-route-check'),
                 ('private_transport', {'enabled': True}, 'relay-private-transport'), ('reconnect', {}, 'relay-reconnect')]
        for action, changes, worker in cases:
            with self.subTest(action=action):
                response = self.tool(body=self.change(action, **changes))
                self.assertEqual(response.status_code, 200, response.text)
                record = self.pending()
                result = self.confirm(record['code'])
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(self.started.call_args.args[0], worker)
        self.assertEqual(self.started.call_count, len(cases))

    def test_delete_credentials_arbitrary_targets_and_malformed_actions_are_denied(self):
        cases = [{**self.change(), 'action': 'delete_host'}, {**self.change(), 'action': 'delete_route'},
                 self.change(name='   '),
                 self.change(token='replacement'), self.change(agent_url='https://attacker.example'),
                 self.change(certificate_sha256='a' * 64), self.change('create_route', transport='tcp', listen_port=20000, target_port=443, agent_revision=1, target_ip='127.0.0.1'),
                 self.change('create_route', transport='tcp', listen_port=True, target_port=443, agent_revision=1),
                 self.change('create_route', transport='tcp', listen_port=20000, target_port=22, agent_revision=1),
                 self.change('check_route', route_id='foreign', consumer_id='mihomo:profile:conn'),
                 {**self.change(), 'path': '/api/network/relays/any/address'}]
        for body in cases:
            with self.subTest(body=body): self.assertEqual(self.tool(body=body).status_code, 422)
        self.started.assert_not_called()
        self.assertFalse(self.pending())

    def test_missing_host_foreign_route_and_unknown_consumer_cannot_be_prepared(self):
        cases = [{**self.change(), 'host_id': 'f' * 32},
                 self.change('check_route', route_id='pro-' + 'f' * 32, consumer_id='mihomo:profile:conn'),
                 self.change('check_route', route_id=self.route_id, consumer_id='mihomo:other:conn')]
        for body in cases: self.assertEqual(self.tool(body=body).status_code, 404)
        self.started.assert_not_called()

    def test_revision_expiry_denial_and_live_permissions_fence_the_worker(self):
        self.assertEqual(self.tool(body={**self.change(), 'expected_revision': 'f' * 32}).status_code, 409)
        for kind in ('revision', 'expiry', 'mcp'):
            self.assertEqual(self.tool(body=self.change()).status_code, 200)
            record = self.pending()
            if kind == 'revision': self.revision = 'a' * 32
            elif kind == 'expiry':
                control.operations.atomic_json(commands.pending_path(api, 'panel'), {**record, 'expires': time.time() - 1})
            else:
                value = control.read(api); value['mcp']['vps-controller-mcp'] = False; control.store(api, value)
            result = self.confirm(record['code'])
            self.assertIn(result.status_code, (403, 409), result.text)
        self.started.assert_not_called()

    def test_cancel_is_actor_bound_and_cannot_start_a_worker(self):
        self.tool(body=self.change())
        record = self.pending()
        self.assertEqual(self.confirm(record['code'], telegram_id=11, decision='reject').status_code, 409)
        self.assertTrue(self.pending())
        self.assertEqual(self.confirm(record['code'], decision='reject').status_code, 200)
        self.assertFalse(self.pending())
        self.started.assert_not_called()

    def test_unknown_admission_is_not_replayed_and_remains_visible_via_operation_id(self):
        self.tool(body=self.change())
        record = self.pending()
        self.started.side_effect = api.HTTPException(503, 'Unknown admission')
        self.assertEqual(self.confirm(record['code']).status_code, 503)
        self.assertTrue(self.pending()['submitted'])
        self.assertEqual(self.confirm(record['code']).status_code, 409)
        self.assertEqual(self.tool(body=self.change()).status_code, 409)
        self.started.assert_called_once()

    def test_network_and_management_grants_are_enforced_before_observation_or_mutation(self):
        value = control.read(api)
        value['role_permissions'] = {'administrator': ['server_read']}
        value['accounts'] = [{'telegram_id': 11, 'name': 'User', 'role': 'administrator', 'products': ['assistant', 'mihomo'], 'permission_overrides': {}}]
        control.store(api, value)
        headers = {**self.headers, 'X-MCP-Actor': '11'}
        with patch.object(api, 'get_relays') as observe:
            self.assertEqual(self.tool('relay_read', headers=headers).status_code, 403)
            self.assertEqual(self.tool(body=self.change(), headers=headers).status_code, 403)
            observe.assert_not_called()
        value['accounts'][0]['permission_overrides']['network_read'] = True
        control.store(api, value)
        with patch.object(api, 'get_relays', return_value=JSONResponse({'hosts': []})):
            self.assertEqual(self.tool('relay_read', headers=headers).status_code, 200)
        self.assertEqual(self.tool(body=self.change(), headers=headers).status_code, 403)
        self.started.assert_not_called()

    def test_confirmation_does_not_reenter_application_lock(self):
        from contextlib import contextmanager
        held = False
        @contextmanager
        def lock(*_, **__):
            nonlocal held
            self.assertFalse(held, 'Nested application lock')
            held = True
            try: yield
            finally: held = False
        self.tool(body=self.change())
        with patch.object(control.operations, 'locked', lock):
            self.assertEqual(self.confirm(self.pending()['code']).status_code, 200)

    def test_inventory_uses_real_relay_observer_without_agent_network_io(self):
        import relay_manager
        remote = Mock(side_effect=AssertionError('No remote request expected'))
        real = relay_manager.RelayManager(api.DATA_DIR, '1.1.1.1', client_factory=lambda *_: SimpleNamespace(host='8.8.8.8', request=remote))
        saved = copy.deepcopy(self.saved)
        saved.update(schema=1)
        saved['routes'][0]['target_ip'] = '1.1.1.1'
        real.write(saved)
        with patch.object(api, 'relay_control', return_value=real):
            response = self.tool('relay_read', {'view': 'inventory'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['hosts'][0]['state'], 'unknown')
        self.assertNotIn('secret-relay-token', response.text)
        remote.assert_not_called()

    def test_successful_operation_observation_unblocks_an_uncertain_admission(self):
        self.tool(body=self.change())
        record = self.pending()
        self.started.side_effect = api.HTTPException(503, 'Unknown admission')
        self.assertEqual(self.confirm(record['code']).status_code, 503)
        operation = api.DATA_DIR / 'operations' / (record['operation_id'] + '.json')
        control.operations.atomic_json(operation, {'id': record['operation_id'], 'state': 'running'})
        self.assertEqual(self.tool(body=self.change()).status_code, 409)
        control.operations.atomic_json(operation, {'id': record['operation_id'], 'state': 'succeeded'})
        self.assertEqual(self.tool(body=self.change()).status_code, 200)
        self.started.assert_called_once()

    def test_live_management_revocation_prevents_a_prepared_telegram_actors_change(self):
        value = control.read(api)
        value['accounts'] = [{'telegram_id': 11, 'name': 'Operator', 'role': 'administrator', 'products': ['assistant', 'mihomo'],
                              'permission_overrides': {'network_read': True, 'server_manage': True}}]
        control.store(api, value)
        headers = {**self.headers, 'X-MCP-Actor': '11'}
        self.assertEqual(self.tool(body=self.change(), headers=headers).status_code, 200)
        code = self.pending('11')['code']
        value['accounts'][0]['permission_overrides']['server_manage'] = False
        control.store(api, value)
        self.assertEqual(self.confirm(code, telegram_id=11).status_code, 403)
        self.started.assert_not_called()

    def test_confirm_persists_the_existing_workers_payload_under_the_same_operation_id(self):
        from pathlib import Path
        self.tool(body=self.change('private_transport', enabled=True))
        record = self.pending()
        with patch.object(api, 'start_relay_mutation', self.real_start), patch.object(api, 'start_application_operation', return_value={'id': record['operation_id'], 'state': 'queued'}) as admit:
            result = self.confirm(record['code'])
        self.assertEqual(result.status_code, 200, result.text)
        action, argv = admit.call_args.args
        self.assertEqual(action, 'relay-private-transport')
        self.assertEqual(admit.call_args.kwargs['operation_id'], record['operation_id'])
        persisted = control.operations.load_payload(api.DATA_DIR, record['operation_id'], Path(argv[4]), argv[5])
        self.assertEqual(persisted, {'host_id': self.host_id, 'expected_revision': self.revision, 'enabled': True})
