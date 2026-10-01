"""Independent resource observations and a shared, non-blocking CPU sampler."""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class CpuSampler:
    def __init__(self, read=None, clock=time.monotonic):
        self.read = read or (lambda: Path("/proc/stat").read_text())
        self.clock = clock
        self.lock = threading.Lock()
        self.previous = None
        self.value = None

    def sample(self) -> float | None:
        with self.lock:
            now = self.clock()
            if self.previous and 0 <= now - self.previous[0] < 1:
                return self.value
            try:
                row = self.read().splitlines()[0].split()
                if row[0] != "cpu" or len(row) < 6:
                    raise ValueError("Invalid CPU counters")
                values = [int(value) for value in row[1:9]]
                if any(value < 0 for value in values):
                    raise ValueError("Invalid CPU counters")
                # guest/guest_nice are already included in user/nice.
                total, idle = sum(values), values[3] + values[4]
            except (OSError, ValueError, IndexError):
                self.previous = None
                self.value = None
                return None
            previous = self.previous
            self.previous = (now, total, idle)
            self.value = None
            if previous and now > previous[0]:
                total_delta, idle_delta = total - previous[1], idle - previous[2]
                if total_delta > 0 and 0 <= idle_delta <= total_delta:
                    self.value = round((total_delta - idle_delta) / total_delta * 100, 1)
            return self.value


RESOURCE_FIELDS = {
    "cpu": ("cpu_percent", "cpu_count"),
    "load": ("load1", "load5", "load15"),
    "memory": ("memory_total", "memory_available"),
    "disk": ("disk_total", "disk_available"),
    "network": ("network_rx", "network_tx"),
    "uptime": ("uptime_s",),
}


def collect_resources(readers: dict) -> dict:
    resources = {}
    observations = {}
    for group, fields in RESOURCE_FIELDS.items():
        values = [None] * len(fields)
        try:
            candidate = readers[group]()
            if len(candidate) != len(fields):
                raise ValueError("Invalid resource sample")
            values = [value if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None for value in candidate]
        except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
            pass
        resources.update(zip(fields, values))
        available = all(value is not None for value in values)
        observations[group] = {
            "available": available,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "reason": "" if available else "Результат измерения пока недоступен",
        }
    resources["observations"] = observations
    return resources
