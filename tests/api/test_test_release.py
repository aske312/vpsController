"""Test-channel admission and release selection, without deploying an application."""
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from tests.api.support import ROOT, api


class TestReleaseAdmissionTests(unittest.TestCase):
    def test_test_commands_require_service_mode_before_starting_worker(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(api, "SERVICE_MODE_FILE", Path(temp) / "mode"), patch.object(api, "start_application_operation") as start:
            for action in ("test-update", "test-rollback"):
                with self.subTest(action=action), self.assertRaises(HTTPException) as error:
                    api.application_action(api.ApplicationAction(action=action), operation_id="a" * 32)
                self.assertEqual(error.exception.status_code, 409)
            start.assert_not_called()

    def test_admitted_update_keeps_exact_operation_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mode").touch()
            (root / "scripts").mkdir()
            (root / "scripts/vps-control.sh").touch()
            with patch.object(api, "SERVICE_MODE_FILE", root / "mode"), patch.object(api, "INSTALL_DIR", root), patch.object(api, "start_application_operation", return_value={"id": "a" * 32, "state": "queued"}) as start:
                result = api.application_action(api.ApplicationAction(action="test-update"), operation_id="a" * 32)
                self.assertEqual(result["id"], "a" * 32)
                self.assertEqual(start.call_args.args, ("test-update", ["/bin/bash", str(root / "scripts/vps-control.sh"), "test-update"]))
                self.assertEqual(start.call_args.kwargs["operation_id"], "a" * 32)


BASH = os.getenv("PRIVACY_BASH_BIN") or (shutil.which("bash") if os.name != "nt" else None)


@unittest.skipUnless(BASH, "Requires Bash (PRIVACY_BASH_BIN on Windows)")
class TestReleaseSelectionTests(unittest.TestCase):
    def run_shell(self, body, *, mode=True, backup=False, channel="production"):
        source = (ROOT / "scripts/vps-control.sh").read_text(encoding="utf-8")
        functions = []
        for name in ("update_test_app", "update_prebuilt_branch", "release_metadata_value"):
            start = source.index(name + "() {")
            end = re.search(r"\n[a-zA-Z_][a-zA-Z_0-9]*\(\) \{", source[start + 1:])
            functions.append(source[start:start + 1 + end.start()])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if mode:
                (root / "mode").touch()
            if backup:
                (root / "backup").mkdir()
            (root / ".build-commit").write_text("a" * 40)
            (root / ".prebuilt-release").write_text("channel=" + channel + "\n")
            script = '\n'.join([
                "set -eu", "cd " + shlex.quote(root.as_posix()),
                "SERVICE_MODE_FILE=mode; TEST_BACKUP_DIR=backup; INSTALL_DIR=.; DATA_DIR=.",
                "TEST_BRANCH=test-pro; TEST_RELEASE_TAG=pro-test-latest; PRODUCTION_BRANCH=pro; PRODUCTION_RELEASE_TAG=pro-latest; PRODUCT_EDITION=pro",
                "STABL_RELEASE_WAIT_ATTEMPTS=1; UPDATE_DOWNLOAD_TIMEOUT=5; APP_RELEASE_URL=https://example.invalid/release",
                "die() { echo \"$*\" >&2; exit 17; }; ok() { :; }; info() { :; }",
                "git() { printf '%040d\\n' 0 | tr 0 a; }; system_architecture() { echo amd64; }",
                "install() { :; }; python3() { :; }; mktemp() { echo .; }; curl() { :; }",
                "tar() { echo commit=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }",
                "write_action_status() { :; }; install_prebuilt_release() { printf 'INSTALL %s %s %s\\n' \"$1\" \"$3\" \"$4\"; }",
                *functions, body,
            ])
            return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=15)

    def test_first_test_update_preserves_rollback_and_later_updates_keep_it(self):
        for backup, expected in ((False, "yes"), (True, "no")):
            with self.subTest(backup=backup):
                result = self.run_shell("update_test_app test-update", backup=backup)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"INSTALL install-release {expected} test", result.stdout)

    def test_same_sha_only_skips_installation_in_the_same_channel(self):
        result = self.run_shell("update_test_app test-update", backup=True, channel="test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("INSTALL", result.stdout)
        result = self.run_shell("update_prebuilt_branch pro pro-latest", channel="test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INSTALL install-release no production", result.stdout)

    def test_channel_guard_rejects_untrusted_branch_tag_and_missing_mode(self):
        for command, mode in (("update_test_app test-update", False), ("update_prebuilt_branch test-pro pro-latest test", True), ("update_prebuilt_branch pro pro-test-latest", True)):
            with self.subTest(command=command):
                result = self.run_shell(command, mode=mode)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertNotIn("INSTALL", result.stdout)

    def test_explicit_local_archive_still_uses_the_test_channel(self):
        result = self.run_shell("update_test_app test-update /tmp/local.tar.gz")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INSTALL install-release yes test", result.stdout)
