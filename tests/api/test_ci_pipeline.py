"""The protected verification gate must reject any incomplete child check."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml
from tests.api.support import ROOT

BASH = shutil.which('bash') if os.name != 'nt' else next((str(p) for p in (Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')) if p.exists()), None)


@unittest.skipUnless(BASH, 'Requires Bash')
class CiPipelineTests(unittest.TestCase):
    def test_both_workflows_require_every_check_before_packaging(self):
        for name in ('ci.yml', 'release.yml'):
            workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
            jobs = workflow['jobs']
            gate = jobs['verify']
            self.assertEqual(set(gate['needs']), {'verify-api', 'verify-web'})
            self.assertEqual(gate['if'], 'always()')
            self.assertEqual(jobs['package']['needs'], 'verify')
            command = gate['steps'][0]['run']
            for api_state, web_state in (('success', 'success'), ('failure', 'success'),
                                         ('success', 'failure'), ('cancelled', 'success'),
                                         ('success', 'skipped'), ('skipped', 'skipped')):
                with self.subTest(workflow=name, api=api_state, web=web_state):
                    result = subprocess.run([BASH, '-c', command], capture_output=True, timeout=5,
                                            env={**os.environ, 'API_RESULT': api_state, 'WEB_RESULT': web_state})
                    self.assertEqual(result.returncode == 0, api_state == web_state == 'success')
