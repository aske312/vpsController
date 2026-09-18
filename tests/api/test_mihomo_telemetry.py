import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import yaml
import grpc
from tests.api.support import manager, free_port, wait_port
from telemetry import ConnectionTelemetry
from telemetry_pb2 import Connection, ConnectionEvent, ConnectionEvents, SubscribeConnectionsRequest


class TelemetryTests(unittest.TestCase):
    def test_package_version_discovery_is_shared_and_invalidated_after_mutation(self):
        with patch.dict(manager.apt_versions_cache, {}, clear=True), patch.object(manager.time, "monotonic", return_value=100) as clock, patch.object(manager, "_apt_package_versions", return_value=("1", "2")) as discover:
            self.assertEqual(manager.apt_package_versions("amneziawg"), ("1", "2"))
            self.assertEqual(manager.apt_package_versions("amneziawg"), ("1", "2"))
            self.assertEqual(discover.call_count, 1)
            clock.return_value = 161
            manager.apt_package_versions("amneziawg")
            self.assertEqual(discover.call_count, 2)
            @manager.module_mutation("update")
            def update(_):
                self.assertEqual(manager.apt_package_versions("amneziawg"), ("1", "2"))
                return "updated"
            self.assertEqual(update("transport-awg"), "updated")
            manager.apt_package_versions("amneziawg")
            self.assertEqual(discover.call_count, 4)

    def test_profile_and_device_totals_keep_available_channel_traffic(self):
        profile = {"id": "partial", "devices": [{"id": "common"}], "connections": [
            {"id": "ss", "device_id": "common", "component": "transport-shadowsocks", "credential": {}},
            {"id": "quic", "device_id": "common", "component": "transport-tuic", "credential": {}},
        ]}
        with patch.object(manager, "normalize_profile", return_value=profile), patch.object(manager, "shadowsocks_profile_stats", return_value={"rx_bytes": 1024, "tx_bytes": 256, "stats_available": True}), patch.object(manager, "quic_profile_stats", return_value={"rx_bytes": None, "tx_bytes": None, "stats_available": False}):
            stats = manager.profile_stats_payload(profile)
        for row in (stats["summary"], stats["devices"]["common"]):
            self.assertTrue(row["stats_available"])
            self.assertTrue(row["stats_partial"])
            self.assertEqual(row["rx_bytes"], 1024)
            self.assertEqual(row["tx_bytes"], 256)

    def test_maintenance_failure_does_not_disable_other_protocols(self):
        stopped = threading.Event()
        with patch.object(stopped, "wait", side_effect=lambda _: stopped.set()), patch.object(manager, "cleanup_profile_transitions"), patch.object(manager, "ensure_reality_telemetry", side_effect=RuntimeError("unavailable")), patch.object(manager, "ensure_quic_telemetry") as quic, patch.object(manager.logger, "error"):
            manager.transition_worker(stopped)
        self.assertEqual([call.args[0] for call in quic.call_args_list], ["transport-hysteria2", "transport-tuic"])

    def test_reality_activity_uses_shared_collector_without_per_user_commands(self):
        for value in (True, False, None):
            with self.subTest(active=value):
                row = {"active": value, "rx_bytes": 42}
                with patch.object(manager, "reality_api_server", return_value="127.0.0.1:1"), patch.object(manager.xray_telemetry, "snapshot", return_value=row) as snapshot, patch.object(manager, "run") as run:
                    stats = manager.reality_profile_stats("a", "b")
                self.assertIs(stats["active"], value)
                self.assertEqual(stats["rx_bytes"], 42)
                self.assertEqual(snapshot.call_args.args[:2], ("127.0.0.1:1", "mihomo-a-b"))
                run.assert_not_called()

    def test_counts_users_closed_traffic_and_disconnects(self):
        collector = ConnectionTelemetry("127.0.0.1:1", "test")
        self.assertIsNone(collector.snapshot("a")["rx_bytes"])
        collector.apply(ConnectionEvents(reset=True, events=[ConnectionEvent(id="c", connection=Connection(user="a", uplinkTotal=7, downlinkTotal=10))]))
        self.assertTrue(collector.snapshot("a")["active"])
        self.assertFalse(collector.snapshot("b")["active"])
        collector.apply(ConnectionEvents(events=[ConnectionEvent(type=1, id="c", uplinkDelta=3, downlinkDelta=5)]))
        collector.apply(ConnectionEvents(events=[ConnectionEvent(type=2, id="c", connection=Connection(user="a", uplinkTotal=12, downlinkTotal=20, closedAt=1))]))
        self.assertEqual(collector.snapshot("a")["rx_bytes"], 20)
        self.assertEqual(collector.snapshot("a")["tx_bytes"], 12)
        self.assertFalse(collector.snapshot("a")["active"])
        collector.ready = False
        self.assertIsNone(collector.snapshot("a")["active"])
        collector.apply(ConnectionEvents(reset=True))
        self.assertEqual(collector.snapshot("a")["rx_bytes"], 0)

    def test_old_wireguard_endpoint_does_not_mean_online(self):
        output = "interface\nkey\t(none)\t127.0.0.1:5\t10.0.0.2/32\t100\t10\t20\t25\n"
        with patch.object(manager, "run", return_value=subprocess.CompletedProcess([], 0, output)), patch.object(manager.time, "time", return_value=1000):
            stats = manager.wg_like_dump("transport-wg")["key"]
        self.assertFalse(stats["active"])
        self.assertEqual(stats["rx_bytes"], 20)

    def test_unavailable_quic_is_not_reported_as_zero_traffic(self):
        with patch.object(manager, "quic_telemetry", {}):
            self.assertIsNone(manager.quic_profile_stats("transport-tuic", {})["rx_bytes"])


