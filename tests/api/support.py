"""Shared isolated module loaders and loopback helpers for API tests."""
import importlib.util
import socket
import sys
import time
import types
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
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


def free_port():
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
