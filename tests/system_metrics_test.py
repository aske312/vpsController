import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from system_metrics import CpuSampler, collect_resources


class SystemMetricsTests(unittest.TestCase):
    def test_cpu_sampler_is_shared_and_non_blocking(self):
        now = [0.0]
        text = ["cpu 10 0 0 90 0 0 0 0 50 0"]
        sampler = CpuSampler(lambda: text[0], lambda: now[0])
        self.assertIsNone(sampler.sample())
        now[0] = 1
        text[0] = "cpu 20 0 0 100 0 0 0 0 60 0"
        self.assertEqual(sampler.sample(), 50)
        self.assertEqual(sampler.sample(), 50)

    def test_resource_failures_do_not_hide_other_metrics(self):
        def broken():
            raise OSError("private diagnostic")

        result = collect_resources({
            "cpu": lambda: (None, 2),
            "load": lambda: (float("nan"), 1, 2),
            "memory": broken,
            "disk": lambda: (100, 60),
            "network": lambda: (10, 20),
            "uptime": lambda: (42,),
        })
        self.assertIsNone(result["memory_total"])
        self.assertIsNone(result["load1"])
        self.assertEqual(result["disk_available"], 60)
        self.assertEqual(result["network_rx"], 10)
        self.assertFalse(result["observations"]["memory"]["available"])
        self.assertTrue(result["observations"]["network"]["available"])
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()
