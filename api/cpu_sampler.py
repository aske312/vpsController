"""A shared minimum sampling window for simultaneous dashboard requests."""
from pathlib import Path
import threading
import time


class CpuSampler:
    def __init__(self, interval=1.0):
        self.interval = interval
        self.lock = threading.Lock()
        self.previous = None
        self.sampled_at = 0.0
        self.percent = 0.0

    def percent_used(self):
        with self.lock:
            now = time.monotonic()
            if self.previous is not None and now - self.sampled_at < self.interval:
                return self.percent
            try:
                # guest and guest_nice are already included in user and nice.
                values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
                total, idle = sum(values), values[3] + values[4]
            except (OSError, ValueError, IndexError):
                return 0.0
            if self.previous is not None:
                elapsed = total - self.previous[0]
                idle_delta = idle - self.previous[1]
                self.percent = round(max(0.0, min(100.0, 100 * (elapsed - idle_delta) / elapsed)), 1) if elapsed > 0 and idle_delta >= 0 else 0.0
            self.previous = (total, idle)
            self.sampled_at = now
            return self.percent
