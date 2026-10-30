"""Actual measured bytes, restart/replay handling and persistent ownership."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import os
import socket
import time
import threading
import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT, managed_mihomo_fixture
import traffic_usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        managed_mihomo_fixture(self)
        self.profile = {"id": "usage", "devices": [{"id": "phone"}, {"id": "laptop"}], "connections": [
            {"id": "a", "device_id": "phone", "component": "transport-reality", "credential": {"uuid": "a"}},
            {"id": "b", "device_id": "laptop", "component": "transport-reality", "credential": {"uuid": "b"}},
        ]}

    def sample(self, rows, profile=None):
        return traffic_usage.snapshot(profile or self.profile, rows, manager.DATA_ROOT / "usage", manager.load_json, manager.atomic_json)

    def test_background_collection_is_independent_of_ui_and_one_broken_profile(self):
        stopped = threading.Event()
        ticks = []
        def wait(_):
            ticks.append(True)
            if len(ticks) == 2:
                stopped.set()
        def sample(item, **_):
            if item["id"] == "bad":
                raise OSError("unavailable")
        with patch.object(manager, "component_is_managed", return_value=True), patch.object(manager, "profiles", return_value=[{"id": "bad"}, {"id": "good"}]), patch.object(manager, "profile_stats_payload", side_effect=sample) as collect, patch.object(stopped, "wait", side_effect=wait), patch.object(manager.logger, "error") as log:
            manager.traffic_worker(stopped)
        self.assertEqual([call.args[0]["id"] for call in collect.call_args_list], ["bad", "good", "bad", "good"])
        self.assertEqual(log.call_count, 1)

    def test_persistence_idempotency_devices_and_counter_reset(self):
        rows = {"a": {"rx_bytes": 100, "tx_bytes": 30}, "b": {"rx_bytes": 200, "tx_bytes": 70}}
        for _ in range(3):
            result = self.sample(rows)
            self.assertEqual(result["summary"]["used_bytes"], 400)
        self.assertEqual(result["devices"]["phone"]["used_bytes"], 130)
        rows["a"] = {"rx_bytes": 20, "tx_bytes": 10}
        self.assertEqual(self.sample(rows)["summary"]["used_bytes"], 430)
        changed = deepcopy(self.profile)
        changed["connections"][0]["credential"]["sni"] = "changed.example"
        self.assertEqual(self.sample(rows, changed)["summary"]["used_bytes"], 430)
        # Removing/replacing a preset cannot erase real previously measured usage.
        changed["connections"] = []
        self.assertEqual(self.sample({}, changed)["summary"]["used_bytes"], 430)
        self.assertTrue(result["summary"]["stats_partial"])

    def test_unknown_and_invalid_samples_are_not_zero_or_fake_totals(self):
        result = self.sample({"a": {"rx_bytes": None, "tx_bytes": None}})
        self.assertIsNone(result["summary"]["used_bytes"])
        self.assertFalse(result["summary"]["stats_available"])
        for value in (-1, True, float("nan"), "123"):
            self.assertIsNone(traffic_usage.measured_bytes({"used_bytes": value}))
        self.sample({"a": {"rx_bytes": 20, "tx_bytes": 10}})
        self.assertEqual(self.sample({})["devices"]["phone"]["used_bytes"], 30)

    def test_quic_reconnect_snapshot_is_not_counted_twice(self):
        def row(total, epoch):
            return {"used_bytes": total, "traffic_scope": "observation", "observed_since": epoch}
        self.assertEqual(self.sample({"a": row(100, 1)})["summary"]["used_bytes"], 100)
        self.assertEqual(self.sample({"a": row(120, 1)})["summary"]["used_bytes"], 120)
        self.assertEqual(self.sample({"a": row(110, 2)})["summary"]["used_bytes"], 120)
        self.assertEqual(self.sample({"a": row(130, 2)})["summary"]["used_bytes"], 140)

    def test_native_ss_epoch_handles_restart_even_when_new_counter_is_larger(self):
        self.sample({"a": {"used_bytes": 100, "counter_epoch": "old"}})
        self.assertEqual(self.sample({"a": {"used_bytes": 150, "counter_epoch": "new"}})["summary"]["used_bytes"], 250)

    def test_manager_real_samples_feed_usage_without_latency_probes(self):
        with patch.object(manager, "normalize_profile", side_effect=lambda value: value), patch.object(manager, "reality_profile_stats", return_value={"rx_bytes": 100, "tx_bytes": 50}), patch.object(manager, "endpoint_latency_ms") as ping:
            result = manager.profile_stats_payload(self.profile, collect_latency=False)
        self.assertEqual(result["usage"]["summary"]["used_bytes"], 300)
        self.assertEqual(result["usage"]["devices"]["phone"]["used_bytes"], 150)
        ping.assert_not_called()

    def test_missing_wireguard_peer_is_unknown(self):
        profile = deepcopy(self.profile)
        profile["connections"][0]["component"] = "transport-wg"
        profile["connections"] = profile["connections"][:1]
        with patch.object(manager, "normalize_profile", side_effect=lambda value: value), patch.object(manager, "wg_like_dump", return_value={}):
            result = manager.profile_stats_payload(profile)
        self.assertFalse(result["connections"]["a"]["stats_available"])
        self.assertIsNone(result["usage"]["summary"]["used_bytes"])

    def test_ss_uses_native_payload_sample_and_never_process_ip_accounting(self):
        path = manager.DATA_ROOT / "shadowsocks-traffic" / "instance.json"
        manager.atomic_json(path, {"used_bytes": 1234, "epoch": "first", "updated_at": 100})
        with patch.object(manager.time, "time", return_value=110), patch.object(manager, "run") as run, patch.object(manager, "systemctl_active", return_value=True):
            row = manager.shadowsocks_profile_stats("profile", 1234, "instance")
        self.assertEqual(row["used_bytes"], 1234)
        self.assertIsNone(row["rx_bytes"])
        self.assertFalse(any("IPIngressBytes" in str(call) or "IPEgressBytes" in str(call) for call in run.call_args_list))
        with patch.object(manager.time, "time", return_value=1000), patch.object(manager, "run"), patch.object(manager, "systemctl_active", return_value=True):
            self.assertIsNone(manager.shadowsocks_profile_stats("profile", 1234, "instance")["used_bytes"])

    def test_libev_report_parser_persists_only_own_port_and_native_total(self):
        spec = importlib.util.spec_from_file_location("ss_native_traffic", ROOT / "protocol-images/mihomo/modules/transport-shadowsocks/traffic.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = manager.DATA_ROOT / "ss.json"
        receiver = module.Traffic(1234, path)
        receiver.record(b'stat: {"4321":999}')
        self.assertFalse(path.exists())
        receiver.record(b'stat: {"1234":12345}\x00')
        self.assertEqual(json.loads(path.read_text())["used_bytes"], 12345)
        receiver.record(b'stat: {"1234":-50}')
        self.assertEqual(json.loads(path.read_text())["used_bytes"], 12345)

    @unittest.skipIf(os.name == "nt", "Unix datagram sockets require Linux")
    def test_libev_datagram_receiver_lifecycle(self):
        spec = importlib.util.spec_from_file_location("ss_native_traffic", ROOT / "protocol-images/mihomo/modules/transport-shadowsocks/traffic.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = manager.DATA_ROOT / "ss-socket.json"
        with module.Traffic(1234, path) as receiver, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            address = receiver.address
            sender.sendto(b'stat: {"1234":9876}\x00', address)
            deadline = time.monotonic() + 2
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(json.loads(path.read_text())["used_bytes"], 9876)
        self.assertFalse(Path(address).exists())
