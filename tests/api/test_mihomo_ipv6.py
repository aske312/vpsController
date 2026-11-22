"""IPv6 offers, writes and existing subscription exports follow the server network."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT


class MihomoIpv6Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = {'available': False, 'state': 'unavailable', 'reason': 'IPv6 unavailable'}
        self.enterContext(patch.object(manager.server_addresses, 'ipv6_egress_state', side_effect=lambda: dict(self.state)))
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'SETTINGS_ROOT', self.root))
        for name, file in [('DNS_SETTINGS_FILE', 'dns.json'), ('ROUTING_SETTINGS_FILE', 'routing.json'),
                           ('PRESET_SETTINGS_FILE', 'presets.json'), ('PERSONAL_RULES_FILE', 'personal.json')]:
            self.enterContext(patch.object(manager, name, self.root / file))
        self.enterContext(patch.object(manager, 'profiles', return_value=[]))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='127.0.0.1'))

    def profile(self):
        return {'id': 'ipv6-fixture', 'common_device_id': 'common',
                'routing': {'dns_ipv6': True, 'strategy': 'select'},
                'devices': [{'id': 'common', 'routing': {'dns_ipv6': True}}],
                'connections': [{'id': 'ss', 'device_id': 'common', 'component': 'transport-shadowsocks',
                                 'credential': {'port': 18888, 'method': 'aes-128-gcm', 'password': 'test'}}]}

    def test_dns_and_profile_schema_publish_same_capability(self):
        for state in ('ready', 'unavailable', 'unknown'):
            with self.subTest(state=state):
                self.state.update(available=state == 'ready', state=state)
                self.assertEqual(manager.get_routing_schema()['server_ipv6'], self.state)
                self.assertEqual(manager.get_dns_settings()['server_ipv6'], self.state)

    def test_new_settings_cannot_enable_ipv6_without_confirmed_server_network(self):
        for state in ('unavailable', 'unknown', 'ready'):
            self.state.update(available=state == 'ready', state=state)
            with self.subTest(state=state):
                self.assertEqual(manager.validate_routing({'dns_ipv6': True}, current={})['dns_ipv6'], state == 'ready')
                self.assertEqual(manager.validate_dns({'ipv6': True})['ipv6'], state == 'ready')
                self.assertFalse(manager.validate_routing({'dns_ipv6': False}, current={})['dns_ipv6'])
                self.assertFalse(manager.validate_dns({'ipv6': False})['ipv6'])

    def test_existing_ipv6_preferences_are_safe_in_yaml_without_rewriting_profiles(self):
        item = self.profile()
        before = deepcopy(item)
        for available in (False, True, False):
            self.state.update(available=available, state='ready' if available else 'unavailable')
            with self.subTest(available=available):
                config = yaml.safe_load(manager.render_profile(item))
                self.assertEqual(config['ipv6'], available)
                self.assertEqual(config['dns']['ipv6'], available)
                self.assertEqual(item, before)
        self.assertFalse(list(self.root.iterdir()))

    def test_global_dns_ipv6_is_also_limited_at_export_and_schema_is_not_authorization(self):
        self.state.update(available=True, state='ready')
        self.assertTrue(manager.get_dns_settings()['server_ipv6']['available'])
        self.state.update(available=False, state='unavailable')
        base = {'ipv6': True, 'nameserver': '1.1.1.1', 'fallback': '8.8.8.8', 'enhanced_mode': 'fake-ip'}
        self.assertFalse(manager.client_dns_settings({}, base=base)['ipv6'])
        self.assertTrue(base['ipv6'])
        self.assertFalse(manager.validate_dns({'ipv6': True})['ipv6'])
