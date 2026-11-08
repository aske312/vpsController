import json
import os
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.api.support import api, manager
from component_registry import ComponentRegistry
import subscription_access as access


class SubscriptionAccessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'mihomo').mkdir()
        ComponentRegistry(self.root).write_receipt('mihomo', 'installed')
        (self.root / 'mihomo/state.json').write_text(json.dumps({'modules': {'transport-reality': True}}))
        self.profile = {'id': 'profile', 'subscription_token': 'secret-token', 'devices': [{'id': 'phone'}],
                        'connections': [{'id': 'channel', 'component': 'transport-reality', 'device_id': 'phone',
                                         'credential': {'uuid': 'secret-uuid'}}]}
        self.observe = lambda _: {'active': True}

    def channels(self, profiles=None):
        return access.available(self.root, profiles=[self.profile] if profiles is None else profiles, observe=self.observe)

    def test_active_subscription_needs_no_direct_files_or_clients(self):
        with patch.object(api, 'read_clients', side_effect=AssertionError('Direct must not be read')):
            self.assertEqual(self.channels(), ['transport-reality'])
        self.assertEqual(self.channels([]), [])
        self.assertEqual(access.available(self.root, profiles=[self.profile], excluded={'transport-reality'}, observe=self.observe), [])

    def test_missing_runtime_ownership_device_token_or_credentials_cannot_close_panel(self):
        for key in ('subscription_token', 'devices', 'connections'):
            with self.subTest(key=key):
                profile = deepcopy(self.profile)
                profile.pop(key)
                self.assertEqual(self.channels([profile]), [])
        self.assertEqual(access.available(self.root, profiles=[self.profile], observe=lambda _: {'active': None}), [])
        with patch.object(access.ComponentRegistry, 'management', return_value={'state': 'unknown'}):
            self.assertEqual(self.channels(), [])

    def test_expired_exhausted_or_unobserved_limits_are_not_an_access_path(self):
        for state in ('expired', 'exhausted', 'unknown'):
            with self.subTest(state=state), patch.object(access.limits(), 'status', return_value={'state': state}):
                self.assertEqual(self.channels(), [])
        self.profile.update({'limits': {'days': 1}, 'limits_started_at': int(time.time()) - 90000})
        self.assertEqual(self.channels(), [])

    def test_gateway_trusts_only_isolated_mihomo_ranges_and_server_hairpin(self):
        with patch.object(access, 'interface_addresses', return_value={'mh-wg0': {'address': '10.82.0.1', 'subnet': '10.82.0.0/24'}}):
            values = access.gateway_values({'PUBLIC_IPV4': '203.0.113.1', 'WG_SUBNET': '10.7.0.0/24'})
        self.assertEqual(values['PANEL_SUBSCRIPTION_SOURCES'], '127.0.0.0/8 ::1/128 203.0.113.1 10.82.0.0/24')
        self.assertNotIn('10.0.0.0/8', values['PANEL_SUBSCRIPTION_SOURCES'])

    def test_protected_panel_blocks_removal_of_last_subscription_path(self):
        with patch.dict(os.environ, {'ACCESS_MODE': 'vpn'}), patch.object(manager.subscription_access, 'available', return_value=[]):
            with self.assertRaises(manager.HTTPException) as denied:
                manager.require_subscription_panel_path([])
            self.assertEqual(denied.exception.status_code, 409)
        with patch.dict(os.environ, {'ACCESS_MODE': 'external'}):
            manager.require_subscription_panel_path([])

    def test_last_transport_and_manager_cannot_be_stopped_with_panel_closed(self):
        services = {'mihomo': {'controls': ['stop']}, 'mihomo-transport-reality': {'controls': ['stop']}}
        with patch.dict(os.environ, {'ACCESS_MODE': 'vpn'}), patch.object(api, 'managed_services', return_value=services), patch.object(access, 'available', return_value=[]):
            for service in services:
                with self.subTest(service=service), self.assertRaises(api.HTTPException) as denied:
                    api.service_action_definition(service, api.ServiceAction(action='stop'))
                self.assertEqual(denied.exception.status_code, 409)
        with patch.dict(os.environ, {'ACCESS_MODE': 'vpn'}), patch.object(api, 'managed_services', return_value=services), patch.object(access, 'available', return_value=['transport-wg']):
            self.assertEqual(api.service_action_definition('mihomo-transport-reality', api.ServiceAction(action='stop')), services['mihomo-transport-reality'])

    def test_relay_rejects_retired_consumers_before_reading_direct_state(self):
        with patch.object(api, 'read_clients', side_effect=AssertionError('Direct must not be read')):
            with self.assertRaises(api.HTTPException) as denied:
                api.start_relay_mutation('relay-route-check', {'consumer_id': 'direct:phone'}, None)
            self.assertEqual(denied.exception.status_code, 410)
            for prepare in (api.prepare_relay_probe, api.prepare_relay_media_probe):
                with self.assertRaises(api.network_probe.ProbeUnavailable):
                    prepare({}, {}, 'direct:phone')
