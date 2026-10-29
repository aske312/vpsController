import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.api.support import api


class ApplicationVersionStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = self.root / 'version.json'
        self.enterContext(patch.object(api, 'APP_VERSION_FILE', self.cache))
        self.enterContext(patch.object(api, 'installed_build_commit', return_value='b' * 40))
        self.enterContext(patch.object(api, 'expected_application_branch', return_value='test-pro'))
        self.thread = self.enterContext(patch.object(api.threading, 'Thread'))
        self.valid = {'branch': 'test-pro', 'current_commit': 'b' * 40, 'latest_commit': 'b' * 12,
                      'outdated': False, 'checked_at': '2026-10-29T12:00:00+00:00', 'error': ''}

    def test_installed_identity_is_current_while_obsolete_comparison_refreshes(self):
        self.cache.write_text(json.dumps({**self.valid, 'branch': 'pro', 'current_commit': 'a' * 40}))
        value = api.application_version_status()
        self.assertEqual((value['branch'], value['current_commit']), ('test-pro', 'b' * 40))
        self.assertEqual((value['latest_commit'], value['outdated'], value['checked_at']), ('', None, None))
        self.assertTrue(value['refreshing'])
        self.thread.return_value.start.assert_called_once()

    def test_unusable_cache_cannot_crash_or_claim_an_update_comparison(self):
        for cached in ([], None, 12, {'branch': 'test-pro', 'current_commit': 'b' * 40, 'outdated': 'false'}):
            with self.subTest(cached=cached):
                self.cache.write_text(json.dumps(cached))
                value = api.application_version_status()
                self.assertEqual(value['current_commit'], 'b' * 40)
                self.assertIsNone(value['outdated'])

    def test_expired_or_future_cache_cannot_confirm_current(self):
        for age in (601, -60):
            with self.subTest(age=age):
                self.cache.write_text(json.dumps(self.valid))
                stamp=time.time()-age;os.utime(self.cache,(stamp,stamp))
                value=api.application_version_status()
                self.assertTrue(value['refreshing'])
                self.assertIsNone(value['outdated'])

    def test_fresh_comparison_for_this_installation_is_preserved(self):
        self.cache.write_text(json.dumps(self.valid))
        value=api.application_version_status()
        self.assertFalse(value['refreshing'])
        self.assertIs(value['outdated'], False)
        self.thread.return_value.start.assert_not_called()
