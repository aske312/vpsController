import unittest
from unittest.mock import patch

from tests.api.support import api
from system_metrics import CpuSampler, collect_resources


class SystemMetricsTests(unittest.TestCase):
    def test_cpu_is_shared_nonblocking_and_has_no_synthetic_zero(self):
        now = [0.0]
        text = ["cpu 10 0 0 90 0 0 0 0 50 0"]
        sampler = CpuSampler(lambda: text[0], lambda: now[0])
        self.assertIsNone(sampler.sample())
        now[0] = 1
        text[0] = "cpu 20 0 0 100 0 0 0 0 60 0"
        self.assertEqual(sampler.sample(), 50)
        for _ in range(20):
            self.assertEqual(sampler.sample(), 50)
        now[0] = 2
        text[0] = "cpu 0 0 0 0 0 0 0 0"
        self.assertIsNone(sampler.sample())
        now[0] = 3
        text[0] = "invalid"
        self.assertIsNone(sampler.sample())
        now[0] = 4
        text[0] = "cpu 1 0 0 1 0 0 0 0"
        self.assertIsNone(sampler.sample())
        now[0] = 5
        text[0] = "cpu 1 0 0 11 0 0 0 0"
        self.assertEqual(sampler.sample(), 0)

    def test_resource_failures_are_independent(self):
        def broken():
            raise OSError("private diagnostic")
        result = collect_resources({"cpu": lambda: (None, 2), "memory": broken, "disk": lambda: (100, 60),
                                    "network": lambda: (0, 0), "load": lambda: (float("nan"), 1, 2), "uptime": lambda: (42,)})
        self.assertIsNone(result["memory_total"])
        self.assertIsNone(result["load1"])
        self.assertIsNone(result["cpu_percent"])
        self.assertEqual(result["cpu_count"], 2)
        self.assertEqual(result["disk_available"], 60)
        self.assertEqual(result["network_rx"], 0)
        self.assertEqual(result["uptime_s"], 42)
        self.assertFalse(result["observations"]["memory"]["available"])
        self.assertTrue(result["observations"]["network"]["available"])
        self.assertNotIn("private", str(result))

    def test_overview_survives_failed_memory_and_disk_observations(self):
        with patch.object(api, "memory_info", side_effect=OSError), patch.object(api, "disk_info", side_effect=OSError), \
             patch.object(api, "network_info", return_value=(10, 20)), patch.object(api, "cpu_usage_percent", return_value=None), \
             patch.object(api, "run", return_value=""):
            result = api.overview()
        self.assertIsNone(result["resources"]["memory_total"])
        self.assertIsNone(result["resources"]["disk_total"])
        self.assertEqual(result["resources"]["network_rx"], 10)
        self.assertIn("server", result)

    def test_client_failure_does_not_hide_live_resources_or_security(self):
        with patch.object(api, "system_resources", return_value={"cpu_percent": 12}), \
             patch.object(api, "all_client_dump", side_effect=OSError("secret diagnostic")), \
             patch.object(api, "run", return_value=""):
            result = api.live_status()
        self.assertEqual(result["resources"]["cpu_percent"], 12)
        self.assertIsNone(result["clients"])
        self.assertIsNone(result["protocols"]["wg"])
        self.assertIn("clients", result["unavailable"])
        self.assertIsInstance(result["security"], dict)
        self.assertNotIn("secret", str(result))
