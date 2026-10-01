import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from metrics_history import MetricsHistory, MetricsMonitor


class MetricsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = MetricsHistory(Path(self.directory.name) / "history.sqlite3")

    @staticmethod
    def sample(cpu=25, rx=0, uptime=100):
        return {
            "cpu_percent": cpu,
            "network_rx": rx,
            "network_tx": rx * 2,
            "uptime_s": uptime,
            "memory_total": 100,
            "memory_available": 50,
            "disk_total": 1000,
            "disk_available": 700,
            "password": "must-not-be-stored",
        }

    def test_records_rates_and_excludes_unknown_fields(self):
        self.store.record(self.sample(cpu=20, rx=100), 86400)
        self.store.record(self.sample(cpu=40, rx=130, uptime=103), 86403)
        points = {point["at"]: point for point in self.store.query("live", now=86403)["points"]}
        self.assertEqual(points[86403]["cpu_percent"], 40)
        self.assertEqual(points[86403]["rx_bps"], 10)
        self.assertNotIn(b"must-not-be-stored", self.store.path.read_bytes())

    def test_restart_and_missing_samples_are_visible_as_gaps(self):
        self.store.record(self.sample(rx=100), 86400)
        self.store.record(self.sample(rx=130, uptime=103), 86403)
        restarted = MetricsHistory(self.store.path)
        restarted.record(self.sample(rx=10, uptime=1), 86406)
        points = {point["at"]: point for point in restarted.query("live", now=86409)["points"]}
        self.assertIsNone(points[86406]["rx_bps"])
        self.assertIsNone(points[86409]["cpu_percent"])

    def test_long_periods_use_aggregated_buckets(self):
        self.store.record(self.sample(cpu=20, rx=100), 86400)
        self.store.record(self.sample(cpu=40, rx=130, uptime=103), 86403)
        with self.store.database() as database:
            bucket = json.loads(database.execute("SELECT data FROM buckets WHERE resolution=60 AND ts=86400").fetchone()[0])
        self.assertEqual(bucket["samples"], 2)
        self.assertEqual(bucket["metrics"]["cpu_percent"]["sum"], 60)
        self.assertEqual(self.store.query("day", now=86460)["resolution_s"], 60)

    def test_monitor_collects_without_browser_requests(self):
        recorded = threading.Event()

        class History:
            next_cleanup = 0

            @staticmethod
            def record(resources):
                if resources["cpu_percent"] == 25:
                    recorded.set()

        monitor = MetricsMonitor(History(), lambda: {"cpu_percent": 25})
        monitor.start()
        self.addCleanup(monitor.stop)
        self.assertTrue(recorded.wait(2))
        self.assertEqual(monitor.snapshot()["cpu_percent"], 25)


if __name__ == "__main__":
    unittest.main()
