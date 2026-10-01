import json
import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
from metrics_history import MetricsHistory, MetricsMonitor, SettingsConflict


class MetricsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = MetricsHistory(Path(self.directory.name) / "history.sqlite3")

    def sample(self, cpu=25, rx=0, uptime=100):
        return {"cpu_percent": cpu, "network_rx": rx, "network_tx": rx * 2, "uptime_s": uptime,
                "memory_total": 100, "memory_available": 50, "disk_total": 1000, "disk_available": 700,
                "password": "must-not-be-stored", "client_ip": "must-not-be-stored"}

    def test_retention_aggregates_before_removing_raw_and_does_not_average_counters(self):
        self.store.record(self.sample(cpu=20, rx=100), 86400)
        self.store.record(self.sample(cpu=40, rx=130, uptime=103), 86403)
        self.store.record(self.sample(cpu=99, rx=999), 86403)  # Duplicate collector/poll cannot count twice.
        with self.store.database() as db:
            bucket = json.loads(db.execute("SELECT data FROM buckets WHERE resolution=60 AND ts=86400").fetchone()[0])
        self.assertEqual(bucket["samples"], 2)
        self.assertEqual(bucket["metrics"]["cpu_percent"]["sum"], 60)
        self.assertEqual(bucket["metrics"]["network_rx"]["last"], 130)
        self.assertEqual(bucket["metrics"]["network_rx"]["sum"], 0)
        self.assertEqual(bucket["metrics"]["rx_delta"]["sum"], 30)
        self.store.record(self.sample(), 86400 + 25 * 3600)
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM raw WHERE ts=86400").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buckets WHERE ts=86400").fetchone()[0], 2)
        self.assertNotIn(b"must-not-be-stored", self.store.path.read_bytes())

    def test_restart_reuses_history_and_counter_resets_and_missing_samples_are_gaps(self):
        self.store.record(self.sample(rx=100), 86400)
        self.store.record(self.sample(rx=130, uptime=103), 86403)
        restarted = MetricsHistory(self.store.path)
        restarted.record(self.sample(rx=10, uptime=1), 86406)
        restarted.record(self.sample(cpu=None, rx=20, uptime=61), 86466)
        points = {item["at"]: item for item in restarted.query("live", now=86466)["points"]}
        self.assertEqual(points[86403]["rx_bps"], 10)
        for timestamp in (86400, 86406, 86409, 86466):
            self.assertIsNone(points[timestamp]["rx_bps"])
        self.assertIsNone(points[86466]["cpu_percent"])

    def test_settings_revision_and_disabled_storage_keep_existing_history(self):
        self.store.record(self.sample(), 86400)
        settings = self.store.settings()
        self.assertEqual([settings[key] for key in ("raw_hours", "minute_days", "hour_days")], [24, 7, 90])
        self.store.configure({**settings, "enabled": False}, settings["revision"])
        with self.assertRaises(SettingsConflict):
            self.store.configure(settings, settings["revision"])
        self.store.record(self.sample(), 86403)
        with self.store.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM raw").fetchone()[0], 1)

    def test_history_api_requires_auth_and_invalid_period_and_settings_are_rejected(self):
        client = TestClient(api.app)
        self.assertEqual(client.get("/api/metrics/history").status_code, 401)
        api.app.dependency_overrides[api.require_token] = lambda: None
        self.addCleanup(api.app.dependency_overrides.clear)
        with patch.object(api, "metrics_history_store", self.store):
            self.assertEqual(client.get("/api/metrics/history?period=unknown").status_code, 422)
            settings = client.get("/api/services/metrics").json()
            payload = {**settings, "expected_revision": settings["revision"], "disk_limit_mb": 0}
            self.assertEqual(client.put("/api/services/metrics", json=payload).status_code, 422)
            payload["disk_limit_mb"] = 32
            self.assertEqual(client.put("/api/services/metrics", json=payload).status_code, 200)
            self.assertEqual(client.put("/api/services/metrics", json=payload).status_code, 409)
            self.assertEqual(client.get("/api/metrics/history").status_code, 200)

    def test_disk_budget_reclaims_old_raw_first_and_applies_on_each_connection(self):
        self.store.record(self.sample(), 86400)
        with self.store.database() as db:
            db.executemany("INSERT INTO raw VALUES (?,?)", [(86403 + index * 3, json.dumps({"padding": " " * 24000})) for index in range(500)])
        self.assertGreater(self.store.path.stat().st_size, 8 * 1024 * 1024)
        settings = self.store.settings()
        self.store.configure({**settings, "disk_limit_mb": 8}, settings["revision"])
        self.store.record(self.sample(), 88000)
        self.assertLessEqual(self.store.path.stat().st_size, 8 * 1024 * 1024)
        self.assertIsNotNone(self.store.settings()["trimmed_at"])
        with self.store.database() as db:
            self.assertEqual(db.execute("PRAGMA max_page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0], 8 * 1024 * 1024)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buckets WHERE ts=86400").fetchone()[0], 2)

    def test_monitor_collects_without_requests_and_cached_snapshot_is_isolated(self):
        recorded = threading.Event()
        class History:
            def record(self, resources):
                recorded.set()
        monitor = MetricsMonitor(History(), lambda: {"cpu_percent": 25, "observations": {"cpu": {"available": True}}})
        monitor.start()
        self.addCleanup(monitor.stop)
        self.assertTrue(recorded.wait(2))
        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["cpu_percent"], 25)
        snapshot["observations"]["cpu"]["available"] = False
        self.assertTrue(monitor.snapshot()["observations"]["cpu"]["available"])
        monitor.stop()
        self.assertFalse(monitor.thread.is_alive())
