"""Receive libev's own cumulative payload total over a private Unix datagram socket."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import uuid


class Traffic:
    def __init__(self, port, path):
        self.port, self.path = str(port), Path(path)
        self.epoch = uuid.uuid4().hex
        self.stopped = threading.Event()
        self.directory = None

    def record(self, message):
        if not message.startswith(b"stat: "):
            return
        values = json.loads(message[6:].rstrip(b"\0"))
        total = values.get(self.port)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, draft = tempfile.mkstemp(dir=self.path.parent, prefix=".traffic-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump({"used_bytes": total, "epoch": self.epoch, "updated_at": time.time()}, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(draft, self.path)
        finally:
            if os.path.exists(draft):
                os.unlink(draft)

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ss-stats-")
        self.address = str(Path(self.directory.name) / "stats.sock")
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.socket.bind(self.address)
        except OSError:
            self.socket.close()
            self.directory.cleanup()
            raise
        self.socket.settimeout(0.5)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def run(self):
        while not self.stopped.is_set():
            try:
                self.record(self.socket.recv(4096))
            except socket.timeout:
                continue
            except (ValueError, TypeError, AttributeError, OSError):
                # Telemetry failure cannot take down healthy proxy connections.
                continue

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=2)
        self.socket.close()
        self.directory.cleanup()
