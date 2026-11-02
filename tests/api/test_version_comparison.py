import unittest
from unittest.mock import patch

from tests.api.support import api, manager
from version_comparison import newer_release


class VersionComparisonTests(unittest.TestCase):
    def test_upstream_releases_require_forward_semver_order(self):
        cases = [
            ('1.14.3', '1.14.2', False), ('1.14.3', '1.14.3', False),
            ('1.14.3+local', 'v1.14.3', False), ('1.9.0', '1.10.0', True),
            ('1.14.3', '2.0.0', True), ('2.0.0', '1.99.0', False),
            ('1.14.3-beta.2', '1.14.3-beta.10', True),
            ('1.14.3-beta.10', '1.14.3-beta.2', False),
            ('1.14.3-rc.1', '1.14.3', True), ('1.14.3', '1.14.3-rc.1', False),
            ('1.14.3-1', '1.14.3-alpha', True), ('1.14.3-alpha', '1.14.3-alpha.1', True),
            ('unknown', '1.14.3', False), ('1.14.3', '', False),
            ('1.14.3', '1.14.4-01', False),
        ]
        for installed, available, expected in cases:
            with self.subTest(installed=installed, available=available):
                self.assertIs(newer_release(installed, available), expected)

    def test_direct_and_mihomo_do_not_offer_downgrades(self):
        for candidate, expected, breaking in [('1.14.2', False, False), ('1.14.3', False, False),
                                               ('1.14.4', True, False), ('2.0.0', True, True)]:
            with self.subTest(candidate=candidate), \
                 patch.object(api, 'standalone_binary_version', return_value='1.14.3'), \
                 patch.object(api, 'github_latest_tag', return_value=candidate), \
                 patch.object(manager, 'singbox_installed_version', return_value='1.14.3'), \
                 patch.object(manager, 'github_latest_tag', return_value=candidate):
                direct = api.protocol_version_info({}, 'tuic', True)
                mihomo = manager.module_version_info('transport-tuic', {}, True)
                for result in (direct, mihomo):
                    self.assertEqual((result['update_available'], result['update_breaking']), (expected, breaking))
