"""Run the selector with synthetic downloads; never install services or packages."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Bootstrap requires Linux root')
class InstallerRoutingTests(unittest.TestCase):
    def test_editions_route_to_their_own_branch_and_forward_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy(ROOT / 'install.sh', root / 'install.sh')
            shutil.copy(ROOT / 'editions.json', root / 'editions.json')
            tools = root / 'bin'; tools.mkdir()
            curl = tools / 'curl'
            curl.write_text('''#!/usr/bin/env bash
set -eu
url="${*: -1}"
while [[ "$1" != --output ]]; do shift; done
case "$url" in
  */light/scripts/install-panel.sh|*/pro/scripts/install-panel.sh|*/agent/scripts/install-agent.sh) ;;
  *) exit 99 ;;
esac
cat >"$2" <<'SCRIPT'
#!/usr/bin/env bash
printf '%s|%s|%s\\n' "$VPS_CONTROL_BRANCH" "$VPS_CONTROL_REPOSITORY" "$*" > "$ROUTE_LOG"
SCRIPT
''')
            curl.chmod(0o755)
            log = root / 'route'
            env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], ROUTE_LOG=str(log))
            for edition, arguments in [('light', ['--domain', 'panel.example.com']),
                                       ('pro', []), ('agent', ['--public-ip', '8.8.8.8'])]:
                result = subprocess.run(['bash', str(root / 'install.sh'), '--edition', edition, *arguments],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(log.read_text().strip(), edition + '|https://github.com/aske312/vpsController|' + ' '.join(arguments))
            log.unlink()
            for arguments in (['--edition'], ['--edition', 'invalid']):
                result = subprocess.run(['bash', str(root / 'install.sh'), *arguments], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(log.exists())


if __name__ == '__main__': unittest.main()
