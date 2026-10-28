"""Test and production publish the same version without moving pro back to test."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

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

    def test_explicit_minor_version_starts_at_requested_release(self):
        self.git('tag', 'pro-v0.2.31')
        (self.root / 'package.json').write_text(json.dumps({'version': '0.3.0'}))
        self.commit('prepare 0.3.0')
        self.assertEqual(module.release_version(self.root), 'v0.3.0')
        self.commit('production merge')
        self.assertEqual(module.release_version(self.root), 'v0.3.0')
        self.git('tag', 'pro-v0.3.0')
        (self.root / 'fix.txt').write_text('next fix')
        self.commit('new fix')
        self.assertEqual(module.release_version(self.root), 'v0.3.1')

    def test_explicit_patch_version_is_not_lowered_to_next_automatic_patch(self):
        self.git('tag', 'pro-v0.2.31')
        (self.root / 'package.json').write_text(json.dumps({'version': '0.2.40'}))
        self.commit('explicit version')
        self.assertEqual(module.release_version(self.root), 'v0.2.40')


class ReleaseWorkflowVersionTests(unittest.TestCase):
    def test_architectures_and_publication_use_the_verified_version_snapshot(self):
        for name in ('ci.yml', 'release.yml'):
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text(encoding='utf-8'))
                jobs = workflow['jobs']
                verify = jobs['verify']
                self.assertEqual(verify['outputs']['version'], '${{ steps.version.outputs.version }}')
                selectors = [step for step in verify['steps'] if 'release-version.py' in step.get('run', '')]
                self.assertEqual(len(selectors), 1)
                self.assertEqual(selectors[0]['id'], 'version')
                self.assertEqual(verify['steps'][0]['with']['fetch-depth'], 0)
                build = next(step for step in jobs['package']['steps'] if 'RELEASE_VERSION' in step.get('env', {}))
                self.assertEqual(build['env']['RELEASE_VERSION'], '${{ needs.verify.outputs.version }}')
                for job_name, job in jobs.items():
                    if job_name != 'verify':
                        self.assertFalse(any('release-version.py' in step.get('run', '') for step in job['steps']))
                if 'publish' in jobs:
                    self.assertIn('verify', jobs['publish']['needs'])
                    publish = next(step for step in jobs['publish']['steps'] if 'VERSION' in step.get('env', {}))
                    self.assertEqual(publish['env']['VERSION'], build['env']['RELEASE_VERSION'])
