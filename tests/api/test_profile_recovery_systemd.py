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
from unittest.mock import patch
import uuid

from tests.api.support import ROOT
import profile_recovery
from service_state import observe_service


@unittest.skipUnless(sys.platform == "linux" and os.getenv("CORE_SYSTEMD_TESTS") == "1",
                     "Requires explicitly enabled isolated Linux/systemd tests")
class ProfileRecoverySystemdTests(unittest.TestCase):
    def test_abrupt_exit_restores_files_and_real_service(self):
        self.check_abrupt_exit()

    def test_settings_abrupt_exit_restores_running_service_and_permissions(self):
        self.check_abrupt_exit(settings=True)

    def test_settings_abrupt_exit_preserves_stopped_service(self):
        self.check_abrupt_exit(settings=True, running=False)

    def test_settings_failed_restore_keeps_marker_and_service_stopped(self):
        self.check_abrupt_exit(settings=True, failed_restore=True)

    def test_settings_restores_runtime_enablement(self):
        self.check_abrupt_exit(settings=True, enabled="enabled-runtime")

    def check_abrupt_exit(self, *, settings=False, running=True, failed_restore=False, enabled="disabled"):
        if os.geteuid() != 0 or not shutil.which("systemctl"):
            self.skipTest("Root and systemd are required for an isolated runtime unit")
        with tempfile.TemporaryDirectory(prefix="vps-core-profile-") as folder:
            work = Path(folder)
            unit = f"vps-core-profile-test-{uuid.uuid4().hex}.service"
            unit_file = Path("/run/systemd/system") / unit
            with unit_file.open("x") as output:
                output.write("[Unit]\nDescription=Isolated Core recovery test\n[Service]\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n")
            def run(*args, check=False):
                return subprocess.run(args, check=check, text=True, capture_output=True, timeout=30)
            try:
                run("systemctl", "daemon-reload", check=True)
                if enabled == "enabled-runtime":
                    run("systemctl", "enable", "--runtime", unit, check=True)
                if running:
                    run("systemctl", "start", unit, check=True)
                config = work / "config"
                config.mkdir()
                profiles = work / "profiles.json"
                profiles.write_text('[{"id":"original"}]')
                settings_root = work / "settings"
                settings_root.mkdir()
                settings_file = settings_root / "transport-wg.json"
                settings_file.write_text('{"port":18000,"mtu":1280}\n')
                settings_file.chmod(0o640)
                external = work / "tunnel.conf"
                external.write_text("original peers")
                external.chmod(0o600)
                permissions = {target: (target.stat().st_mode & 0o777, target.stat().st_uid, target.stat().st_gid)
                               for target in (settings_file, external)}
                api = SimpleNamespace(PROFILE_FILE=profiles, ROUTING_SETTINGS_FILE=work / "routing.json",
                    CONFIG_ROOT=config, SETTINGS_ROOT=settings_root, TRANSPORTS={"transport-wg"},
                    WG_CONFIG_BY_MODULE={"transport-wg": external},
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
    CONFIG_ROOT=work/'config', SETTINGS_ROOT=work/'settings', TRANSPORTS={'transport-wg'},
    WG_CONFIG_BY_MODULE={'transport-wg':work/'tunnel.conf'},
    SERVICE_BY_MODULE={'transport-wg':sys.argv[3]}, observe_service=observe_service,
    get_action_payload=lambda: {'id':'1'*32})
with profile_recovery.transaction(api, {'transport-wg'}, settings_modules={'transport-wg'} if sys.argv[4]=='1' else set()):
    api.PROFILE_FILE.write_text('[]')
    (api.CONFIG_ROOT/'created.json').write_text('{}')
    (work/'tunnel.conf').write_text('candidate peers')
    if sys.argv[4]=='1':
        (work/'settings/transport-wg.json').write_text('{"port":18000,"mtu":1400}')
        (work/'settings/transport-wg.json').chmod(0o600)
    os._exit(77)
"""
                result = run(sys.executable, "-c", program, str(ROOT), str(work), unit, "1" if settings else "0")
                self.assertEqual(result.returncode, 77, result.stderr)
                self.assertTrue(profile_recovery.marker(api).exists())
                self.assertEqual(profiles.read_text(), "[]")
                run("systemctl", "stop" if running else "start", unit, check=True)
                if settings:
                    if enabled == "disabled":
                        run("systemctl", "enable", "--runtime", unit, check=True)
                    else:
                        run("systemctl", "disable", "--runtime", unit, check=True)
                if failed_restore:
                    original = profile_recovery.restore_file
                    def fail(target, saved):
                        if target == settings_file:
                            raise OSError("isolated settings restore failure")
                        return original(target, saved)
                    with patch.object(profile_recovery, "restore_file", side_effect=fail), self.assertRaisesRegex(RuntimeError, "isolated settings restore failure"):
                        profile_recovery.recover(api)
                    self.assertTrue(profile_recovery.marker(api).exists())
                    self.assertEqual(observe_service(unit)["runtime"]["state"], "stopped")
                profile_recovery.recover(api)
                self.assertEqual(json.loads(profiles.read_text()), [{"id": "original"}])
                self.assertFalse((config / "created.json").exists())
                self.assertFalse(profile_recovery.marker(api).exists())
                self.assertEqual(observe_service(unit)["runtime"]["state"], "running" if running else "stopped")
                if settings:
                    self.assertEqual(observe_service(unit)["unit_file_state"], enabled)
                self.assertEqual(external.read_text(), "original peers")
                if settings:
                    self.assertEqual(json.loads(settings_file.read_text()), {"port": 18000, "mtu": 1280})
                for target, expected in permissions.items():
                    details = target.stat()
                    self.assertEqual((details.st_mode & 0o777, details.st_uid, details.st_gid), expected)
                profile_recovery.recover(api)
            finally:
                run("systemctl", "stop", unit)
                run("systemctl", "disable", "--runtime", unit)
                run("systemctl", "reset-failed", unit)
                unit_file.unlink()
                run("systemctl", "daemon-reload", check=True)
