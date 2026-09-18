import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
from protocol_health import TrafficSampler, protocol_health


class ProtocolHealthTests(unittest.TestCase):
    def test_states_follow_diagnostics_clients_and_current_traffic(self):
        for base in ("healthy", "warning", "critical", "pending"):
            for clients in (0, 1):
                for rate in (0, 10):
                    diag = {"checked_at": "now", "status": base, "checks": [{"id": "service", "ok": True}]}
                    traffic = {"available": True, "rx_bps": rate, "tx_bps": 0}
                    state = {"healthy": "READY", "warning": "WARN", "critical": "ERROR", "pending": "WARN"}[base]
                    if clients and rate:
                        state = "WORKS" if state == "READY" else "WARN"
                    with self.subTest(base=base, clients=clients, rate=rate):
                        self.assertEqual(protocol_health({"service_active": True}, diag, traffic, clients)["state"], state)
        self.assertEqual(protocol_health({"service_active": False}, {}, {}, 0)["state"], "ERROR")

    def test_first_sample_old_totals_reset_and_gap_do_not_mean_traffic(self):
        now = [0]
        sampler = TrafficSampler(lambda: now[0])
        self.assertFalse(sampler.sample("wg", 100000, 200000)["available"])
        now[0] = 6
        sample = sampler.sample("wg", 100060, 200120)
        self.assertEqual(sample["rx_bps"], 10)
        self.assertEqual(sampler.sample("wg", 100060, 200120), sample)
        now[0] = 12
        self.assertEqual(sampler.sample("wg", 100060, 200120)["rx_bps"], 0)
        now[0] = 18
        self.assertFalse(sampler.sample("wg", 1, 2)["available"])
        now[0] = 60
        self.assertFalse(sampler.sample("wg", 100, 200)["available"])

    def test_stale_checks_and_failed_listener_cannot_be_ready(self):
        diag = {"checked_at": "now", "status": "healthy", "checks": [{"id": "listener", "ok": True}]}
        self.assertEqual(protocol_health({"active": True}, diag, {}, 1, stale=True)["state"], "WARN")
        diag["checks"][0]["ok"] = False
        self.assertEqual(protocol_health({"active": True}, diag, {}, 1)["state"], "ERROR")

    def test_every_required_port_must_listen(self):
        with patch.object(api, "run", return_value="UNCONN 0 0 0.0.0.0:500 0.0.0.0:*"):
            self.assertTrue(api._diagnostic_listener([500], True)[0])
            self.assertFalse(api._diagnostic_listener([500, 4500], True)[0])

    def test_openvpn_tcp_is_not_plain_tls(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "settings.json"
            settings.write_text('{"protocol":"tcp","port":1194}')
            with patch.object(api, "OPENVPN_SETTINGS", settings):
                _, ports, udp, tls, _ = api._direct_diagnostic_target("openvpn")
            self.assertEqual(ports, [1194])
            self.assertFalse(udp)
            self.assertFalse(tls)

    def test_shadowsocks_checks_only_configured_transports(self):
        for mode in ("tcp_only", "udp_only", "tcp_and_udp", "broken"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                (Path(folder) / "client.json").write_text(json.dumps({"server_port": 12345, "mode": mode}))
                with patch.object(api, "PUBLIC_IP_ENDPOINT", "127.0.0.1"), patch.object(api, "SHADOWSOCKS_CONFIG_DIR", Path(folder)), patch.object(api, "run", return_value="active"), patch.object(api, "_diagnostic_listener", return_value=(True, "listening")) as listener, patch.object(api, "_diagnostic_resolve", return_value=["127.0.0.1"]), patch.object(api, "direct_diagnostic_cache", {}):
                    result = api.direct_protocol_diagnostics("shadowsocks", force=True)
                self.assertEqual(result["status"], "critical" if mode == "broken" else "healthy")
                # First call is the generic target probe; final probes reflect SS modes.
                transports = {call.args[1] for call in listener.call_args_list[1:]}
                expected = {False} if mode == "tcp_only" else {True} if mode == "udp_only" else {False, True} if mode == "tcp_and_udp" else set()
                self.assertEqual(transports, expected)

    def test_status_queues_initial_diagnostics_and_exposes_canonical_health(self):
        with patch.object(api, "protocol_status_data", return_value={"service_active": True, "peers": 0}), patch.object(api, "direct_diagnostic_cache", {}), patch.object(api, "schedule_protocol_diagnostics", return_value=True) as schedule:
            result = api.protocol_status("trojan")
        schedule.assert_called_once_with("trojan")
        self.assertEqual(result["health"]["state"], "WARN")
        self.assertTrue(result["health"]["checking"])
        self.assertEqual(result["diagnostics"]["status"], "pending")

    def test_scheduler_deduplicates_pending_protocol(self):
        with patch.object(api, "protocol_diagnostic_pending", set()), patch.object(api.protocol_diagnostic_executor, "submit") as submit:
            api.schedule_protocol_diagnostics("trojan")
            api.schedule_protocol_diagnostics("trojan")
            submit.assert_called_once()
