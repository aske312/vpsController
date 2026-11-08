"""Retiring standalone Direct must leave subscription state and exports intact."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api, manager
from tests.api import test_subscription_formats as subscription_formats


class DirectRetirementTests(unittest.TestCase):
    setUp = subscription_formats.SubscriptionFormatTests.setUp
    fetch = subscription_formats.SubscriptionFormatTests.fetch

    def test_retired_http_entrypoints_cannot_mutate_subscription_or_export_credentials(self):
        self.fetch('ClashMeta/1.0', 'phone')
        before = deepcopy(self.store)
        device = self.store[0]['devices'][-1]['id']
        exported = manager.render_client_profile(self.store[0], device)
        cases = [('GET', '/api/clients'), ('POST', '/api/clients'),
                 ('POST', '/api/clients/native'), ('GET', '/api/clients/native/' + 'a' * 32),
                 ('POST', '/api/clients/relay'), ('GET', '/api/clients/relay/' + 'a' * 32),
                 ('POST', '/api/clients/relay/' + 'a' * 32 + '/resume'),
                 ('DELETE', '/api/clients/existing')]
        for protocol in api.DIRECT_IMAGE_IDS:
            cases.extend([('POST', f'/api/protocol-images/{protocol}/install'),
                          ('POST', f'/api/protocol-images/{protocol}/update'),
                          ('DELETE', f'/api/protocol-images/{protocol}'),
                          ('POST', f'/api/protocol-images/{protocol}/purge'),
                          ('GET', f'/api/protocol-images/{protocol}/adoption'),
                          ('POST', f'/api/protocol-images/{protocol}/adoption'),
                          ('GET', f'/api/protocols/{protocol}/status'),
                          ('PATCH', f'/api/protocols/{protocol}/settings'),
                          ('POST', f'/api/protocols/{protocol}/restart'),
                          ('POST', f'/api/protocols/{protocol}/diagnostics/check'),
                          ('POST', f'/api/protocols/{protocol}/resources/check')])
        client = TestClient(api.app)
        with patch.object(api, 'ADMIN_USER', 'test'), patch.object(api, 'ADMIN_PASSWORD', 'secret'), \
                patch.object(api, 'start_application_operation', side_effect=AssertionError('Direct started an operation')), \
                patch.object(api, 'run', side_effect=AssertionError('Direct ran a command')):
            for method, path in cases:
                with self.subTest(method=method, path=path):
                    self.assertEqual(client.request(method, path, json={}).status_code, 401)
                    result = client.request(method, path, json={}, auth=('test', 'secret'))
                    self.assertEqual(result.status_code, 410, result.text)
        self.assertEqual(self.store, before)
        self.assertEqual(manager.render_client_profile(self.store[0], device), exported)

    def test_catalog_preserves_mihomo_and_hides_standalone_images(self):
        images = {identity: {'id': identity} for identity in api.DIRECT_IMAGE_IDS | {'mihomo'}}
        with patch.object(api, 'protocol_image_manifests', return_value=images):
            self.assertEqual(api.protocol_images()['items'], [{'id': 'mihomo'}])
        # Direct and subscription module identities are deliberately different.
        for module in ('mihomo', 'transport-wg', 'transport-awg', 'transport-reality',
                       'transport-shadowsocks', 'transport-hysteria2', 'transport-tuic'):
            api.require_subscription_component(module)
