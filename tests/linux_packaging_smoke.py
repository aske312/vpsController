"""Exercise real rsync filters with synthetic files, never actual secrets."""
import os
from pathlib import Path
import subprocess
import tempfile

project = Path(__file__).resolve().parents[1]
for script in ('scripts/build-release.sh', 'scripts/vps-control.sh'):
    lines = (project / script).read_text().splitlines()
    start = next(index for index, line in enumerate(lines) if line.lstrip().startswith('rsync -a --delete'))
    end = start
    while lines[end].rstrip().endswith('\\'):
        end += 1
    command = '\n'.join(lines[start:end + 1])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / 'source'
        stage = Path(directory) / 'stage'
        root.mkdir()
        stage.mkdir()
        private = ['.servers/test.txt', 'docs/audit/test.md', 'docs/backlog/test.md', '.env', '.env.local', '.runtime/test.txt', 'AGENTS.md', 'tmp/test.txt', 'work/test.txt', 'output/test.txt']
        for name in private + ['README.md', 'api/main.py', 'public/test.txt']:
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('synthetic fixture')
        env = dict(os.environ, ROOT_DIR=str(root), PROJECT_DIR=str(root), STAGE=str(stage), INSTALL_DIR=str(stage))
        subprocess.run(['bash', '-euc', command], env=env, check=True)
        assert all(not (stage / name).exists() for name in private)
        assert (stage / 'api/main.py').exists() and (stage / 'public/test.txt').exists()
        print(script, 'private/local files excluded; application files retained')
