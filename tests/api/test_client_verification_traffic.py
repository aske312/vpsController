"""Real core loopback test of standard YAML, not an application verification."""
from copy import deepcopy
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import yaml
from tests.api.support import manager, free_port, wait_port, managed_mihomo_fixture


@unittest.skipUnless(os.getenv("PRIVACY_XRAY_BIN") and os.getenv("PRIVACY_MIHOMO_BIN"), "Set local test core paths")
class StandardSubscriptionTrafficTests(unittest.TestCase):
    def test_unverified_client_standard_yaml_passes_core_validation_and_loopback_traffic(self):
        managed_mihomo_fixture(self)
        nonce = uuid.uuid4().hex.encode()

        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(nonce)

            def log_message(self, *_):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            self.addCleanup(origin.server_close)
            self.addCleanup(origin.shutdown)
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            xray = Path(os.environ["PRIVACY_XRAY_BIN"]).resolve()
            mihomo = Path(os.environ["PRIVACY_MIHOMO_BIN"]).resolve()
            password = "isolated-test-" + uuid.uuid4().hex
            server_port = free_port()
            config = {"log": {"loglevel": "error"}, "inbounds": [{"listen": "127.0.0.1", "port": server_port,
                      "protocol": "shadowsocks", "settings": {"method": "aes-128-gcm", "password": password, "network": "tcp"}}],
                      "outbounds": [{"protocol": "freedom"}]}
            server_file = root / "server.json"
            server_file.write_text(json.dumps(config), encoding="utf-8")
            profile = {"id": "profile", "name": "Isolated test", "common_device_id": "common", "subscription_token": "test-token",
                       "devices": [{"id": "common", "name": "Common", "routing": {}}],
                       "connections": [{"id": "ss", "component": "transport-shadowsocks", "device_id": "common", "settings": {},
                                        "credential": {"port": server_port, "method": "aes-128-gcm", "password": password}}]}
            with (patch.object(manager, "profiles", return_value=[deepcopy(profile)]),
                  patch.object(manager, "save_profiles") as save,
                  patch.object(manager, "public_endpoint", return_value="127.0.0.1"),
                  patch.object(manager, "routing_settings", return_value={"strategy": "select"}),
                  patch.object(manager, "dns_settings", return_value={"enhanced_mode": "fake-ip", "nameserver": "1.1.1.1", "fallback": "8.8.8.8"}),
                  patch.object(manager, "profile_rules", return_value=[]),
                  patch.object(manager, "CORE_BIN", mihomo), patch.object(manager, "CORE_HOME", root / "validate"),
                  patch.object(manager.client_compatibility, "verified_records", return_value=[])):
                request = manager.Request({"type": "http", "method": "GET", "path": "/s/test-token", "query_string": b"",
                                           "headers": [(b"user-agent", b"Happ/3.0")]})
                response = manager.public_profile_subscription("test-token", request, False)
                save.assert_not_called()
            self.assertIn(".yaml", response.headers["content-disposition"])
            parsed = yaml.safe_load(response.body)
            self.assertEqual(parsed["proxies"][0]["password"], password)
            self.assertEqual(len(parsed["rules"]), 1)
            self.assertTrue(parsed["rules"][0].startswith("MATCH,"))
            group_name = parsed["rules"][0].split(",", 1)[1]
            group = next(group for group in parsed["proxy-groups"] if group["name"] == group_name)
            self.assertEqual(group["proxies"], [parsed["proxies"][0]["name"]])
            # Confine execution to the test's loopback listener; core -t above
            # validated the original subscription without opening TUN or ports.
            parsed.update({"mixed-port": free_port(udp=True), "bind-address": "127.0.0.1", "allow-lan": False,
                           "tun": {"enable": False}, "dns": {"enable": False}, "geo-auto-update": False,
                           "external-controller": "", "log-level": "error"})
            self.assertNotEqual(parsed["mixed-port"], server_port)
            client_file = root / "client.yaml"
            client_file.write_text(yaml.safe_dump(parsed), encoding="utf-8")
            processes = []
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with (open(root / "server.log", "w+") as server_log, open(root / "client.log", "w+") as client_log):
                try:
                    for command, log, port in (([str(xray), "run", "-config", str(server_file)], server_log, server_port),
                                               ([str(mihomo), "-d", str(root / "client"), "-f", str(client_file)], client_log, parsed["mixed-port"])):
                        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                        processes.append(process)
                        wait_port(port, process)
                    with socket.create_connection(("127.0.0.1", parsed["mixed-port"]), timeout=5) as connection:
                        connection.settimeout(5)
                        connection.sendall(f"GET http://127.0.0.1:{origin.server_port}/ HTTP/1.1\r\nHost: 127.0.0.1:{origin.server_port}\r\nConnection: close\r\n\r\n".encode())
                        chunks = []
                        while chunk := connection.recv(65536):
                            chunks.append(chunk)
                        self.assertIn(nonce, b"".join(chunks))
                except Exception as exc:
                    for log in (server_log, client_log):
                        log.flush()
                        log.seek(0)
                    raise AssertionError(f"{exc}\n{server_log.read()[-1200:]}\n{client_log.read()[-1200:]}") from exc
                finally:
                    for process in reversed(processes):
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=10)
