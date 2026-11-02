"""Actual group selection and survival of an already established TCP stream."""
from contextlib import ExitStack
import http.server
import json
import os
from pathlib import Path
import queue
import select
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port
from tests.api.test_mihomo_rules_runtime import profile


def headers(connection):
    data = b""
    while b"\r\n\r\n" not in data:
        part = connection.recv(4096)
        if not part or len(data) > 65536:
            raise OSError("Incomplete fixture CONNECT")
        data += part
    return data


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_):
        pass


class Echo(socketserver.StreamRequestHandler):
    def handle(self):
        while data := self.rfile.readline():
            self.wfile.write(data)
            self.wfile.flush()


class Proxy(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(3)
            line = headers(self.request).split(b"\r\n", 1)[0].decode()
            method, address, _ = line.split()
            host, port = address.rsplit(":", 1)
            if method != "CONNECT" or host != "127.0.0.1":
                return
            if self.server.failed:
                self.request.sendall(b"HTTP/1.1 503 Unavailable\r\n\r\n")
                return
            if int(port) == self.server.health_port:
                time.sleep(self.server.delay)
            else:
                self.server.paths.put(self.server.label)
            with socket.create_connection((host, int(port)), timeout=3) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self.request.settimeout(None)
                upstream.settimeout(None)
                while True:
                    readable, _, _ = select.select([self.request, upstream], [], [], 1)
                    for source in readable:
                        data = source.recv(8192)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
        except (OSError, ValueError):
            return


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


@unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Requires real Mihomo")
class StrategyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))

    def exercise(self, strategy):
        with ExitStack() as stack:
            def serve(server):
                threading.Thread(target=server.serve_forever, daemon=True).start()
                stack.callback(server.server_close)
                stack.callback(server.shutdown)
                return server

            health = serve(http.server.ThreadingHTTPServer(("127.0.0.1", 0), Health))
            echo = serve(TCPServer(("127.0.0.1", 0), Echo))
            paths = queue.Queue()
            proxies = []
            for name, delay in (("first", .15), ("second", .01)):
                server = TCPServer(("127.0.0.1", 0), Proxy)
                server.label, server.delay, server.failed = name, delay, False
                server.health_port, server.paths = health.server_port, paths
                proxies.append(serve(server))
            item = profile()
            item["devices"][0]["routing"] = {"strategy": strategy}
            test_url = f"http://127.0.0.1:{health.server_port}/check"
            settings = {"strategy": strategy, "test_url": test_url, "interval": 0, "health_timeout": 1000}
            with patch.object(manager, "routing_settings", return_value=settings):
                group = yaml.safe_load(manager.render_profile(item))["proxy-groups"][0]
            group["proxies"] = ["first", "second"]
            mixed, controller = free_port(), free_port()
            config = {"mixed-port": mixed, "allow-lan": False, "bind-address": "127.0.0.1",
                      "external-controller": f"127.0.0.1:{controller}", "secret": "strategy-fixture",
                      "mode": "rule", "log-level": "error", "proxy-groups": [group],
                      "proxies": [{"name": server.label, "type": "http", "server": "127.0.0.1", "port": server.server_address[1]} for server in proxies],
                      "rules": [f"MATCH,{manager.MIHOMO_PROXY_GROUP}"]}
            folder = stack.enter_context(tempfile.TemporaryDirectory())
            path = Path(folder) / "config.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            log = stack.enter_context(open(Path(folder) / "core.log", "w", encoding="utf-8"))
            process = subprocess.Popen([os.environ["PRIVACY_MIHOMO_BIN"], "-d", folder, "-f", str(path)],
                                       stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def stop():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            stack.callback(stop)
            wait_port(controller, process)
            wait_port(mixed, process)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            group_path = "/proxies/" + urllib.parse.quote(manager.MIHOMO_PROXY_GROUP, safe="")
            def api(suffix="", data=None):
                route = group_path.replace("/proxies/", "/group/") if suffix.startswith("/delay?") else group_path
                request = urllib.request.Request(f"http://127.0.0.1:{controller}{route}{suffix}",
                                                 data=json.dumps(data).encode() if data is not None else None,
                                                 headers={"Authorization": "Bearer strategy-fixture", "Content-Type": "application/json"},
                                                 method="PUT" if data is not None else "GET")
                with opener.open(request, timeout=5) as response:
                    return json.loads(response.read() or b"{}")
            def probe():
                api("/delay?" + urllib.parse.urlencode({"url": test_url, "timeout": 1000}))
            def selected(expected):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if api().get("now") == expected:
                        return
                    time.sleep(.1)
                self.fail(f"{strategy} did not select {expected}")
            def stream(expected):
                connection = socket.create_connection(("127.0.0.1", mixed), timeout=3)
                stack.callback(connection.close)
                connection.sendall(f"CONNECT 127.0.0.1:{echo.server_address[1]} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                self.assertIn(b"200", headers(connection).split(b"\r\n")[0])
                self.assertEqual(paths.get(timeout=3), expected)
                return connection
            if strategy != "select":
                probe()
            original = "second" if strategy == "url-test" else "first"
            selected(original)
            held = stream(original)
            held.sendall(b"before\n")
            self.assertEqual(held.recv(64), b"before\n")
            if strategy == "select":
                api(data={"name": "second"})
            elif strategy == "fallback":
                proxies[0].failed = True
                probe()
            else:
                proxies[0].delay, proxies[1].delay = .005, .18
                probe()
            target = "first" if strategy == "url-test" else "second"
            selected(target)
            new = stream(target)
            new.sendall(b"new\n")
            self.assertEqual(new.recv(64), b"new\n")
            held.sendall(b"after\n")
            self.assertEqual(held.recv(64), b"after\n")
            if strategy == "fallback":
                proxies[0].failed = False
                probe()
                selected("first")
                stream("first")

    def test_fallback_uses_priority_and_recovers_the_first_node(self):
        self.exercise("fallback")

    def test_url_test_changes_new_streams_without_interrupting_existing_streams(self):
        self.exercise("url-test")

    def test_manual_selection_keeps_the_existing_stream(self):
        self.exercise("select")
