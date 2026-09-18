import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("cpu_sampler", Path(__file__).resolve().parents[2] / "api/cpu_sampler.py")
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)


class CpuSamplerTests(unittest.TestCase):
    def test_nearby_and_concurrent_requests_share_a_full_sampling_window(self):
        sampler = cpu.CpuSampler()
        with patch.object(cpu.time, "monotonic", return_value=10) as clock, patch.object(cpu.Path, "read_text", side_effect=[
            "cpu 100 0 0 900 0 0 0 0 100 0", "cpu 120 0 0 980 0 0 0 0 120 0",
            "cpu 121 0 0 999 0 0 0 0 121 0",
        ]) as read:
            self.assertEqual(sampler.percent_used(), 0)
            clock.return_value = 11
            self.assertEqual(sampler.percent_used(), 20)  # guest is not counted twice
            clock.return_value = 11.001
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(lambda _: sampler.percent_used(), range(24))), [20] * 24)
            self.assertEqual(read.call_count, 2)
            clock.return_value = 12
            self.assertEqual(sampler.percent_used(), 5)

    def test_reset_and_unavailable_counters_do_not_report_overload(self):
        sampler = cpu.CpuSampler()
        with patch.object(cpu.time, "monotonic", side_effect=[1, 2, 3, 4]), patch.object(cpu.Path, "read_text", side_effect=[
            "cpu 100 0 0 900 0", "cpu 1 0 0 9 0", OSError("unavailable"), "cpu 1 0 0 9 0",
        ]):
            for _ in range(4):
                self.assertEqual(sampler.percent_used(), 0)
