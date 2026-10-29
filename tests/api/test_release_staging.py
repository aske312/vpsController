"""Exercise the release's actual staging command without downloading/building."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('rsync') and shutil.which('bash'), 'Requires rsync and Bash')
class ReleaseStagingTests(unittest.TestCase):
    def test_environment_template_is_shipped_without_secrets_or_local_notes(self):
        source = (Path(__file__).resolve().parents[2] / 'scripts/build-release.sh').read_text()
        command = source[source.index('rsync -a --delete'):source.index('\n\n# Check the staged release')]
        with tempfile.TemporaryDirectory() as directory:
            root, stage = Path(directory) / 'source', Path(directory) / 'stage'
            root.mkdir(); stage.mkdir()
            files = ('.env.example', '.env', '.env.production', 'api/.env',
                     '.servers/vps-1.txt', 'docs/.audit/README.md', 'api/main.py')
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            subprocess.run(['bash', '-euc', command], check=True,
                           env={**os.environ, 'ROOT_DIR': str(root), 'STAGE': str(stage)})
            self.assertEqual(sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()),
                             ['.env.example', 'api/main.py'])
