import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('published_release', ROOT / 'scripts/verify-published-release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublishedReleaseTests(unittest.TestCase):
    def release(self):
        tag = 'pro-test-latest'
        return {'draft': False, 'tag_name': tag, 'target_commitish': 'a' * 40, 'published_at': '2026-11-23T07:45:34Z',
                'assets': [{'name': f'vps-control-pro-linux-{arch}.tar.gz', 'state': 'uploaded', 'size': 1024,
                            'digest': 'sha256:' + 'b' * 64,
                            'browser_download_url': f'https://github.com/aske312/vpsController/releases/download/{tag}/vps-control-pro-linux-{arch}.tar.gz'}
                           for arch in ('amd64', 'arm64')]}

    def test_installer_discoverable_candidate_and_production_are_accepted(self):
        release = self.release()
        module.validate_release(release, 'pro-test-latest', 'a' * 40)
        release['tag_name'] = 'pro-latest'
        for asset in release['assets']:
            asset['browser_download_url'] = asset['browser_download_url'].replace('pro-test-latest', 'pro-latest')
        module.validate_release(release, 'pro-latest', 'a' * 40)

    def test_successful_upload_does_not_make_a_draft_or_foreign_commit_a_release(self):
        for changes in ({'draft': True}, {'draft': None}, {'target_commitish': 'c' * 40},
                        {'published_at': None}, {'tag_name': 'pro-latest'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                module.validate_release({**self.release(), **changes}, 'pro-test-latest', 'a' * 40)

    def test_draft_asset_links_incomplete_uploads_and_missing_architectures_are_rejected(self):
        changes = [{'browser_download_url': 'https://github.com/aske312/vpsController/releases/download/untagged-123/archive.tar.gz'},
                   {'state': 'new'}, {'size': 0}, {'size': True}, {'digest': None}, {'digest': 'sha256:bad'}]
        for fields in changes:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                release = self.release()
                release['assets'][0].update(fields)
                module.validate_release(release, 'pro-test-latest', 'a' * 40)
        for assets in (self.release()['assets'][:1], [self.release()['assets'][0]] * 2, None):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                release = copy.deepcopy(self.release()); release['assets'] = assets
                module.validate_release(release, 'pro-test-latest', 'a' * 40)
