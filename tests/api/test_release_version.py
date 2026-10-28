"""Test and production publish the same version without moving pro back to test."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.api.support import ROOT

spec = importlib.util.spec_from_file_location('release_version', ROOT / 'scripts/release-version.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.git('init', '--quiet')
        (self.root / 'package.json').write_text(json.dumps({'version': '0.2.6'}))
        self.commit('initial')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Version test', '-c', 'user.email=test@example.invalid', *args],
                                       cwd=self.root, text=True, stderr=subprocess.STDOUT).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '--quiet', '--allow-empty', '-m', message)

    def test_initial_and_unchanged_production_use_existing_version(self):
        self.assertEqual(module.release_version(self.root), 'v0.2.6')
        self.git('tag', 'pro-v0.2.31')
        self.assertEqual(module.release_version(self.root), 'v0.2.31')
        self.commit('metadata-only merge')
        self.assertEqual(module.release_version(self.root), 'v0.2.31')

    def test_candidate_and_its_production_merge_have_the_same_next_version(self):
        self.git('tag', 'pro-v0.2.31')
        (self.root / 'fix.txt').write_text('VK route')
        self.commit('test candidate')
        candidate = self.git('rev-parse', 'HEAD')
        self.assertEqual(module.release_version(self.root, candidate), 'v0.2.32')
        self.git('tag', 'pro-test-latest')
        self.commit('production merge')
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), candidate)
        self.assertEqual(module.release_version(self.root), 'v0.2.32')
        self.git('tag', 'pro-v0.2.32')
        self.assertEqual(module.release_version(self.root, candidate), 'v0.2.32')
        (self.root / 'fix.txt').write_text('next fix')
        self.commit('next candidate')
        self.assertEqual(module.release_version(self.root), 'v0.2.33')
