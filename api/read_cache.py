"""Coalesce concurrent reads; cache only file-stamped version metadata."""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from pathlib import Path


class InFlightReads:
    """Join overlapping reads only; completed results and failures are never cached."""
    def __init__(self):
        self._lock = threading.Lock()
        self._pending = {}

    def run(self, key, read):
        with self._lock:
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._pending[key] = future
        if not owner:
            return future.result()
        try:
            value = read()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            return value
        finally:
            with self._lock:
                if self._pending.get(key) is future:
                    self._pending.pop(key)


def file_stamp(path: Path):
    try:
        value = path.stat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    except OSError:
        return None


class StampedReadCache:
    """Bounded metadata cache, not a cache of live runtime observations."""
    def __init__(self, capacity: int = 128):
        self._lock = threading.Lock()
        self._entries = {}
        self._pending = {}
        self._capacity = capacity

    def get(self, key, stamp, load, *, ttl: float = 60, valid=bool):
        if stamp is None:
            return load()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] == stamp and time.monotonic() < entry[1]:
                return entry[2]
            pending = self._pending.get(key)
            owner = pending is None or pending[0] != stamp
            if owner:
                future = Future()
                self._pending[key] = (stamp, future)
            else:
                future = pending[1]
        if not owner:
            return future.result()
        try:
            value = load()
            cacheable = valid(value)
        except BaseException as exc:
            with self._lock:
                if self._pending.get(key) == (stamp, future):
                    self._pending.pop(key)
            future.set_exception(exc)
            raise
        with self._lock:
            if self._pending.get(key) == (stamp, future):
                self._pending.pop(key)
                if cacheable:
                    if key not in self._entries and len(self._entries) >= self._capacity:
                        self._entries.pop(next(iter(self._entries)))
                    self._entries[key] = (stamp, time.monotonic() + ttl, value)
        future.set_result(value)
        return value
