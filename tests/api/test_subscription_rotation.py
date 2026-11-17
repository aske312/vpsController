"""URL revocation preserves credentials, policies, usage and HWID identity."""
import copy
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from tests.api.support import manager


class SubscriptionRotationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, 'DATA_ROOT', root))
        self.enterContext(patch.object(manager, 'NETWORK_ENDPOINTS_FILE', root / 'routes.json'))
        self.metadata = {'client_name': 'Mihomo', 'os': 'android'}
        self.token = 'old-private-url-token'
        digest = hmac.new(self.token.encode(), b'fixture-hwid', hashlib.sha256).hexdigest()
        self.profile = {'id': 'own', 'name': 'Own', 'subscription_token': self.token, 'subscriptions': {},
                        'common_device_id': 'common', 'routing': {'structured_policy': '{"groups":[],"rules":[]}'},
                        'limits': {'traffic_gb': 20, 'speed_mbps': 50, 'days': 30},
                        'devices': [{'id': 'common', 'name': 'Common'},
                                    {'id': 'phone', 'name': 'Phone', 'hwid_hash': digest, **self.metadata,
                                     'routing': {'route_dns': True}, 'personal_rule_ids': ['personal-123']}],
                        'connections': [{'id': 'c', 'device_id': 'common', 'component': 'transport-wg',
                                         'settings': {'mtu': 1400}, 'credential': {'private_key': 'keep-private-key'}}]}
        self.rows = [copy.deepcopy(self.profile)]
        self.enterContext(patch.object(manager, 'profiles', side_effect=lambda: copy.deepcopy(self.rows)))
        self.save = self.enterContext(patch.object(manager, 'save_profiles', side_effect=lambda rows: self.rows.__setitem__(slice(None), copy.deepcopy(rows))))
        self.enterContext(patch.object(manager.subscription_limits, 'status', return_value={'state': 'active', 'used_bytes': 123}))

    def rotate(self, identity='a' * 32):
        return manager.rotate_profile_subscription('own', manager.profile_revision(self.rows[0]), identity)

    def test_rotation_preserves_settings_and_hwid_across_multiple_new_urls(self):
        revision = manager.profile_revision(self.rows[0])
        result = self.rotate()
        self.assertNotEqual(revision, result['revision'])
        self.assertNotEqual(self.rows[0]['subscription_token'], self.token)
        for key in ('id', 'name', 'routing', 'connections', 'devices', 'limits'):
            self.assertEqual(self.rows[0][key], self.profile[key])
        self.assertNotIn('device_identity_secret', result)
        self.assertNotIn(self.token, str(result))
        for identity in ('a' * 32, 'b' * 32):
            self.rotate(identity)
            profile, device = manager.subscription_device(self.rows[0], 'fixture-hwid', self.rows[0]['subscription_token'], self.metadata, read_only=True)
            self.assertEqual(device, 'phone')
            self.assertEqual(profile['devices'][1]['routing'], self.profile['devices'][1]['routing'])
            self.assertEqual(self.rows[0]['device_identity_secret'], self.token)
        self.assertEqual(self.save.call_count, 2)

    def test_replay_is_idempotent_and_stale_revision_never_changes_url(self):
        revision = manager.profile_revision(self.rows[0])
        self.rotate()
        token = self.rows[0]['subscription_token']
        manager.rotate_profile_subscription('own', revision, 'a' * 32)
        self.assertEqual(self.save.call_count, 1)
        with self.assertRaises(manager.HTTPException) as error:
            manager.rotate_profile_subscription('own', revision, 'c' * 32)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.rows[0]['subscription_token'], token)

    def test_old_common_and_device_urls_are_revoked_before_config_rendering(self):
        old_device = manager.device_subscription_token(self.token, 'phone')
        self.rotate()
        with patch.object(manager.subscription_signing, 'validate_nonce', return_value=None), \
             patch.object(manager.subscription_signing, 'server_signer', return_value=object()), \
             patch.object(manager, 'render_client_profile') as render:
            for token in (self.token, old_device):
                request = Request({'type': 'http', 'method': 'GET', 'path': '/s/' + token, 'headers': [], 'query_string': b''})
                with self.assertRaises(manager.HTTPException) as error:
                    manager.public_profile_subscription(token, request, allow_changes=False)
                self.assertEqual(error.exception.status_code, 404)
            render.assert_not_called()

    def test_new_url_is_returned_without_changing_profile_and_stale_admission_is_rejected(self):
        self.rotate()
        with patch.dict('os.environ', {'PUBLIC_DOMAIN': 'subscription.test'}):
            link = manager.profile_subscription('own')
        self.assertEqual(link['url'], 'https://subscription.test/s/' + self.rows[0]['subscription_token'])
        with patch.object(manager, 'start_profile_operation') as start:
            with self.assertRaises(manager.HTTPException):
                manager.submit_subscription_rotation('own', manager.ProfileRevisionInput(expected_revision='f' * 64), 'd' * 32)
            start.assert_not_called()
