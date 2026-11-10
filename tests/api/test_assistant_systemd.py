"""Exercise the generated service's credential directive under real systemd."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from tests.api.support import ROOT
import assistant_control as control
import assistant_worker as worker


@unittest.skipUnless(sys.platform == 'linux' and os.environ.get('CORE_SYSTEMD_TESTS') == '1', 'Requires isolated systemd runner')
class AssistantCredentialSystemdTests(unittest.TestCase):
    def test_generated_bot_credentials_are_readable_only_in_the_service(self):
        name = 'vps-control-credential-test-' + uuid.uuid4().hex
        unit = Path('/run/systemd/system', name + '.service')
        with tempfile.TemporaryDirectory(prefix=name, dir='/run') as folder:
            root = Path(folder); root.chmod(0o755)
            runtime = root / 'runtime'; runtime.mkdir(mode=0o755)
            python = runtime / 'venv/bin/python'; python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            data = root / 'data'
            api = SimpleNamespace(DATA_DIR=data, INSTALL_DIR=ROOT, observe_service=lambda *_: {'active': False, 'unit_present': True})
            credential = control.directory(api) / 'nodera-bot/runtime.json'
            credential.parent.mkdir(parents=True, mode=0o700)
            credential.write_text('credential-fixture'); credential.chmod(0o600)
            try:
                with patch.object(worker, 'assert_unit_owner', return_value=unit), patch.object(worker, 'checked'), \
                     patch.object(worker, 'require_idle'), patch.object(control, 'runtime_directory', return_value=runtime), \
                     patch.object(control, 'migrate_settings'), patch.object(control, 'read', return_value={'modules': {'nodera-bot': False}}):
                    worker.install(api, 'nodera-bot')
                (runtime / 'bot.py').write_text("import os\nfrom pathlib import Path\nassert (Path(os.environ['CREDENTIALS_DIRECTORY'])/'settings').read_text()=='credential-fixture'\n")
                (runtime / 'bot.py').chmod(0o644)
                # Wait for the fixture and avoid touching the application's real state directory.
                text = unit.read_text().replace('Type=simple', 'Type=oneshot').replace('StateDirectory=vps-control-nodera\n', '').replace('StateDirectoryMode=0700\n', '').replace('Restart=on-failure', 'Restart=no')
                unit.write_text(text)
                subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
                started = subprocess.run(['systemctl', 'start', unit.name], capture_output=True, text=True)
                self.assertEqual(started.returncode, 0, started.stderr)
                self.assertEqual(subprocess.check_output(['systemctl', 'show', unit.name, '--property=Result', '--value'], text=True).strip(), 'success')
                self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
            finally:
                subprocess.run(['systemctl', 'stop', unit.name], capture_output=True)
                subprocess.run(['systemctl', 'reset-failed', unit.name], capture_output=True)
                unit.unlink(missing_ok=True)
                subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
