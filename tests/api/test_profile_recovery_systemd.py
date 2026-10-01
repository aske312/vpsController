"""Opt-in Linux test: isolated files/unit, never the installed application."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from tests.api.support import ROOT
import profile_recovery
from service_state import observe_service


@unittest.skipUnless(sys.platform == "linux" and os.getenv("CORE_SYSTEMD_TESTS") == "1",
                     "Requires explicitly enabled isolated Linux/systemd tests")
class ProfileRecoverySystemdTests(unittest.TestCase):
    def test_abrupt_exit_restores_files_and_real_service(self):
        if os.geteuid() != 0 or not shutil.which("systemctl"):
            self.skipTest("Root and systemd are required for an isolated runtime unit")
        with tempfile.TemporaryDirectory(prefix="vps-core-profile-") as folder:
            work = Path(folder)
            unit = f"vps-core-profile-test-{uuid.uuid4().hex}.service"
            unit_file = Path("/run/systemd/system") / unit
            with unit_file.open("x") as output:
                output.write("[Unit]\nDescription=Isolated Core recovery test\n[Service]\nExecStart=/usr/bin/sleep infinity\n")
            def run(*args, check=False):
                return subprocess.run(args, check=check, text=True, capture_output=True, timeout=30)
            try:
                run("systemctl", "daemon-reload", check=True)
                run("systemctl", "start", unit, check=True)
                config = work / "config"
                config.mkdir()
                profiles = work / "profiles.json"
                profiles.write_text('[{"id":"original"}]')
                api = SimpleNamespace(PROFILE_FILE=profiles, ROUTING_SETTINGS_FILE=work / "routing.json",
                    CONFIG_ROOT=config, TRANSPORTS={"transport-wg"}, WG_CONFIG_BY_MODULE={},
                    SERVICE_BY_MODULE={"transport-wg": unit}, observe_service=observe_service, run=run,
                    get_action_payload=lambda: {"id": "1" * 32})
                program = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0] = [sys.argv[1] + '/api', sys.argv[1] + '/protocol-images/mihomo']
import profile_recovery
from service_state import observe_service
work = Path(sys.argv[2])
api = SimpleNamespace(PROFILE_FILE=work/'profiles.json', ROUTING_SETTINGS_FILE=work/'routing.json',
    CONFIG_ROOT=work/'config', TRANSPORTS={'transport-wg'}, WG_CONFIG_BY_MODULE={},
    SERVICE_BY_MODULE={'transport-wg':sys.argv[3]}, observe_service=observe_service,
    get_action_payload=lambda: {'id':'1'*32})
with profile_recovery.transaction(api, {'transport-wg'}):
    api.PROFILE_FILE.write_text('[]')
    (api.CONFIG_ROOT/'created.json').write_text('{}')
    os._exit(77)
"""
                result = run(sys.executable, "-c", program, str(ROOT), str(work), unit)
                self.assertEqual(result.returncode, 77, result.stderr)
                self.assertTrue(profile_recovery.marker(api).exists())
                self.assertEqual(profiles.read_text(), "[]")
                run("systemctl", "stop", unit, check=True)
                profile_recovery.recover(api)
                self.assertEqual(json.loads(profiles.read_text()), [{"id": "original"}])
                self.assertFalse((config / "created.json").exists())
                self.assertFalse(profile_recovery.marker(api).exists())
                self.assertEqual(observe_service(unit)["runtime"]["state"], "running")
                profile_recovery.recover(api)
            finally:
                run("systemctl", "stop", unit)
                run("systemctl", "reset-failed", unit)
                unit_file.unlink()
                run("systemctl", "daemon-reload", check=True)
