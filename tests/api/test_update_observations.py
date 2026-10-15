"""Read-only package observations with isolated files and no host commands."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class UpdateObservationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = self.root / "updates.json"
        self.enterContext(patch.object(api, "UPDATES_FILE", self.cache))
        self.enterContext(patch.object(api, "DATA_DIR", self.root))
        self.command = self.enterContext(patch.object(api.subprocess, "run"))
        self.thread = self.enterContext(patch.object(api.threading, "Thread"))

    def refresh(self, output, code=0):
        self.command.return_value = subprocess.CompletedProcess([], code, output, "fixture private command detail")
        api.refresh_updates_cache()
        return api.update_status()

    def assert_unknown(self, result):
        self.assertIsNone(result["available"])
        self.assertIsNone(result["security"])
        self.assertIsNone(result["kernel_available"])
        self.assertIsNone(result["checked_at"])
        self.assertTrue(result["error"])
        self.assertNotIn("private", result["error"])
        self.assertNotIn("_observation_version", result)

    def test_confirmed_empty_simulation_is_zero_and_uses_direct_read_only_command(self):
        result = self.refresh("Reading package lists... Done\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n")
        self.assertEqual(result["available"], 0)
        self.assertEqual(result["security"], 0)
        self.assertFalse(result["kernel_available"])
        self.assertFalse(result["refreshing"])
        self.assertEqual(result["error"], "")
        args, options = self.command.call_args
        self.assertEqual(args[0], ["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"])
        self.assertEqual(options["env"]["LC_ALL"], "C")
        self.assertNotIn("_observation_version", result)

    def test_successful_simulation_counts_only_confirmed_package_operations(self):
        result = self.refresh("2 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n"
                              "Inst openssl [3.0] (3.1 Debian-Security:stable-security [amd64])\n"
                              "Inst linux-image-amd64 [1] (2 Debian:stable [amd64])\nConf openssl (3.1)\n")
        self.assertEqual(result["available"], 2)
        self.assertEqual(result["security"], 1)
        self.assertTrue(result["kernel_available"])
        self.assertIsInstance(result["checked_at"], str)

    def test_failed_partial_malformed_and_held_simulations_never_publish_zero_updates(self):
        outputs = ("", "unexpected output", "Inst package (2 Debian [amd64])",
                   "1 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
                   "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\nInst package (2 Debian [amd64])",
                   "1 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\nInst malformed",
                   "0 upgraded, 0 newly installed, 0 to remove and 1 not upgraded.",
                   "0 upgraded, 0 newly installed, 1 to remove and 0 not upgraded.")
        for output in outputs:
            with self.subTest(output=output):
                self.assert_unknown(self.refresh(output))
        self.assert_unknown(self.refresh("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.", code=100))
        self.assertFalse(api.updates_refresh_lock.locked())

    def test_timeout_missing_command_and_os_failure_replace_old_current_claim(self):
        for failure in (subprocess.TimeoutExpired("apt-get", 150), FileNotFoundError(), OSError("unavailable")):
            with self.subTest(failure=failure):
                self.refresh("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
                self.command.side_effect = failure
                api.refresh_updates_cache()
                self.assert_unknown(api.update_status())
                self.assertFalse(api.updates_refresh_lock.locked())
                self.command.side_effect = None
        self.assertEqual(self.refresh("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")["available"], 0)

    def test_legacy_or_malformed_cache_requires_simulation_without_blocking_get(self):
        for value in ({"available": 0, "security": 0}, [], {"_observation_version": 1, "available": True},
                      {"_observation_version": 1, "available": -1, "security": 0}):
            with self.subTest(value=value):
                self.cache.write_text(json.dumps(value))
                result = api.update_status()
                self.assertIsNone(result["available"])
                self.assertTrue(result["refreshing"])
        self.command.assert_not_called()
        self.thread.return_value.start.assert_called()

    def test_refresh_marks_cached_zero_pending_and_failure_retries_after_one_minute(self):
        self.refresh("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
        with api.updates_refresh_lock:
            self.assertTrue(api.update_status()["refreshing"])
        result = self.refresh("", code=100)
        self.assertFalse(result["refreshing"])
        modified = self.cache.stat().st_mtime
        self.thread.reset_mock()
        with patch.object(api.time, "time", return_value=modified + 61):
            self.assertTrue(api.update_status()["refreshing"])
            self.thread.return_value.start.assert_called_once()
        self.assert_unknown(api.update_status())

    def test_single_flight_and_expired_success_cache_stays_pending(self):
        with api.updates_refresh_lock:
            api.refresh_updates_cache()
        self.command.assert_not_called()
        self.refresh("0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
        modified = self.cache.stat().st_mtime
        os.utime(self.cache, (modified - 901, modified - 901))
        self.assertTrue(api.update_status()["refreshing"])
