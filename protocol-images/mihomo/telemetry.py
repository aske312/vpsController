"""Read-only sing-box connection events, attributed by authenticated inbound user."""
import threading
import time
from collections import OrderedDict

import grpc
from telemetry_pb2 import ConnectionEvents, SubscribeConnectionsRequest


class ConnectionTelemetry:
    def __init__(self, address, secret):
        self.address = address
        self.secret = secret
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.ready = False
        self.since = None
        self.rows = {}
        self.closed = OrderedDict()
        self.totals = {}
        self.channel = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="mihomo-quic-stats")
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.channel:
            self.channel.close()
        if self.thread:
            self.thread.join(timeout=3)

    def apply(self, message):
        with self.lock:
            if message.reset:
                # A new observation session starts with the core's available
                # snapshot (including recent closed connections). Never claim
                # these are lifetime totals or add a replayed snapshot twice.
                self.rows.clear()
                self.closed.clear()
                self.totals.clear()
                self.since = time.time()
            for event in message.events:
                old = self.rows.get(event.id) or self.closed.get(event.id)
                if event.HasField("connection"):
                    conn = event.connection
                    if not conn.user:
                        continue
                    row = {"user": conn.user, "up": max(0, conn.uplinkTotal), "down": max(0, conn.downlinkTotal)}
                elif old:
                    row = {**old, "up": old["up"] + max(0, event.uplinkDelta), "down": old["down"] + max(0, event.downlinkDelta)}
                else:
                    continue
                total = self.totals.setdefault(row["user"], {"up": 0, "down": 0})
                for key in ("up", "down"):
                    total[key] += max(0, row[key] - (old[key] if old else 0))
                if event.type == 2 or event.closedAt or (event.HasField("connection") and event.connection.closedAt):
                    self.rows.pop(event.id, None)
                    self.closed[event.id] = row
                    self.closed.move_to_end(event.id)
                    if len(self.closed) > 2048:
                        self.closed.popitem(last=False)
                else:
                    self.rows[event.id] = row
            self.ready = True

    def snapshot(self, user):
        with self.lock:
            total = self.totals.get(user, {})
            count = sum(row["user"] == user for row in self.rows.values())
            return {"active": count > 0 if self.ready else None,
                    "active_connections": count if self.ready else None,
                    "rx_bytes": total.get("down", 0) if self.ready else None,
                    "tx_bytes": total.get("up", 0) if self.ready else None,
                    "stats_available": self.ready, "activity_available": self.ready,
                    "traffic_scope": "observation", "observed_since": self.since}

    def run(self):
        while not self.stopped.is_set():
            try:
                with grpc.insecure_channel(self.address, options=[
                    ("grpc.keepalive_time_ms", 10000), ("grpc.keepalive_timeout_ms", 3000),
                    ("grpc.keepalive_permit_without_calls", 1),
                ]) as channel:
                    self.channel = channel
                    call = channel.unary_stream("/daemon.StartedService/SubscribeConnections",
                                                request_serializer=SubscribeConnectionsRequest.SerializeToString,
                                                response_deserializer=ConnectionEvents.FromString)
                    for message in call(SubscribeConnectionsRequest(interval=1_000_000_000),
                                        metadata=(("authorization", f"Bearer {self.secret}"),)):
                        if self.stopped.is_set():
                            break
                        self.apply(message)
            except grpc.RpcError:
                pass
            finally:
                with self.lock:
                    self.ready = False
                self.channel = None
            self.stopped.wait(2)
