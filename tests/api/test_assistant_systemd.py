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


@unittest.skipUnless(sys.platform == 'linux' and os.environ.get('CORE_SYSTEMD_TESTS') == '1', 'Requires isolated systemd runner')
class DurableChatSystemdTests(unittest.TestCase):
    def test_cancellation_stops_the_job_and_a_runtime_in_another_process_session(self):
        from tests.api.support import api
        import json
        import time
        import types
        import assistant_jobs as jobs
        identity = uuid.uuid4().hex
        unit = 'vps-control-chat-' + identity + '.service'
        with tempfile.TemporaryDirectory(prefix='chat-cancel-', dir='/run') as directory:
            root = Path(directory)
            fixture = root / 'worker.py'
            child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(90)"
            fixture.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
                               "p=subprocess.Popen([sys.executable,'-c',%r],start_new_session=True)\n"
                               "Path(%r).write_text(str(p.pid))\ntime.sleep(90)\n" % (child, str(root/'child.pid')))
            isolated = types.SimpleNamespace(DATA_DIR=root/'data', observe_service=api.observe_service)
            try:
                with patch.object(control, 'read', return_value={'revision':'fixture','model':'m'}), patch.object(jobs, 'dispatch'):
                    jobs.submit(isolated, control.Chat(prompt='fixture', model='m', request_id=identity))
                    jobs.update(isolated, identity, state='running')
                    subprocess.run(['systemd-run','--quiet','--collect','--unit='+unit,
                                    '--property=KillMode=control-group','--property=TimeoutStopSec=1s',
                                    sys.executable,str(fixture)], check=True,capture_output=True)
                    deadline = time.monotonic() + 15
                    while not (root/'child.pid').exists() and time.monotonic() < deadline: time.sleep(.05)
                    self.assertTrue((root/'child.pid').exists())
                    child_pid = int((root/'child.pid').read_text())
                    time.sleep(.1)  # Let the child install its SIGTERM handler.
                    result = jobs.cancel(isolated, identity)
                    while result['state'] not in jobs.TERMINAL and time.monotonic() < deadline:
                        time.sleep(.1)
                        result = jobs.get(isolated, identity)
                    self.assertEqual(result['error']['code'], 'cancelled')
                    self.assertFalse(jobs.input_path(isolated, identity).exists())
                    process = Path('/proc') / str(child_pid) / 'stat'
                    try: process_state = process.read_text().split()[2]
                    except FileNotFoundError: process_state = None
                    self.assertIn(process_state, (None, 'Z'))
                    self.assertNotIn('output', control.operations.read(jobs.path(isolated, identity)))
            finally:
                subprocess.run(['systemctl','stop',unit],capture_output=True)
                subprocess.run(['systemctl','reset-failed',unit],capture_output=True)

    def test_chat_outlives_its_submitting_process_and_delivers_once(self):
        import json
        import time
        import assistant_jobs as jobs
        identity = uuid.uuid4().hex
        unit = 'vps-control-chat-' + identity + '.service'
        with tempfile.TemporaryDirectory(prefix='chat-systemd-', dir='/run') as directory:
            root = Path(directory)
            env = root / 'fixture.env'; env.write_text('CHAT_FIXTURE=isolated\n'); env.chmod(0o600)
            fixture = root / 'worker.py'
            fixture.write_text("""import sys, os, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, %r)
import assistant_jobs as jobs
import assistant_control as control
root = Path(%r)
api = SimpleNamespace(DATA_DIR=root/'data', ENV_FILE=root/'fixture.env')
control.read = lambda api: {'revision': 'fixture', 'model': 'm'}
def answer(*args, **kwargs):
    assert os.environ.get('CHAT_FIXTURE') == 'isolated'
    (root/'started').touch()
    deadline = time.monotonic() + 20
    while not (root/'release').exists():
        if time.monotonic() > deadline: raise RuntimeError('fixture timeout')
        time.sleep(.05)
    return {'ok': True, 'result': {'answer': 'once', 'tools_used': []}}
control.chat_request = answer
if len(sys.argv) > 1:
    jobs.execute(api, sys.argv[1])
else:
    jobs.__file__ = str(root/'worker.py')
    jobs.submit(api, control.Chat(prompt='fixture', model='m', request_id=%r))
""" % (str(ROOT/'api'), str(root), identity))
            try:
                submitted = subprocess.run([sys.executable, str(fixture)], capture_output=True, text=True, timeout=20)
                self.assertEqual(submitted.returncode, 0, submitted.stderr)
                deadline = time.monotonic() + 20
                while not (root/'started').exists() and time.monotonic() < deadline: time.sleep(.05)
                self.assertTrue((root/'started').exists())
                # The API caller has exited; the independent worker still owns the task.
                record = control.operations.read(root/'data/assistant/requests'/f'{identity}.json')
                self.assertEqual(record['state'], 'running')
                (root/'release').touch()
                while time.monotonic() < deadline:
                    record = control.operations.read(root/'data/assistant/requests'/f'{identity}.json')
                    if record['state'] == 'done': break
                    time.sleep(.05)
                self.assertEqual(record['state'], 'done')
                self.assertEqual(record['output']['result']['answer'], 'once')
                stored = (root/'data/assistant/requests'/f'{identity}.json').read_text()
                self.assertNotIn('once', stored)
                self.assertEqual(json.loads(stored)['private_storage'], 'nodera-private-v1')
                self.assertNotIn('payload', record)
                self.assertEqual((root/'data/assistant/requests'/f'{identity}.json').stat().st_mode & 0o777, 0o600)
            finally:
                subprocess.run(['systemctl','stop',unit],capture_output=True)
                subprocess.run(['systemctl','reset-failed',unit],capture_output=True)
