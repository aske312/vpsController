"""Real Mihomo: a new best route must not terminate a working TCP stream."""
from contextlib import ExitStack
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
import urllib.request
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port


@unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Requires real Mihomo")
class URLTestContinuityTests(unittest.TestCase):
    def test_switch_preserves_stream_and_failed_route_is_replaced(self):
        stopped = threading.Event()

        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                if self.server.failed:
                    self.send_error(502)
                    return
                self.send_response(200)
                self.end_headers()
                request = self.rfile.readline()
                while self.rfile.readline().strip():
                    pass
                try:
                    if b"/health" in request:
                        time.sleep(self.server.delay)
                        self.wfile.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    elif b"/stream" in request:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
                        while not stopped.wait(.1):
                            self.wfile.write(self.server.marker + b"\n")
                            self.wfile.flush()
                    else:
                        body = self.server.marker
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                except (OSError, ConnectionError):
                    pass

            def log_message(self, *_):
                pass

        servers, process, stream = [], None, None
        for marker, delay in ((b"route-A", .01), (b"route-B", .3)):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
            server.marker, server.delay, server.failed = marker, delay, False
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        try:
            with ExitStack() as cleanup:
                root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
                profile = {"id": "audit", "name": "audit", "common_device_id": "common", "devices": [{"id": "common", "routing": {"strategy": "url-test", "interval": 1, "tolerance": 0, "health_timeout": 1000, "max_failed_times": 1, "test_url": "http://audit.test/health"}}], "connections": [
                    {"id": name, "name": name, "component": "transport-shadowsocks", "device_id": "common", "credential": {"port": 31000 + index, "method": "aes-128-gcm", "password": "test"}}
                    for index, name in enumerate(("A", "B"))]}
                with patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"), patch.object(manager, "public_endpoint", return_value="127.0.0.1"), patch.object(manager, "module_is_installed", return_value=True):
                    config = yaml.safe_load(manager.render_profile(profile))
                port, api_port = free_port(), free_port()
                names = [proxy["name"] for proxy in config["proxies"]]
                config["proxies"] = [{"name": name, "type": "http", "server": "127.0.0.1", "port": server.server_port} for name, server in zip(names, servers)]
                config.update({"mixed-port": port, "external-controller": f"127.0.0.1:{api_port}", "secret": "test-secret"})
                config["dns"]["enable"] = False
                path = root / "config.yaml"
                path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                with open(root / "core.log", "w") as log:
                    process = subprocess.Popen([os.environ["PRIVACY_MIHOMO_BIN"], "-d", str(root), "-f", str(path)], stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    def stop_core():
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=10)
                    cleanup.callback(stop_core)
                    wait_port(port, process)
                    wait_port(api_port, process)

                    def selected():
                        request = urllib.request.Request(f"http://127.0.0.1:{api_port}/proxies", headers={"Authorization": "Bearer test-secret"})
                        with urllib.request.urlopen(request, timeout=2) as response:
                            return json.load(response)["proxies"][manager.MIHOMO_PROXY_GROUP]["now"]

                    def await_selection(name):
                        deadline = time.monotonic() + 25
                        while time.monotonic() < deadline:
                            if selected() == name:
                                return
                            time.sleep(.2)
                        self.fail(f"URL-test did not select {name}")

                    def connect(path):
                        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
                        sock.settimeout(3)
                        sock.sendall(f"GET http://audit.test{path} HTTP/1.1\r\nHost: audit.test\r\nConnection: close\r\n\r\n".encode())
                        return sock

                    await_selection(names[0])
                    stream = connect("/stream")
                    reader = stream.makefile("rb")
                    while reader.readline().strip():
                        pass
                    self.assertEqual(reader.readline().strip(), b"route-A")
                    servers[0].delay, servers[1].delay = .3, .01
                    await_selection(names[1])
                    # Drain buffered data, then verify fresh bytes on the original stream.
                    stream.settimeout(.05)
                    drain_deadline = time.monotonic() + 1
                    while time.monotonic() < drain_deadline:
                        try:
                            if not stream.recv(65536):
                                self.fail("Working stream closed during route change")
                        except TimeoutError:
                            break
                    stream.settimeout(3)
                    self.assertIn(b"route-A", stream.recv(4096))
                    with connect("/new") as request:
                        data = b""
                        while chunk := request.recv(4096):
                            data += chunk
                    self.assertIn(b"route-B", data)
                    servers[1].failed = True
                    await_selection(names[0])
                    with connect("/after-failure") as request:
                        data = b""
                        while chunk := request.recv(4096):
                            data += chunk
                    self.assertIn(b"route-A", data)
                    reader.close()
        finally:
            stopped.set()
            if stream:
                stream.close()
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            for server in servers:
                server.shutdown()
                server.server_close()
