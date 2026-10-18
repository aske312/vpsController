"""Shared isolated module loaders and loopback helpers for API tests."""
import importlib.util
import socket
import sys
import time
import types
import tempfile
import http.server
import threading
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_NB = 4
    fcntl.flock = lambda *_: None
    sys.modules["fcntl"] = fcntl

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load_module("privacy_api", "api/main.py")
manager = load_module("privacy_manager", "protocol-images/mihomo/manager.py")


def managed_mihomo_fixture(test_case):
    """An explicitly owned component for tests of its managed lifecycle."""
    from component_registry import ComponentRegistry
    root = Path(test_case.enterContext(tempfile.TemporaryDirectory()))
    ComponentRegistry(root).write_receipt("mihomo", "installed")
    test_case.enterContext(patch.object(manager, "DATA_ROOT", root / "mihomo"))
    return root


def free_port(*, udp=False):
    if udp:
        # Mixed Mihomo listeners bind TCP and UDP on the same number; an unused
        # TCP port can still be occupied by a different UDP application.
        for _ in range(64):
            with socket.socket(type=socket.SOCK_DGRAM) as datagram, socket.socket() as stream:
                datagram.bind(("127.0.0.1", 0))
                port = datagram.getsockname()[1]
                try:
                    stream.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise OSError("No free loopback TCP/UDP port")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Test core exited before listening")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return
        except OSError:
            time.sleep(.05)
    raise AssertionError("Test core did not start listening")


def wait_http_proxy(port, process, destination_port, *, timeout=8):
    """Wait for forwarding, not just accept(). The caller routes this private
    readiness origin through DIRECT, independently of the transport under test.
    Mihomo opens its listener while the tunnel is still suspended for startup.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Test proxy exited before forwarding became ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.25) as connection:
                connection.settimeout(.25)
                connection.sendall(f"GET http://127.0.0.1:{destination_port}/ HTTP/1.1\r\nHost: 127.0.0.1:{destination_port}\r\nConnection: close\r\n\r\n".encode())
                response = b""
                while len(response) < 8192:
                    chunk = connection.recv(8192)
                    if not chunk:
                        break
                    response += chunk
                if response.split(b"\r\n", 1)[0].endswith(b"200 OK") and response.partition(b"\r\n\r\n")[2] == b"proxy-ready":
                    return
        except OSError:
            pass
        time.sleep(.02)
    raise AssertionError("Test proxy accepted connections but did not become ready to forward")


@contextmanager
def mihomo_readiness():
    """Private DIRECT destination for startup readiness, never the tested route."""
    class Ready(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', '11')
            self.end_headers()
            self.wfile.write(b'proxy-ready')

        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Ready)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    worker.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
