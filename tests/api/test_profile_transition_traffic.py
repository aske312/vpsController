"""Optional loopback check: both YAML generations work, only the new one survives expiry."""
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
from tests.api.support import manager, free_port, wait_port


@unittest.skipUnless(os.getenv("PRIVACY_XRAY_BIN") and os.getenv("PRIVACY_MIHOMO_BIN"), "Set local test core paths")
class ProfileTransitionTrafficTests(unittest.TestCase):
    def test_both_yaml_generations_work_until_old_listener_is_removed(self):
        self.check_transition("tcp")

    def test_cdn_path_and_encryption_can_change_while_old_yaml_still_connects(self):
        self.check_transition("websocket")

    def check_transition(self, transport):
        xray = Path(os.environ["PRIVACY_XRAY_BIN"]).resolve()
        mihomo = Path(os.environ["PRIVACY_MIHOMO_BIN"]).resolve()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"transition-verified")

            def log_message(self, *_):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            self.addCleanup(origin.server_close)
            self.addCleanup(origin.shutdown)
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            processes = []
            logs = []

            def start(command):
                log = open(root / f"process-{len(logs)}.log", "w+")
                logs.append(log)
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                processes.append(process)
                return process

            def stop(process):
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

            try:
                old = {"uuid": str(uuid.uuid4()), "port": free_port()}
                if transport != "tcp":
                    old.update(cdn_enabled=True, cdn_port=old["port"], cdn_domain="127.0.0.1", cdn_path="/old", cdn_transport=transport)
                    old["port"] = 0
                port_key = "port" if transport == "tcp" else "cdn_port"
                stream = {"network": "tcp", "security": "none"} if transport == "tcp" else manager.edge_stream(transport, "/old", "auto")
                config = {"log": {"loglevel": "error"}, "inbounds": [{"tag": "mihomo-vless-old" if transport == "tcp" else "mihomo-vless-cdn-old", "listen": "127.0.0.1", "port": old[port_key], "protocol": "vless", "settings": {"clients": [{"id": old["uuid"]}], "decryption": "none"}, "streamSettings": stream}], "outbounds": [{"protocol": "freedom"}]}
                with patch.object(manager, "REALITY_XRAY_BIN", xray), patch.object(manager, "CORE_BIN", mihomo):
                    private, public = manager.vless_encryption_pair({"privacy_mode": "encrypted"})
                new = manager.stage_vless_transition(config, {"credential": old}, private, public)
                server_file = root / "server.json"
                server_file.write_text(json.dumps(config))
                server = start([str(xray), "run", "-config", str(server_file)])
                wait_port(old[port_key], server)
                wait_port(new[port_key], server)
                proxy_ports = []
                for index, credential in enumerate((old, new)):
                    client_home = root / str(index)
                    client_home.mkdir()
                    proxy_port = free_port()
                    proxy_ports.append(proxy_port)
                    proxy = {"name": "connection", "type": "vless", "server": "127.0.0.1", "port": credential[port_key], "uuid": credential["uuid"]}
                    if credential.get("encryption"):
                        proxy["encryption"] = credential["encryption"]
                    if transport != "tcp":
                        proxy = yaml.safe_load("proxies:\n" + "\n".join(manager.render_vless_cdn(credential, "connection")))["proxies"][0]
                        proxy.update(tls=False, port=credential[port_key])
                    client_file = client_home / "config.yaml"
                    client_file.write_text(yaml.safe_dump({"port": proxy_port, "bind-address": "127.0.0.1", "allow-lan": False, "log-level": "error", "proxies": [proxy], "rules": ["MATCH,connection"]}))
                    client = start([str(mihomo), "-d", str(client_home), "-f", str(client_file)])
                    wait_port(proxy_port, client)

                def probe(port):
                    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                        connection.settimeout(5)
                        connection.sendall(f"GET http://127.0.0.1:{origin.server_port}/ HTTP/1.1\r\nHost: 127.0.0.1:{origin.server_port}\r\nConnection: close\r\n\r\n".encode())
                        response = b""
                        try:
                            while chunk := connection.recv(65536):
                                response += chunk
                        except TimeoutError:
                            pass
                        return b"transition-verified" in response

                self.assertTrue(probe(proxy_ports[0]), "Old YAML must still connect during grace")
                self.assertTrue(probe(proxy_ports[1]), "New encrypted YAML must connect during grace")
                stop(server)
                expired = deepcopy(config)
                expired["inbounds"] = expired["inbounds"][1:]
                server_file.write_text(json.dumps(expired))
                server = start([str(xray), "run", "-config", str(server_file)])
                wait_port(new[port_key], server)
                self.assertFalse(probe(proxy_ports[0]), "Old YAML must be revoked after expiry")
                self.assertTrue(probe(proxy_ports[1]), "Current YAML must remain usable after expiry")
            except Exception as exc:
                output = []
                for log in logs:
                    log.flush()
                    log.seek(0)
                    output.append(log.read()[-1500:])
                raise AssertionError(f"{exc}\n" + "\n".join(output)) from exc
            finally:
                for process in reversed(processes):
                    stop(process)
                for log in logs:
                    log.close()
