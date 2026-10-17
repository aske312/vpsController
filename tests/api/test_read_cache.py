import concurrent.futures
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from tests.api.support import api
from read_cache import StampedReadCache, file_stamp
from service_state import observation_scope, observe_service


class ReadCacheTests(unittest.TestCase):
    def test_version_stamp_ttl_and_unavailable_results(self):
        cache = StampedReadCache()
        load = Mock(side_effect=["one", "two", "three", "", "recovered"])
        with patch("read_cache.time.monotonic", return_value=100) as clock:
            self.assertEqual(cache.get("version", "file1", load), "one")
            self.assertEqual(cache.get("version", "file1", load), "one")
            self.assertEqual(load.call_count, 1)
            self.assertEqual(cache.get("version", "file2", load), "two")
            clock.return_value = 161
            self.assertEqual(cache.get("version", "file2", load), "three")
            self.assertEqual(cache.get("version", "file3", load), "")
            self.assertEqual(cache.get("version", "file3", load), "recovered")

    def test_atomic_replacement_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "binary"
            self.assertIsNone(file_stamp(binary))
            binary.write_bytes(b"old")
            before = file_stamp(binary)
            replacement = Path(directory) / "replacement"
            replacement.write_bytes(b"new")
            os.replace(replacement, binary)
            self.assertNotEqual(before, file_stamp(binary))
        cache = StampedReadCache()
        load = Mock(return_value="uncertain")
        cache.get("missing", None, load)
        cache.get("missing", None, load)
        self.assertEqual(load.call_count, 2)

    def test_concurrent_refresh_is_shared_but_a_new_file_is_not_blocked(self):
        cache = StampedReadCache()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def slow():
            calls.append("old")
            entered.set()
            self.assertTrue(release.wait(3))
            return "old"

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            old = executor.submit(cache.get, "binary", "old-stamp", slow)
            try:
                self.assertTrue(entered.wait(3))
                same = executor.submit(cache.get, "binary", "old-stamp", slow)
            finally:
                release.set()
            self.assertEqual(old.result(timeout=3), "old")
            self.assertEqual(same.result(timeout=3), "old")
            self.assertEqual(calls, ["old"])
            entered.clear()
            release.clear()
            old = executor.submit(cache.get, "replacement", "old-stamp", slow)
            try:
                self.assertTrue(entered.wait(3))
                fresh = executor.submit(cache.get, "replacement", "new-stamp", lambda: "new")
                self.assertEqual(fresh.result(timeout=3), "new")
            finally:
                release.set()
            self.assertEqual(old.result(timeout=3), "old")
        self.assertEqual(cache.get("replacement", "new-stamp", lambda: self.fail("New value was overwritten")), "new")

    def test_failed_reads_are_not_cached_and_other_keys_remain_available(self):
        cache = StampedReadCache(capacity=2)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            cache.get("bad", "stamp", lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))
        self.assertEqual(cache.get("good", "stamp", lambda: "available"), "available")
        self.assertEqual(cache.get("bad", "stamp", lambda: "recovered"), "recovered")
        self.assertEqual(cache.get("third", "stamp", lambda: "third"), "third")
        self.assertEqual(cache.get("good", "stamp", lambda: "available"), "available")

    def test_systemd_observations_are_scoped_to_one_response(self):
        def observation(state):
            return subprocess.CompletedProcess([], 0, f"LoadState=loaded\nActiveState={state}\nSubState=running\nUnitFileState=enabled", "")
        with patch("service_state.subprocess.run", side_effect=[observation("active"), observation("inactive")]) as run:
            with observation_scope():
                first = observe_service("example.service")
                first["runtime"]["state"] = "draft"
                with observation_scope():
                    self.assertEqual(observe_service("example.service")["runtime"]["state"], "running")
            with observation_scope():
                self.assertEqual(observe_service("example.service")["runtime"]["state"], "stopped")
            self.assertEqual(run.call_count, 2)

    def test_package_and_binary_version_reads_invalidate_when_files_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "version-source"
            path.write_text("first")
            with patch.object(api, "version_reads", StampedReadCache()), patch.object(api, "DPKG_STATUS_FILE", path), patch.object(api, "run", return_value="Installed: 1\nCandidate: 2") as run:
                self.assertEqual(api.apt_package_versions("fixture"), ("1", "2"))
                self.assertEqual(api.apt_package_versions("fixture"), ("1", "2"))
                self.assertEqual(run.call_count, 1)
                path.write_text("replaced package database")
                run.return_value = "Installed: 2\nCandidate: 2"
                self.assertEqual(api.apt_package_versions("fixture"), ("2", "2"))
                self.assertEqual(run.call_count, 2)
            with patch.object(api, "version_reads", StampedReadCache()), patch.object(api, "run", return_value="Core 1.2.3") as run:
                self.assertEqual(api.standalone_binary_version(path, "version"), "1.2.3")
                self.assertEqual(api.standalone_binary_version(path, "version"), "1.2.3")
                self.assertEqual(run.call_count, 1)
                path.write_text("new binary")
                run.return_value = "Core 2.3.4"
                self.assertEqual(api.standalone_binary_version(path, "version"), "2.3.4")

    def test_github_discovery_is_background_single_flight_and_retries_failures_later(self):
        with patch.dict(api.github_release_cache, {}, clear=True), patch.dict(api.github_release_retry_after, {}, clear=True), \
             patch.object(api, "github_release_refreshing", set()), patch.object(api.threading, "Thread") as thread:
            self.assertEqual(api.github_latest_tag("fixture/repo"), "")
            self.assertEqual(api.github_latest_tag("fixture/repo"), "")
            self.assertEqual(thread.call_count, 1)
            with patch.object(api, "run", return_value='{"tag_name":"v1.2.3"}'):
                api.refresh_github_latest_tag("fixture/repo")
            self.assertEqual(api.github_latest_tag("fixture/repo"), "1.2.3")
            self.assertNotIn("fixture/repo", api.github_release_refreshing)
            self.assertEqual(thread.call_count, 1)
            self.assertEqual(api.github_latest_tag("fixture/offline"), "")
            with patch.object(api, "run", return_value=""):
                api.refresh_github_latest_tag("fixture/offline")
            self.assertEqual(api.github_latest_tag("fixture/offline"), "")
            self.assertEqual(thread.call_count, 2)
            self.assertNotIn("fixture/offline", api.github_release_refreshing)

    def test_service_discovery_does_not_collect_unused_version_metadata(self):
        with patch.object(api, "protocol_image_manifests", return_value={}) as images, \
             patch.object(api, "mihomo_module_services", return_value={}):
            api.protocol_managed_services()
        images.assert_called_once_with(include_versions=False)
