"""Automatic MRS refresh, last-good retention and portable subscription delivery."""
from copy import deepcopy
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from tests.api.support import manager, ROOT
from tests.api.test_mihomo_rules_runtime import profile
from mrs_cache import MrsCache


class MrsCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = MrsCache(self.root, 'test-core', {
            'ads': {'url': 'https://feed.invalid/ads.yaml', 'format': 'yaml', 'interval': 3600}})
        self.download = self.enterContext(patch('mrs_cache.urllib.request.urlopen'))
        self.convert = self.enterContext(patch('mrs_cache.subprocess.run', side_effect=self.converter))
        self.set_source(b'payload:\n  - blocked.example\n')

    def set_source(self, source):
        self.download.side_effect = lambda *args, **kwargs: io.BytesIO(source)

    @staticmethod
    def converter(command, **kwargs):
        Path(command[-1]).write_bytes(b'test-mrs-artifact:' + Path(command[-2]).read_bytes())
        return subprocess.CompletedProcess(command, 0)

    def test_refresh_changed_and_unchanged_sources(self):
        self.assertTrue(self.cache.refresh('ads', now=10000))
        first = self.cache.read('ads')
        self.assertFalse(self.cache.refresh('ads', now=10001))
        self.assertEqual(self.download.call_count, 1)
        self.cache.refresh('ads', now=14000)
        self.assertEqual(self.convert.call_count, 1)  # Unchanged source is not recompiled.
        self.set_source(b'payload:\n  - new-blocked.example\n')
        self.cache.refresh('ads', now=18000)
        self.assertNotEqual(self.cache.read('ads'), first)
        self.assertEqual(self.convert.call_count, 2)
        # Persisted freshness survives a manager restart.
        restarted = MrsCache(self.root, 'test-core', self.cache.feeds)
        self.assertFalse(restarted.refresh('ads', now=18001))

    def test_failed_download_and_conversion_keep_last_good_version(self):
        self.cache.refresh('ads', now=10000)
        first = self.cache.read('ads')
        self.download.side_effect = OSError('upstream unavailable')
        with self.assertRaises(OSError):
            self.cache.refresh('ads', now=14000)
        self.assertEqual(self.cache.read('ads'), first)
        self.assertFalse(self.cache.refresh('ads', now=14001))  # Failure backoff.
        self.set_source(b'payload:\n  - new-blocked.example\n')
        self.convert.side_effect = subprocess.TimeoutExpired('test-core', 45)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.cache.refresh('ads', now=15000)
        self.assertEqual(self.cache.read('ads'), first)

    def test_invalid_sources_do_not_replace_cached_rules(self):
        self.cache.refresh('ads', now=10000)
        first = self.cache.read('ads')
        for source in (b'', b'<html>upstream error</html>', b'payload: []', b'payload: ["bad domain"]'):
            with self.subTest(source=source):
                self.set_source(source)
                with self.assertRaises(ValueError):
                    self.cache.refresh('ads', now=14000, force=True)
                self.assertEqual(self.cache.read('ads'), first)
        with self.assertRaises(KeyError):
            self.cache.refresh('../unknown', force=True)

    def test_http_delivery_is_cached_and_never_runs_the_converter(self):
        request = manager.Request({'type': 'http', 'headers': []})
        with patch.object(manager, 'MRS_CACHE', self.cache):
            with self.assertRaises(manager.HTTPException) as missing:
                manager.public_mrs_rules('ads', request)
            self.assertEqual(missing.exception.status_code, 503)
            self.download.assert_not_called()
            self.cache.refresh('ads', now=10000)
            result = manager.public_mrs_rules('ads', request)
            self.assertEqual(result.body, self.cache.read('ads'))
            request = manager.Request({'type': 'http', 'headers': [(b'if-none-match', result.headers['etag'].encode())]})
            self.assertEqual(manager.public_mrs_rules('ads', request).status_code, 304)
            self.assertEqual(self.convert.call_count, 1)
            with self.assertRaises(manager.HTTPException) as unknown:
                manager.public_mrs_rules('unknown', request)
            self.assertEqual(unknown.exception.status_code, 404)
            # Exercise routing through /s/*, without starting maintenance workers.
            client = TestClient(manager.app)
            fetched = client.get('/s/rules/ads.mrs')
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.content, self.cache.read('ads'))


class MrsExportTests(unittest.TestCase):
    def test_all_mihomo_platforms_and_common_profiles_use_mrs(self):
        item = profile()
        item['devices'][0]['routing'] = {'block_ads': True, 'block_malware': True}
        with patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'), \
                patch.object(manager, 'public_endpoint', return_value='127.0.0.1'), \
                patch.object(manager, 'module_is_installed', return_value=True), \
                patch.object(manager, 'component_is_managed', return_value=True), \
                patch.object(manager, 'MRS_CACHE'), \
                patch.dict(os.environ, {'PUBLIC_DOMAIN': 'panel.example'}):
            item['devices'][0]['routing']['mrs_rule_sets'] = False
            legacy = yaml.safe_load(manager.render_profile(deepcopy(item)))
            item['devices'][0]['routing'].pop('mrs_rule_sets')
            compact = yaml.safe_load(manager.render_profile(deepcopy(item)))
            self.assertEqual(compact['rules'], legacy['rules'])
            self.assertEqual(compact['proxies'], legacy['proxies'])
            self.assertEqual(len(compact['rule-providers']), 2)
            for name, definition in compact['rule-providers'].items():
                self.assertEqual(definition['format'], 'mrs')
                self.assertEqual(definition['url'], f'https://panel.example/s/rules/{name}.mrs')
                self.assertTrue(definition['path'].endswith('.mrs'))
                self.assertEqual(definition['interval'], legacy['rule-providers'][name]['interval'])
            for platform in ('ios', 'android', 'windows', 'macos', 'linux', 'unknown', None):
                with self.subTest(platform=platform):
                    item['devices'][0]['os'] = platform
                    self.assertEqual(yaml.safe_load(manager.render_profile(deepcopy(item))), compact)
            item['devices'][0]['routing']['mrs_rule_sets'] = False
            self.assertEqual(yaml.safe_load(manager.render_profile(deepcopy(item))), legacy)
            item['devices'][0]['routing'].pop('mrs_rule_sets')
            with patch.object(manager, 'component_is_managed', return_value=False):
                self.assertEqual(yaml.safe_load(manager.render_profile(deepcopy(item))), legacy)
            with patch.dict(os.environ, {'PUBLIC_DOMAIN': ''}):
                self.assertEqual(yaml.safe_load(manager.render_profile(deepcopy(item))), legacy)
        self.assertTrue(manager.validate_routing({'mrs_rule_sets': True}, current={})['mrs_rule_sets'])


    def test_export_waits_for_first_successful_build(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'PUBLIC_DOMAIN': 'panel.example'}), \
                patch.object(manager, 'MRS_CACHE', MrsCache(folder, 'test-core', {'ads': {}})):
            with self.assertRaises(manager.HTTPException) as error:
                manager.mrs_provider('ads', {'format': 'yaml'})
            self.assertEqual(error.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
