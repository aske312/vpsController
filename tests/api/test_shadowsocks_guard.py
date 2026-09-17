import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "protocol-images/mihomo/modules/transport-shadowsocks/guard.py"
spec = importlib.util.spec_from_file_location("shadowsocks_guard", PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ShadowsocksGuardTests(unittest.TestCase):
    def run_child(self, source, stop=None):
        children, output = [], []
        popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        with patch.object(guard.subprocess, "Popen", side_effect=launch):
            started = time.monotonic()
            result = guard.supervise([sys.executable, "-u", "-c", source], stop, output.append)
        self.assertLess(time.monotonic() - started, 8)
        self.assertIsNotNone(children[0].poll(), "Guard must reap its child")
        return result, output

    def test_descriptor_exhaustion_stops_infinite_error_loop(self):
        result, output = self.run_child("import sys\nwhile True: sys.stderr.write('ERROR: accept: Too many open files\\n'); sys.stderr.flush()")
        self.assertEqual(result, 75)
        self.assertEqual(len(output), 1)
        self.assertIn("resource exhaustion", output[0])

    def test_log_flood_is_bounded_without_killing_healthy_child(self):
        result, output = self.run_child("import sys\nfor i in range(10000): print('ordinary diagnostic', flush=True)\nsys.exit(0)")
        self.assertEqual(result, 0)
        self.assertLessEqual(len(output), 20)

    def test_resource_error_is_detected_even_after_log_budget_is_exhausted(self):
        result, output = self.run_child("import sys,time\nfor i in range(100): print('ordinary diagnostic', flush=True)\nprint('ERROR: accept: Too many open files', flush=True)\ntime.sleep(30)")
        self.assertEqual(result, 75)
        self.assertIn("resource exhaustion", output[-1])
        self.assertLessEqual(len(output), 21)

    def test_normal_exit_and_crash_status_are_preserved(self):
        for code in (0, 7):
            with self.subTest(code=code):
                result, output = self.run_child(f"import sys; print('startup ok'); sys.exit({code})")
                self.assertEqual(result, code)
                self.assertEqual(output, ["startup ok"])

    def test_operator_stop_reaps_child_without_requesting_restart(self):
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)
        timer.start()
        try:
            result, _ = self.run_child("import time; time.sleep(30)", stop)
        finally:
            timer.cancel()
        self.assertEqual(result, 0)

    @unittest.skipIf(sys.platform == "win32", "Linux resource limits")
    def test_actual_emfile_from_exhausted_process(self):
        result, output = self.run_child("import os,resource,sys,time\nresource.setrlimit(resource.RLIMIT_NOFILE,(64,64))\nfds=[]\ntry:\n while True: fds.append(os.open('/dev/null',os.O_RDONLY))\nexcept OSError as error:\n print('ERROR: accept: '+error.strerror,flush=True)\ntime.sleep(30)")
        self.assertEqual(result, 75)
        self.assertIn("resource exhaustion", output[-1])
