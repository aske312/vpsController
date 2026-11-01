"""Exercise the release's actual staging command without downloading/building."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import tarfile
import unittest

BASH = shutil.which('bash') if os.name != 'nt' else next((str(p) for p in (Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')) if p.exists()), None)


@unittest.skipUnless(BASH, 'Requires Bash')
class ReleaseCompressionTests(unittest.TestCase):
    def test_parallel_and_fallback_compression_preserve_readable_gzip_tar(self):
        source = (Path(__file__).resolve().parents[2] / 'scripts/build-release.sh').read_text()
        command = source[source.index('if command -v pigz'):source.index('\necho "Prepared release:')]
        for parallel in (False, True):
            with self.subTest(parallel=parallel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / 'vps-control-release'
                stage.mkdir()
                (stage / 'payload').write_bytes(b'payload fixture')
                value = root.resolve().as_posix()
                if os.name == 'nt':
                    value = '/' + value[0].lower() + value[2:]
                script = '''set -eu
WORK_DIR=$FIXTURE_ROOT
OUTPUT=$FIXTURE_ROOT/release.tar.gz
pigz() { gzip; }
command() {
  if [[ "$*" == '-v pigz' ]]; then [[ "$FIXTURE_PARALLEL" == yes ]]; else builtin command "$@"; fi
}
''' + command
                subprocess.run([BASH, '-s'], input=script, text=True, check=True, capture_output=True,
                               env={**os.environ, 'FIXTURE_ROOT': value, 'FIXTURE_PARALLEL': 'yes' if parallel else 'no'})
                with tarfile.open(root / 'release.tar.gz', 'r:gz') as archive:
                    self.assertEqual(archive.extractfile('vps-control-release/payload').read(), b'payload fixture')


@unittest.skipUnless(shutil.which('rsync') and shutil.which('bash'), 'Requires rsync and Bash')
class ReleaseStagingTests(unittest.TestCase):
    def test_staging_keeps_template_without_secrets_caches_or_retired_files(self):
        source = (Path(__file__).resolve().parents[2] / 'scripts/build-release.sh').read_text()
        command = source[source.index('rsync -a --delete'):source.index('\n\n# Check the staged release')]
        with tempfile.TemporaryDirectory() as directory:
            root, stage = Path(directory) / 'source', Path(directory) / 'stage'
            root.mkdir(); stage.mkdir()
            files = ('.env.example', '.env', '.env.production', 'api/.env',
                     '.servers/vps-1.txt', 'docs/.audit/README.md', 'api/main.py',
                     'api/__pycache__/retired.cpython-312.pyc', 'api/retired.pyc')
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            retired = stage / 'protocol-images/mihomo/client_singbox.py'
            retired.parent.mkdir(parents=True)
            retired.write_text('retired fixture')
            subprocess.run(['bash', '-euc', command], check=True,
                           env={**os.environ, 'ROOT_DIR': str(root), 'STAGE': str(stage)})
            self.assertEqual(sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()),
                             ['.env.example', 'api/main.py'])
