from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import grpc

spec = importlib.util.spec_from_file_location("xray_stats_test", Path(__file__).resolve().parents[2] / "protocol-images/mihomo/xray_stats.py")
xray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xray)


class XrayStatsTests(unittest.TestCase):
    def setUp(self):
        self.calls = {"traffic": 0, "online": 0}
        self.failure = None
        self.online_failure = None
        self.guard = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.server = grpc.server(self.pool)
        def traffic(request, context):
            with self.guard:
                self.calls["traffic"] += 1
            self.assertFalse(request.reset)
            self.assertEqual(request.pattern, "user>>>")
            if self.failure:
                context.abort(self.failure, "unavailable")
            response = xray.MESSAGES["QueryStatsResponse"]()
            for index in range(45):
                for direction, value in (("downlink", 100 + index), ("uplink", 10 + index)):
                    response.stat.add(name=f"user>>>user-{index}>>>traffic>>>{direction}", value=value)
            return response
        def online(request, context):
            with self.guard:
                self.calls["online"] += 1
            self.assertFalse(request.reset)
            self.assertTrue(request.name.endswith(">>>online"))
            if self.online_failure:
                context.abort(self.online_failure, "unavailable")
            return xray.MESSAGES["GetStatsResponse"](stat={"name": request.name, "value": 2 if "user-0>" in request.name else 0})
        handlers = {
            "QueryStats": grpc.unary_unary_rpc_method_handler(traffic, request_deserializer=xray.MESSAGES["QueryStatsRequest"].FromString, response_serializer=xray.MESSAGES["QueryStatsResponse"].SerializeToString),
            "GetStatsOnline": grpc.unary_unary_rpc_method_handler(online, request_deserializer=xray.MESSAGES["GetStatsRequest"].FromString, response_serializer=xray.MESSAGES["GetStatsResponse"].SerializeToString),
        }
        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("xray.app.stats.command.StatsService", handlers),))
        port = self.server.add_insecure_port("127.0.0.1:0")
        self.address = f"127.0.0.1:{port}"
        self.server.start()
        self.collector = xray.XrayStats()
        self.addCleanup(self.pool.shutdown)
        self.addCleanup(lambda: self.server.stop(0).wait())
        self.addCleanup(self.collector.close)

    def test_many_users_share_traffic_query_preserving_individual_counters(self):
        for index in range(45):
            row = self.collector.snapshot(self.address, f"user-{index}", True)
            self.assertEqual(row["rx_bytes"], 100 + index)
            self.assertEqual(row["tx_bytes"], 10 + index)
            self.assertEqual(row["active"], index == 0)
        self.assertEqual(self.calls, {"traffic": 1, "online": 45})
        self.assertEqual(self.collector.snapshot(self.address, "user-0")["online_ips"], 2)
        self.assertEqual(self.calls, {"traffic": 1, "online": 45})

    def test_concurrent_tabs_share_refresh_and_expired_cache_updates(self):
        with patch.object(xray.time, "monotonic", return_value=100) as clock:
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(pool.map(lambda _: self.collector.snapshot(self.address, "user-0"), range(16)))
            self.assertTrue(all(row["rx_bytes"] == 100 for row in rows))
            self.assertEqual(self.calls, {"traffic": 1, "online": 1})
            clock.return_value = 106
            self.collector.snapshot(self.address, "user-0")
            self.assertEqual(self.calls, {"traffic": 2, "online": 2})

    def test_failed_core_is_unknown_and_does_not_cause_per_user_retries(self):
        self.failure = grpc.StatusCode.UNAVAILABLE
        with patch.object(xray.time, "monotonic", return_value=100) as clock:
            for index in range(45):
                row = self.collector.snapshot(self.address, f"user-{index}")
                self.assertIsNone(row["active"])
                self.assertIsNone(row["rx_bytes"])
                self.assertFalse(row["stats_available"])
            self.assertEqual(self.calls, {"traffic": 1, "online": 0})
            self.failure = None
            clock.return_value = 106
            self.assertEqual(self.collector.snapshot(self.address, "user-0")["rx_bytes"], 100)

    def test_online_api_failure_keeps_traffic_and_missing_user_requires_enabled_policy(self):
        self.online_failure = grpc.StatusCode.UNIMPLEMENTED
        for index in range(3):
            row = self.collector.snapshot(self.address, f"user-{index}", True)
            self.assertTrue(row["stats_available"])
            self.assertFalse(row["activity_available"])
        self.assertEqual(self.calls["online"], 1)
        self.collector.close()
        self.online_failure = grpc.StatusCode.NOT_FOUND
        self.assertFalse(self.collector.snapshot(self.address, "user-1", True)["active"])
        self.assertIsNone(self.collector.snapshot(self.address, "user-2", False)["active"])
