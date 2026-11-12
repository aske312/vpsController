import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
import assistant_control as control
import assistant_provider_observations as observations


class ProviderObservationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = types.SimpleNamespace(DATA_DIR=self.root)
        self.identity = 'a' * 32
        self.settings = {'revision': 'b' * 32, 'providers': [{'id': self.identity, 'provider_url': 'https://provider.example/v1', 'api_key': 'private-key'}], 'accounts': []}
        self.enterContext(patch.object(control, '_read', return_value=self.settings))
        self.result = {'provider_info': {'checked_at': 1700000000, 'catalog_latency_ms': 12, 'models_count': 43, 'catalog_http_status': 200},
                       'quota': {'remaining': 0, 'used': 10, 'unlimited': False}}

    def test_snapshot_survives_page_reload_is_non_secret_and_invalidates_on_config_change(self):
        observations.save(self.env, self.settings, self.identity, self.result, quota_requested=True)
        data = observations.read(self.env, self.settings)
        self.assertEqual(data[self.identity]['quota']['remaining'], 0)
        text = (self.root / 'assistant/ai-agent/provider-observations.json').read_text()
        self.assertNotIn('private-key', text)
        self.assertNotIn('provider.example', text)
        self.assertEqual(observations.read(self.env, {**self.settings, 'revision': 'c' * 32}), {})
        self.assertEqual(observations.read(self.env, {**self.settings, 'providers': []}), {})

    def test_catalog_only_refresh_preserves_original_quota_timestamp_and_failed_optional_probe_clears_it(self):
        observations.save(self.env, self.settings, self.identity, self.result, quota_requested=True)
        newer = {'provider_info': {**self.result['provider_info'], 'checked_at': 1700000060}}
        observations.save(self.env, self.settings, self.identity, newer, quota_requested=False)
        row = observations.read(self.env, self.settings)[self.identity]
        self.assertEqual(row['quota_checked_at'], 1700000000)
        self.assertEqual(row['provider_info']['checked_at'], 1700000060)
        observations.save(self.env, self.settings, self.identity, {**newer, 'quota': None}, quota_requested=True)
        self.assertIsNone(observations.read(self.env, self.settings)[self.identity]['quota'])

    def test_unexpected_provider_fields_are_never_written(self):
        result = {'provider_info': {**self.result['provider_info'], 'secret': 'private-key'},
                  'quota': {**self.result['quota'], 'name': 'private-name', 'key': 'private-key'}}
        observations.save(self.env, self.settings, self.identity, result, quota_requested=True)
        self.assertNotIn('private', (self.root / 'assistant/ai-agent/provider-observations.json').read_text())

    def test_catalog_api_caches_only_saved_matching_credentials(self):
        self.enterContext(patch.object(api, 'DATA_DIR', self.root))
        self.enterContext(patch.object(control, 'read', return_value=self.settings))
        self.enterContext(patch.object(control, 'require_installed'))
        self.enterContext(patch.object(control.assistant_provider, 'catalog', return_value=['model']))
        self.enterContext(patch.object(control.assistant_provider, 'token_quota', return_value={'remaining': 5}))
        payload = control.Catalog(provider_url='https://provider.example/v1', provider_id=self.identity, include_quota=True)
        result = control.model_catalog(api, payload)
        self.assertEqual(result['provider_info']['models_count'], 1)
        self.assertIn(self.identity, observations.read(self.env, self.settings))
        with patch.object(observations, 'save') as save:
            control.model_catalog(api, payload.model_copy(update={'api_key': 'draft-key'}))
            save.assert_not_called()
