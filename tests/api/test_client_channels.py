"""Public channel commands are confined to an existing subscription device."""
from copy import deepcopy
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import manager
from tests.api import test_subscription_formats as subscription_formats


class ClientChannelTests(unittest.TestCase):
    def setUp(self):
        subscription_formats.SubscriptionFormatTests.setUp(self)
        self.enterContext(patch.object(manager, 'module_is_ready', return_value=False))

    fetch = subscription_formats.SubscriptionFormatTests.fetch

    def register(self, hwid="pc"):
        self.fetch("Nodera/0.0.4 (Windows; Mihomo)", hwid,
                   extra_headers=((b"x-client-name", b"Nodera"), (b"x-device-os", b"windows")))
        return self.store[0]["devices"][-1]["id"]

    def request(self, token="test-token", hwid="pc", **payload):
        return TestClient(manager.app).post(f"/s/{token}/commands/select-channels", json=payload,
            headers={"X-Device-ID": hwid, "X-Client-Name": "Nodera", "X-Device-OS": "windows",
                     "User-Agent": "Nodera/0.0.4 (Windows; Mihomo)"})

    def test_registered_hwid_without_new_routes_does_not_mutate(self):
        device_id = self.register()
        before = deepcopy(self.store)
        with patch.object(manager, "render_client_profile", return_value=("proxies: []", "yaml")) as render, \
                patch.object(manager, "save_profiles") as save:
            response = self.request(require_udp=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["command"], "select-channels")
        self.assertTrue(data["require_udp"])
        self.assertGreater(data["expires_at"], time.time())
        self.assertEqual(response.headers["cache-control"], "no-store")
        render.assert_called_once()
        self.assertEqual(render.call_args.args[1], device_id)
        save.assert_not_called()
        self.assertEqual(before, self.store)

    def test_missing_unknown_hwid_and_other_subscription_are_denied_without_registration(self):
        self.register()
        before = deepcopy(self.store)
        with patch.object(manager, "render_client_profile") as render:
            for hwid in ("", "new-pc", "x" * 257):
                self.assertEqual(self.request(hwid=hwid).status_code, 403)
            self.assertEqual(self.request(token="wrong").status_code, 404)
        render.assert_not_called()
        self.assertEqual(before, self.store)

    def test_bound_token_and_hwid_cannot_select_another_device(self):
        first = self.register("first")
        second = self.register("second")
        token = manager.device_subscription_token("test-token", first)
        with patch.object(manager, "render_client_profile", return_value=("test", "yaml")) as render:
            self.assertEqual(self.request(token=token, hwid="second").status_code, 403)
            self.assertEqual(self.request(token=token, hwid="first").status_code, 200)
            self.assertEqual(render.call_args.args[1], first)
        self.assertNotEqual(first, second)

    def test_hwid_is_scoped_to_subscription_secret(self):
        self.register()
        other = deepcopy(self.store[0])
        other.update(id="other", subscription_token="other-token")
        self.store.append(other)
        expected = hmac.new(b"test-token", b"pc", hashlib.sha256).hexdigest()
        self.assertEqual(other["devices"][-1]["hwid_hash"], expected)
        self.assertEqual(self.request(token="other-token").status_code, 403)

    def test_expired_or_exhausted_subscription_cannot_issue_command(self):
        self.register()
        with patch.object(manager.subscription_limits, "status", return_value={"state": "expired"}), \
                patch.object(manager, "render_client_profile") as render:
            self.assertEqual(self.request().status_code, 403)
            render.assert_not_called()

    def test_arbitrary_commands_are_rejected(self):
        self.register()
        self.assertEqual(self.request(shell="anything").status_code, 422)

    def test_real_export_contains_only_the_registered_devices_credentials(self):
        device_id = self.register()
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        config = response.json()["config"]
        self.assertIn(device_id, config)
        self.assertNotIn("test-only", config)
        self.assertNotIn("test-token", json.dumps(response.json()))

    def test_busy_and_failed_render_release_capacity_without_leaking_errors(self):
        self.register()
        with patch.object(manager, "client_channel_slots") as slots:
            slots.acquire.return_value = False
            self.assertEqual(self.request().status_code, 429)
            slots.release.assert_not_called()
        with patch.object(manager, "render_client_profile", side_effect=ValueError("private-secret")):
            response = self.request()
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("private-secret", response.text)
        with patch.object(manager, "render_client_profile", return_value=("test", "yaml")):
            self.assertEqual(self.request().status_code, 200)

    def lifecycle(self):
        self.register()
        # Two real, device-owned exports; no common credentials are selected.
        device_id = self.store[0]['devices'][-1]['id']
        connection = deepcopy(self.store[0]['connections'][-1])
        connection.update(id='second-ss', name='second')
        connection['credential']['port'] = 12002
        self.store[0]['connections'].append(connection)
        candidate = self.request().json()
        import yaml
        config = yaml.safe_load(candidate['config'])
        names = [node['name'] for node in config['proxies']]
        payload = {'operation_id': 'a' * 32, 'revision': candidate['revision'],
                   'source_digest': manager.client_channels.digest(config), 'duration': 30,
                   'require_udp': True, 'reports': [
                       {'name': name, 'state': 'passed' if index == 0 else 'failed',
                        'seconds': 30, 'transfers': 3, 'bytes': 786432, 'udp': True}
                       for index, name in enumerate(names)]}
        return config, payload, device_id

    def command(self, suffix, payload=None, hwid='pc'):
        client = TestClient(manager.app)
        headers = {'X-Device-ID': hwid, 'X-Client-Name': 'Nodera', 'X-Device-OS': 'windows',
                   'User-Agent': 'Nodera/0.0.4 (Windows; Mihomo)'}
        url = '/s/test-token/commands/select-channels' + suffix
        return client.get(url, headers=headers) if payload is None else client.post(url, headers=headers, json=payload)

    def test_prepare_changes_public_subscription_and_reject_restores_original(self):
        config, payload, device = self.lifecycle()
        before = deepcopy(self.store)
        prepared = self.command('/prepare', payload)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(prepared.json()['state'], 'pending')
        import yaml
        public = yaml.safe_load(self.fetch('Nodera/0.0.4 (Windows; Mihomo)', 'pc',
            extra_headers=((b'x-client-name', b'Nodera'), (b'x-device-os', b'windows'))).body)
        response = self.fetch('Nodera/0.0.4 (Windows; Mihomo)', 'pc',
            extra_headers=((b'x-client-name', b'Nodera'), (b'x-device-os', b'windows')))
        self.assertEqual(response.headers['x-nodera-selection-state'], 'pending')
        self.assertEqual(len(public['proxies']), 1)
        self.assertEqual(len(config['proxies']), 2)
        self.assertEqual(len(self.request().json()['config']) > 0, True)
        rejected = self.command('/' + payload['operation_id'] + '/decision', {'action': 'reject'})
        self.assertEqual(rejected.json()['state'], 'rejected')
        current, _ = manager.render_client_profile(self.store[0], device)
        self.assertEqual(len(yaml.safe_load(current)['proxies']), 2)
        self.assertEqual(before[0]['connections'], self.store[0]['connections'])

    def test_accept_idempotency_restart_and_explicit_rollback(self):
        config, payload, device = self.lifecycle()
        self.assertEqual(self.command('/prepare', payload).status_code, 200)
        self.assertEqual(self.command('/prepare', payload).json()['operation_id'], payload['operation_id'])
        suffix = '/' + payload['operation_id'] + '/decision'
        for _ in range(2):
            self.assertEqual(self.command(suffix, {'action': 'accept'}).json()['state'], 'accepted')
        # State is read afresh from disk and survives service/process restarts.
        record = manager.application_operation.read(manager.client_channels.path(manager.DATA_ROOT, 'profile', device))
        self.assertEqual(record['state'], 'accepted')
        self.assertEqual(self.command('/' + payload['operation_id']).json()['state'], 'accepted')
        for _ in range(2):
            self.assertEqual(self.command(suffix, {'action': 'rollback'}).json()['state'], 'rolled_back')
        self.assertEqual(self.command(suffix, {'action': 'accept'}).status_code, 409)

    def test_expiry_and_new_decision_restore_previous_accepted_selection(self):
        config, payload, device = self.lifecycle()
        self.command('/prepare', payload)
        self.command('/' + payload['operation_id'] + '/decision', {'action': 'accept'})
        second = deepcopy(payload)
        second['operation_id'] = 'b' * 32
        for report in second['reports']:
            report['state'] = 'passed'
        self.assertEqual(self.command('/prepare', second).status_code, 200)
        import yaml
        with patch.object(manager.client_channels.time, 'time', return_value=time.time() + 1801):
            status = self.command('/' + second['operation_id']).json()
            self.assertEqual(status['state'], 'expired')
            self.assertEqual(len(yaml.safe_load(status['config'])['proxies']), 1)

    def test_stale_reports_short_tests_other_device_and_busy_updates_cannot_commit(self):
        config, payload, device = self.lifecycle()
        for key, bad in [('source_digest', '0' * 64), ('revision', '0' * 64)]:
            wrong = deepcopy(payload)
            wrong[key] = bad
            self.assertEqual(self.command('/prepare', wrong).status_code, 409)
        wrong = deepcopy(payload)
        wrong['reports'][0]['seconds'] = 29
        self.assertEqual(self.command('/prepare', wrong).status_code, 409)
        self.assertEqual(self.command('/prepare', payload, hwid='unregistered').status_code, 403)
        with patch.object(manager, 'component_is_managed', return_value=False):
            self.assertEqual(self.command('/prepare', payload).status_code, 409)
        self.assertFalse(manager.client_channels.path(manager.DATA_ROOT, 'profile', device).exists())

    def test_admin_credential_change_invalidates_prepared_selection(self):
        config, payload, device = self.lifecycle()
        self.command('/prepare', payload)
        self.store[0]['connections'][-1]['credential']['password'] = 'rotated'
        self.assertEqual(self.command('/' + payload['operation_id']).json()['state'], 'stale')
        self.assertEqual(self.command('/' + payload['operation_id'] + '/decision', {'action': 'accept'}).status_code, 409)

    def test_new_route_is_provisioned_once_and_only_selected_after_client_report(self):
        import yaml
        device = self.register()
        before, _ = manager.render_client_profile(self.store[0], device)
        calls = self.provision.call_count
        with patch.object(manager, 'module_is_ready', side_effect=lambda component: component == 'transport-shadowsocks'):
            candidate = self.request()
            self.assertEqual(candidate.status_code, 200, candidate.text)
            data = candidate.json()
            nodes = yaml.safe_load(data['config'])['proxies']
            self.assertEqual(len(nodes), 2)
            self.assertEqual(self.provision.call_count, calls + 1)
            created = [row for row in self.store[0]['connections'] if row.get('selection_candidate')]
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0]['device_id'], device)
            self.assertEqual(manager.render_client_profile(self.store[0], device)[0], before)
            self.assertEqual(self.request().json()['config'], data['config'])
            self.assertEqual(self.provision.call_count, calls + 1)
        names = [row['name'] for row in nodes]
        payload = {'operation_id': 'c' * 32, 'revision': data['revision'],
                   'source_digest': manager.client_channels.digest(yaml.safe_load(data['config'])),
                   'duration': 30, 'require_udp': False,
                   'reports': [{'name': name, 'state': 'passed' if i == 1 else 'failed',
                                'seconds': 30, 'transfers': 3, 'bytes': 786432}
                               for i, name in enumerate(names)]}
        prepared = self.command('/prepare', payload)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual([row['name'] for row in yaml.safe_load(prepared.json()['config'])['proxies']], [names[1]])
        suffix = '/' + payload['operation_id'] + '/decision'
        self.assertEqual(self.command(suffix, {'action': 'accept'}).json()['state'], 'accepted')
        rolled_back = self.command(suffix, {'action': 'rollback'})
        self.assertEqual(rolled_back.json()['config'], before)
        self.assertEqual(manager.render_client_profile(self.store[0], device)[0], before)

    def test_route_inventory_uses_active_bindings_and_installed_running_modules(self):
        device = self.register()
        registry = {'schema': 1, 'bindings': [
            {'state': 'active', 'kind': 'cdn', 'address': 'cdn.example.com'},
            {'state': 'retired', 'kind': 'tls_relay', 'address': 'retired.example.com'}]}
        with patch.object(manager.network_routes, 'migrate', return_value=registry), \
                patch.object(manager, 'module_is_installed', side_effect=lambda module: module == 'transport-reality'), \
                patch.object(manager, 'module_is_ready', return_value=True), \
                patch.object(manager, 'validate_connection', side_effect=lambda module, values: values):
            definitions = manager.client_route_definitions(self.store[0], device)
        self.assertEqual(len(definitions), 15)
        self.assertEqual({row['component'] for row in definitions}, {'transport-reality'})
        self.assertEqual({row['settings']['transport'] for row in definitions if row['settings']['route_mode'] == 'direct'},
                         {'xhttp', 'raw', 'grpc'})
        self.assertFalse(any('retired.example.com' in str(row) for row in definitions))
        self.assertTrue(all(row['device_id'] == device for row in definitions))

    def test_candidate_failure_does_not_save_profile_and_busy_mutations_are_denied(self):
        self.register()
        before = deepcopy(self.store)
        with patch.object(manager, 'component_is_managed', return_value=False):
            self.assertEqual(self.request().status_code, 409)
        with patch.object(manager, 'module_is_ready', side_effect=lambda component: component == 'transport-shadowsocks'), \
                patch.object(manager, 'render_client_profile', side_effect=ValueError('private-secret')), \
                patch.object(manager, 'save_subscription_profile') as save:
            response = self.request()
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('private-secret', response.text)
            save.assert_not_called()
        self.assertEqual(self.store, before)

    def test_extending_catalog_preserves_accepted_routes_and_pending_catalog_is_frozen(self):
        config, payload, device = self.lifecycle()
        self.command('/prepare', payload)
        calls = self.provision.call_count
        with patch.object(manager, 'module_is_ready', side_effect=lambda component: component == 'transport-shadowsocks'):
            self.assertEqual(self.request().status_code, 200)
            self.assertEqual(self.provision.call_count, calls)
            self.command('/' + payload['operation_id'] + '/decision', {'action': 'accept'})
            before, _ = manager.render_client_profile(self.store[0], device)
            candidate = self.request()
            self.assertEqual(candidate.status_code, 200, candidate.text)
            self.assertEqual(self.provision.call_count, calls + 1)
        self.assertEqual(manager.render_client_profile(self.store[0], device)[0], before)
        self.assertEqual(self.command('/' + payload['operation_id']).json()['state'], 'accepted')

    def test_relay_proposals_and_catalog_size_are_bounded(self):
        device = self.register()
        bindings = [{'state': 'active', 'kind': 'cdn', 'address': f'cdn{i}.example.com'} for i in range(20)]
        with patch.object(manager.network_routes, 'migrate', return_value={'bindings': bindings}), \
                patch.object(manager, 'module_is_installed', side_effect=lambda module: module == 'transport-reality'), \
                patch.object(manager, 'module_is_ready', return_value=True), \
                patch.object(manager, 'validate_connection', side_effect=lambda module, values: values):
            self.assertEqual(len(manager.client_route_definitions(self.store[0], device)), 63)
        with patch.object(manager.network_routes, 'migrate', return_value={'bindings': []}), \
                patch.object(manager, 'relay_connection_control') as relay, \
                patch.object(manager, 'module_is_installed', side_effect=lambda module: module == 'transport-shadowsocks'), \
                patch.object(manager, 'module_is_ready', return_value=True), \
                patch.object(manager, 'validate_connection', side_effect=lambda module, values: values):
            relay.return_value.read.return_value = {'hosts': [{'id': 'a' * 32}]}
            proposals = manager.client_route_definitions(self.store[0], device)
        self.assertTrue(any(row['settings'].get('relay_host_id') == 'a' * 32 for row in proposals))
