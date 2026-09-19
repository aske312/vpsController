"""Bounded local telemetry history; no host identity, credentials or traffic content."""
from __future__ import annotations

import copy
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DEFAULTS = {"enabled": True, "raw_hours": 24, "minute_days": 7, "hour_days": 90, "disk_limit_mb": 64}
FIELDS = ("cpu_percent", "load1", "load5", "load15", "memory_total", "memory_available", "disk_total", "disk_available",
          "network_rx", "network_tx", "uptime_s", "memory_used_percent", "disk_used_percent", "rx_bps", "tx_bps", "rx_delta", "tx_delta")
COUNTERS = {"network_rx", "network_tx", "uptime_s"}
DELTAS = {"rx_delta", "tx_delta"}
PERIODS = {"live": (300, 3), "day": (86400, 60), "week": (604800, 3600), "quarter": (7776000, 3600)}


class SettingsConflict(ValueError):
    pass


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


class MetricsHistory:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.initialized = False
        self.next_cleanup = 0

    @contextmanager
    def database(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
            os.chmod(self.path, 0o600)
            connection = sqlite3.connect(self.path, timeout=1)
            try:
                if not self.initialized:
                    connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                    connection.executescript("""
                        CREATE TABLE IF NOT EXISTS raw (ts INTEGER PRIMARY KEY, data TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS buckets (resolution INTEGER, ts INTEGER, data TEXT NOT NULL, PRIMARY KEY(resolution, ts));
                        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    """)
                    connection.execute("INSERT OR IGNORE INTO meta VALUES ('settings', ?)", (json.dumps({**DEFAULTS, "revision": uuid.uuid4().hex}),))
                    connection.commit()
                    self.initialized = True
                # This PRAGMA belongs to the connection, not the database file.
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                pages = max(64, self.policy(connection)["disk_limit_mb"] * 1024 * 1024 // page_size)
                connection.execute(f"PRAGMA max_page_count={pages}")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def policy(db):
        return json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])

    def settings(self):
        with self.database() as db:
            result = self.policy(db)
            trimmed = db.execute("SELECT value FROM meta WHERE key='trimmed_at'").fetchone()
        return {**result, "used_bytes": self.path.stat().st_size, "trimmed_at": json.loads(trimmed[0]) if trimmed else None}

    def configure(self, values: dict, revision: str):
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.policy(db)["revision"] != revision:
                raise SettingsConflict("Настройки хранения изменились; обновите данные перед сохранением")
            settings = {key: values[key] for key in DEFAULTS}
            settings["revision"] = uuid.uuid4().hex
            db.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))
        self.next_cleanup = 0
        return self.settings()

    def record(self, resources: dict, now: float | None = None):
        now = time.time() if now is None else now
        ts = int(now // 3) * 3
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = self.policy(db)
            if now >= self.next_cleanup:
                self.prune(db, policy, now)
                self.next_cleanup = now + 60
            if not policy["enabled"]:
                return
            if db.execute("SELECT 1 FROM raw WHERE ts=?", (ts,)).fetchone():
                return
            values = {key: number(resources.get(key)) for key in FIELDS}
            for prefix in ("memory", "disk"):
                total, available = values[f"{prefix}_total"], values[f"{prefix}_available"]
                values[f"{prefix}_used_percent"] = 100 * (total - available) / total if total and available is not None and available <= total else None
            previous = db.execute("SELECT ts,data FROM raw ORDER BY ts DESC LIMIT 1").fetchone()
            if previous and 0 < ts - previous[0] <= 30:
                old = json.loads(previous[1])
                rebooted = values["uptime_s"] is not None and old.get("uptime_s") is not None and values["uptime_s"] < old["uptime_s"]
                for direction in ("rx", "tx"):
                    current, before = values[f"network_{direction}"], old.get(f"network_{direction}")
                    if not rebooted and current is not None and before is not None and current >= before:
                        values[f"{direction}_delta"] = current - before
                        values[f"{direction}_bps"] = (current - before) / (ts - previous[0])
            db.execute("INSERT INTO raw VALUES (?,?)", (ts, json.dumps(values)))
            for resolution in (60, 3600):
                bucket = ts // resolution * resolution
                existing = db.execute("SELECT data FROM buckets WHERE resolution=? AND ts=?", (resolution, bucket)).fetchone()
                aggregate = json.loads(existing[0]) if existing else {"samples": 0, "metrics": {}}
                aggregate["samples"] += 1
                for key, value in values.items():
                    if value is None:
                        continue
                    field = aggregate["metrics"].setdefault(key, {"sum": 0, "count": 0, "min": value, "max": value, "last": value})
                    field.update(sum=field["sum"] + (0 if key in COUNTERS else value), count=field["count"] + 1,
                                 min=min(field["min"], value), max=max(field["max"], value), last=value)
                db.execute("INSERT OR REPLACE INTO buckets VALUES (?,?,?)", (resolution, bucket, json.dumps(aggregate)))

    @staticmethod
    def prune(db, policy, now):
        # Every raw sample is aggregated in the same transaction before retention can remove it.
        db.execute("DELETE FROM raw WHERE ts < ?", (now - policy["raw_hours"] * 3600,))
        for resolution, days in ((60, policy["minute_days"]), (3600, policy["hour_days"])):
            db.execute("DELETE FROM buckets WHERE resolution=? AND ts < ?", (resolution, now - days * 86400))
        budget = policy["disk_limit_mb"] * 1024 * 1024
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        for table, where in (("raw", "1"), ("buckets", "resolution=60"), ("buckets", "resolution=3600")):
            while (db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]) * page_size > budget * .8:
                deleted = db.execute(f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} ORDER BY ts LIMIT 500)").rowcount
                if not deleted:
                    break
                db.execute("INSERT OR REPLACE INTO meta VALUES ('trimmed_at', ?)", (json.dumps(now),))
        db.execute("PRAGMA incremental_vacuum").fetchall()
        # Bound physical growth between maintenance passes too.
        db.execute(f"PRAGMA max_page_count={max(64, int(budget // page_size))}")

    def query(self, period="live", now=None):
        now = time.time() if now is None else now
        seconds, resolution = PERIODS[period]
        end = int(now // resolution) * resolution
        start = end - seconds
        with self.database() as db:
            if resolution == 3:
                rows = dict(db.execute("SELECT ts,data FROM raw WHERE ts BETWEEN ? AND ? ORDER BY ts", (start, end)).fetchall())
            else:
                rows = dict(db.execute("SELECT ts,data FROM buckets WHERE resolution=? AND ts BETWEEN ? AND ? ORDER BY ts", (resolution, start, end)).fetchall())
        points = []
        for timestamp in range(start, end + 1, resolution):
            record = json.loads(rows[timestamp]) if timestamp in rows else {}
            if resolution == 3:
                values = record
            else:
                values = {key: field["last"] if key in COUNTERS else field["sum"] if key in DELTAS else field["sum"] / field["count"]
                          for key, field in record.get("metrics", {}).items() if field["count"]}
            points.append({"at": timestamp, **{key: values.get(key) for key in ("cpu_percent", "memory_used_percent", "disk_used_percent", "rx_bps", "tx_bps")}})
        return {"period": period, "resolution_s": resolution, "points": points, "settings": self.settings()}


class MetricsMonitor:
    def __init__(self, history: MetricsHistory, sample):
        self.history, self.sample = history, sample
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.error = ""
        self.thread = threading.Thread(target=self.run, name="system-metrics", daemon=True)

    def run(self):
        while not self.stop_event.is_set():
            try:
                resources = self.sample()
                with self.lock:
                    self.latest = (time.monotonic(), resources)
                self.history.record(resources)
                self.error = ""
            except (OSError, sqlite3.Error, ValueError):
                self.error = "История метрик временно недоступна"
                self.history.next_cleanup = 0
            self.stop_event.wait(3)

    def snapshot(self):
        with self.lock:
            if not self.latest:
                return None
            timestamp, resources = self.latest
            return {**copy.deepcopy(resources), "stale": time.monotonic() - timestamp > 9}

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
