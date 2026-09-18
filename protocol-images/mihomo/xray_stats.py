"""Read Xray counters over one gRPC channel, without spawning a CLI per user.

Wire fields and RPC names follow XTLS/Xray-core app/stats/command/command.proto:
https://github.com/XTLS/Xray-core/blob/main/app/stats/command/command.proto
Only the stable read-only subset is described here; counters are never reset.
"""
import threading
import time

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def messages():
    schema = descriptor_pb2.FileDescriptorProto(name="panel_xray_stats.proto", package="xray.app.stats.command", syntax="proto3")
    definitions = {
        "GetStatsRequest": [("name", 1, 9, False, ""), ("reset", 2, 8, False, "")],
        "Stat": [("name", 1, 9, False, ""), ("value", 2, 3, False, "")],
        "GetStatsResponse": [("stat", 1, 11, False, "Stat")],
        "QueryStatsRequest": [("pattern", 1, 9, False, ""), ("reset", 2, 8, False, "")],
        "QueryStatsResponse": [("stat", 1, 11, True, "Stat")],
    }
    for name, fields in definitions.items():
        message = schema.message_type.add(name=name)
        for key, number, kind, repeated, target in fields:
            field = message.field.add(name=key, number=number, type=kind, label=3 if repeated else 1)
            if target:
                field.type_name = ".xray.app.stats.command." + target
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    return {name: message_factory.GetMessageClass(pool.FindMessageTypeByName("xray.app.stats.command." + name)) for name in definitions}


MESSAGES = messages()
SERVICE = "/xray.app.stats.command.StatsService/"


class XrayStats:
    def __init__(self, ttl=5.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.channel = None
        self.address = None
        self.expires = 0.0
        self.counters = None
        self.online = {}
        self.online_unavailable = False

    def close(self):
        with self.lock:
            if self.channel is not None:
                self.channel.close()
            self.channel = None
            self.expires = 0.0

    def rpc(self, method, request, response):
        return self.channel.unary_unary(
            SERVICE + method, request_serializer=type(request).SerializeToString,
            response_deserializer=MESSAGES[response].FromString,
        )(request, timeout=2)

    def snapshot(self, address, email, online_enabled=False):
        with self.lock:
            if self.channel is None or self.address != address:
                if self.channel is not None:
                    self.channel.close()
                self.channel = grpc.insecure_channel(address)
                self.address = address
                self.expires = 0.0
            if time.monotonic() >= self.expires:
                self.counters = None
                self.online = {}
                self.online_unavailable = False
                try:
                    response = self.rpc("QueryStats", MESSAGES["QueryStatsRequest"](pattern="user>>>", reset=False), "QueryStatsResponse")
                    self.counters = {stat.name: stat.value for stat in response.stat}
                except grpc.RpcError:
                    pass
                # Cache failed reads too: an unavailable core must not cause
                # N timeouts or N subprocesses for N users or concurrent tabs.
                self.expires = time.monotonic() + self.ttl
            if self.counters is None:
                return {"active": None, "rx_bytes": None, "tx_bytes": None,
                        "stats_available": False, "activity_available": False}
            if email not in self.online and not self.online_unavailable:
                try:
                    response = self.rpc("GetStatsOnline", MESSAGES["GetStatsRequest"](name=f"user>>>{email}>>>online", reset=False), "GetStatsResponse")
                    self.online[email] = response.stat.value
                except grpc.RpcError as error:
                    self.online[email] = 0 if online_enabled and error.code() == grpc.StatusCode.NOT_FOUND else None
                    if error.code() != grpc.StatusCode.NOT_FOUND:
                        self.online_unavailable = True
            online = self.online.get(email)
            prefix = f"user>>>{email}>>>traffic>>>"
            return {"active": online > 0 if online is not None else None,
                    "online_ips": online, "rx_bytes": self.counters.get(prefix + "downlink", 0),
                    "tx_bytes": self.counters.get(prefix + "uplink", 0), "stats_available": True,
                    "activity_available": online is not None, "activity_source": "xray_online_20s"}