@unittest.skipUnless(os.getenv("PRIVACY_SINGBOX_BIN") and os.getenv("PRIVACY_MIHOMO_BIN") and os.getenv("PRIVACY_OPENSSL_BIN"), "Requires real sing-box, Mihomo and OpenSSL")
class QuicTrafficTests(unittest.TestCase):
    def test_hysteria2_and_tuic_report_authenticated_user_traffic(self):
        for module in ("hysteria2", "tuic"):
            for client_format in ("mihomo", "singbox"):
                with self.subTest(module=module, client=client_format):
                    self.probe(module, client_format)

    def probe(self, module, client_format):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = threading.Event()
            class Origin(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"verified-traffic" * 100)
                    self.wfile.flush()
                    release.wait(15)
                def log_message(self, *_):
                    pass
            origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            processes, logs = [], []
            connection = None
            collector = None
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                config_root = root / "managed"
                cert_root = config_root / "quic"
                cert_root.mkdir(parents=True)
                subprocess.run([os.environ["PRIVACY_OPENSSL_BIN"], "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=gate.312", "-keyout", str(cert_root / "server.key"), "-out", str(cert_root / "server.crt")], check=True, capture_output=True, creationflags=flags)
                port, api_port, proxy_port = free_port(), free_port(), free_port()
                settings = {"port": port, "up_mbps": 100, "down_mbps": 100, "obfs": False, "congestion_control": "bbr", "heartbeat": "10s"}
                credential = {"instance_id": "a", "uuid": str(uuid.uuid4()), "password": "secret-a", "port": port, "sni": "gate.312", "skip_cert_verify": True, **{k: settings[k] for k in ("up_mbps", "down_mbps", "obfs", "congestion_control", "heartbeat")}}
                credential.update({"up_mbps": 23, "down_mbps": 45} if module == "hysteria2" else {"congestion_control": "cubic", "heartbeat": "30s", "udp_relay_mode": "quic"})
                users = [credential, {**credential, "instance_id": "b", "uuid": str(uuid.uuid4()), "password": "secret-b"}]
                service = {"type": "api", "tag": "panel-telemetry", "listen": "127.0.0.1", "listen_port": api_port, "secret": "test-secret"}
                def run(*args, **kwargs):
                    if args[0] == "systemctl":
                        return subprocess.CompletedProcess(args, 0, "", "")
                    return subprocess.run(args, capture_output=True, text=True, creationflags=flags)
                with patch.object(manager, "CONFIG_ROOT", config_root), patch.object(manager, "SINGBOX_BIN", Path(os.environ["PRIVACY_SINGBOX_BIN"])), patch.object(manager, "module_settings", return_value=settings), patch.object(manager, "quic_credentials", return_value=users), patch.object(manager, "quic_telemetry_service", return_value=service), patch.object(manager, "start_quic_telemetry"), patch.object(manager, "run", side_effect=run), patch.object(manager, "service_stably_active", return_value=True), patch.object(manager.shutil, "which", return_value=None):
                    manager.write_quic_runtime("transport-" + module)
                def start(args):
                    log = open(root / f"process-{len(logs)}.log", "w+")
                    logs.append(log)
                    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                    processes.append(process)
                    return process
                server = start([os.environ["PRIVACY_SINGBOX_BIN"], "run", "-c", str(cert_root / module / "config.json")])
                wait_port(api_port, server)
                with grpc.insecure_channel(f"127.0.0.1:{api_port}") as channel:
                    call = channel.unary_stream("/daemon.StartedService/SubscribeConnections", request_serializer=SubscribeConnectionsRequest.SerializeToString)
                    with self.assertRaises(grpc.RpcError) as denied:
                        next(call(SubscribeConnectionsRequest(), metadata=(("authorization", "Bearer wrong-secret"),), timeout=2))
                    self.assertEqual(denied.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
                collector = ConnectionTelemetry(f"127.0.0.1:{api_port}", "test-secret")
                collector.start()
                with patch.object(manager, "public_endpoint", return_value="127.0.0.1"):
                    proxy = yaml.safe_load("proxies:\n" + "\n".join(manager.render_proxy("transport-" + module, credential, "test")))["proxies"][0]
                client_file = root / "client.yaml"
                client_file.write_text(yaml.safe_dump({"mixed-port": proxy_port, "proxies": [proxy], "rules": ["MATCH,test"]}), encoding="utf-8")
                if client_format == "singbox":
                    config = manager.build_singbox_config([{"id": "a", "component": "transport-" + module, "credential": credential}], {}, {"nameserver": "1.1.1.1", "fallback": "8.8.8.8"}, [], "127.0.0.1", lambda: {}, None)
                    config["inbounds"] = [{"type": "mixed", "listen": "127.0.0.1", "listen_port": proxy_port}]
                    client_file = root / "client.json"
                    client_file.write_text(json.dumps(config), encoding="utf-8")
                    client = start([os.environ["PRIVACY_SINGBOX_BIN"], "run", "-c", str(client_file)])
                else:
                    client = start([os.environ["PRIVACY_MIHOMO_BIN"], "-d", str(root), "-f", str(client_file)])
                wait_port(proxy_port, client)
                connection = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
                connection.settimeout(5)
                connection.sendall(f"GET http://127.0.0.1:{origin.server_port}/ HTTP/1.1\r\nHost: 127.0.0.1:{origin.server_port}\r\nConnection: close\r\n\r\n".encode())
                response = b""
                while b"verified-traffic" not in response:
                    chunk = connection.recv(8192)
                    self.assertTrue(chunk, response)
                    response += chunk
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and not (collector.snapshot("a")["rx_bytes"] or 0):
                    time.sleep(.1)
                stats = collector.snapshot("a")
                self.assertTrue(stats["active"], stats)
                self.assertGreater(stats["rx_bytes"], 1000)
                self.assertGreater(stats["tx_bytes"], 0)
                self.assertEqual(collector.snapshot("b")["rx_bytes"], 0)
                self.assertFalse(collector.snapshot("b")["active"])
                release.set()
                while connection.recv(8192):
                    pass
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and collector.snapshot("a")["active"]:
                    time.sleep(.1)
                self.assertFalse(collector.snapshot("a")["active"])
                self.assertGreaterEqual(collector.snapshot("a")["rx_bytes"], stats["rx_bytes"])
            except Exception:
                for log in logs:
                    log.flush()
                    log.seek(0)
                    print(log.read()[-2500:])
                raise
            finally:
                release.set()
                if connection:
                    connection.close()
                if collector:
                    collector.close()
                for process in reversed(processes):
                    process.terminate()
                    process.wait(timeout=10)
                for log in logs:
                    log.close()
                origin.shutdown()
                origin.server_close()
