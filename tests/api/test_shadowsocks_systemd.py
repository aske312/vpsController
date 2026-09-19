"""Opt-in integration test; creates only uniquely named /run systemd units.

Run as root in a disposable Linux VM with PRIVACY_SYSTEMD_TESTS=1.
Includes optional real loopback Shadowsocks traffic when ss-server/ss-local exist.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from tests.api.support import manager, free_port
from component_registry import ComponentRegistry, RegistryError, fingerprint, inventory


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def receive(channel, length):
    data = b""
    while len(data) < length:
        part = channel.recv(length - len(data))
        if not part:
            raise AssertionError("Unexpected end of test stream")
        data += part
    return data


@unittest.skipUnless(sys.platform == "linux" and os.getenv("PRIVACY_SYSTEMD_TESTS") == "1", "Requires opt-in disposable Linux/systemd environment")
class ShadowsocksSystemdTests(unittest.TestCase):
    def test_transaction_restores_real_instance_states(self):
        self.check_transaction(traffic=False)

    def test_rollback_restores_proxy_traffic_and_keeps_unaffected_stream(self):
        if not shutil.which("ss-server") or not shutil.which("ss-local"):
            self.skipTest("Requires Shadowsocks server and client")
        self.check_transaction(traffic=True)

    def check_transaction(self, traffic):
        if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
            self.skipTest("Requires root and running systemd")
        name = "vps-control-ss-test-" + uuid.uuid4().hex
        prefix = name + "@"
        target = name + ".target"
        unit_dir = Path("/run/systemd/system")
        template_path = unit_dir / (prefix + ".service")
        target_path = unit_dir / target
        instances = [prefix + item + ".service" for item in ("old", "stopped", "manual", "created")]

        def command(*args, check=True):
            return subprocess.run(["systemctl", *args], check=check, capture_output=True, text=True, timeout=30)

        with tempfile.TemporaryDirectory(prefix="ss-systemd-test-") as folder, ExitStack() as resources:
            root = Path(folder)
            config = root / "config"
            ss = config / "shadowsocks"
            ss.mkdir(parents=True)
            password = uuid.uuid4().hex

            def write_config(item):
                value = {"server": "127.0.0.1", "server_port": free_port(), "password": password,
                         "method": "chacha20-ietf-poly1305", "mode": "tcp_only"} if traffic else {}
                (ss / f"{item}.json").write_text(json.dumps(value), encoding="utf-8")

            if traffic:
                class Echo(socketserver.BaseRequestHandler):
                    def handle(self):
                        while data := self.request.recv(4096):
                            self.request.sendall(data)

                class Server(socketserver.ThreadingTCPServer):
                    daemon_threads = True

                origin = resources.enter_context(Server(("127.0.0.1", 0), Echo))
                thread = threading.Thread(target=origin.serve_forever, daemon=True)
                thread.start()
                resources.callback(thread.join, 3)
                resources.callback(origin.shutdown)

                def connect_proxy(item):
                    port = free_port()
                    process = subprocess.Popen([shutil.which("ss-local"), "-c", str(ss / f"{item}.json"),
                                                "-b", "127.0.0.1", "-l", str(port)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    resources.callback(stop_process, process)
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            channel = socket.create_connection(("127.0.0.1", port), timeout=3)
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise AssertionError("Test SOCKS client did not start")
                            time.sleep(.05)
                    resources.enter_context(channel)
                    channel.sendall(b"\x05\x01\x00")
                    self.assertEqual(receive(channel, 2), b"\x05\x00")
                    channel.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + origin.server_address[1].to_bytes(2, "big"))
                    reply = receive(channel, 4)
                    self.assertEqual(reply[:2], b"\x05\x00")
                    self.assertEqual(reply[3], 1)
                    receive(channel, 6)
                    channel.sendall(b"before rollback")
                    self.assertEqual(receive(channel, 15), b"before rollback")
                    return channel

            try:
                target_path.write_text("[Unit]\nDescription=Disposable rollback test\n", encoding="utf-8")
                guard = Path(manager.__file__).parent / "modules/transport-shadowsocks/guard.py"
                executable = f"{sys.executable} {guard} {shutil.which('ss-server')} -c {ss}/%i.json" if traffic else "/bin/sleep infinity"
                template_path.write_text(
                    f"[Unit]\nDescription=Disposable instance\nPartOf={target}\n"
                    f"[Service]\nExecStartPre=/usr/bin/test -f {ss}/%i.json\nExecStart={executable}\n"
                    f"[Install]\nWantedBy={target}\n", encoding="utf-8")
                command("daemon-reload")
                for item in ("old", "stopped", "manual"):
                    write_config(item)
                command("enable", "--runtime", instances[0], instances[1])
                command("start", target)
                command("stop", instances[1])
                command("start", instances[2])
                manual_pid = command("show", instances[2], "--property=MainPID", "--value").stdout
                if traffic:
                    connect_proxy("old")
                    preserved_stream = connect_proxy("manual")
                # Adoption of legacy data must grant explicit ownership without
                # rewriting files, restarting a live instance or dropping traffic.
                registry = ComponentRegistry(root / "ownership")
                before_files = inventory([ss])
                context = {"id": "shadowsocks", "service": target, "version": "fixture"}
                with self.assertRaises(RegistryError):
                    registry.require_managed("shadowsocks")
                accepted = registry.adopt("shadowsocks", [ss], context, fingerprint(before_files, context))
                registry.require_managed("shadowsocks")
                self.assertEqual(inventory([ss]), before_files)
                self.assertEqual(command("show", instances[2], "--property=MainPID", "--value").stdout, manual_pid)
                backup = registry.data_dir / "component-backups/shadowsocks" / accepted["backup_id"]
                self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
                self.assertEqual((backup / "configuration.tar").stat().st_mode & 0o777, 0o600)
                if traffic:
                    preserved_stream.sendall(b"after adoption")
                    self.assertEqual(receive(preserved_stream, 14), b"after adoption")
                with (
                    patch.object(manager, "CONFIG_ROOT", config),
                    patch.object(manager, "PROFILE_FILE", root / "profiles.json"),
                    patch.object(manager, "ROUTING_SETTINGS_FILE", root / "routing.json"),
                    patch.object(manager, "SERVICE_BY_MODULE", {"transport-shadowsocks": target}),
                    patch.object(manager.ss_runtime, "PREFIX", prefix),
                    patch.object(manager.ss_runtime, "UNIT", re.compile(re.escape(prefix) + r"[A-Za-z0-9_-]+\.service")),
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected"):
                        with manager.profile_runtime_transaction({"transport-shadowsocks"}):
                            command("disable", "--runtime", "--now", instances[0])
                            (ss / "old.json").unlink()
                            write_config("created")
                            command("enable", "--runtime", "--now", instances[3])
                            raise RuntimeError("injected")
                self.assertEqual(command("is-active", instances[0]).stdout.strip(), "active")
                self.assertEqual(command("is-enabled", instances[0]).stdout.strip(), "enabled-runtime")
                self.assertEqual(command("is-active", instances[1], check=False).stdout.strip(), "inactive")
                self.assertEqual(command("is-enabled", instances[1]).stdout.strip(), "enabled-runtime")
                self.assertEqual(command("show", instances[2], "--property=MainPID", "--value").stdout, manual_pid)
                self.assertEqual(command("is-enabled", instances[2], check=False).stdout.strip(), "disabled")
                self.assertEqual(command("is-active", instances[3], check=False).stdout.strip(), "inactive")
                self.assertEqual(command("is-enabled", instances[3], check=False).stdout.strip(), "disabled")
                self.assertFalse((ss / "created.json").exists())
                if traffic:
                    connect_proxy("old")
                    preserved_stream.sendall(b"after rollback")
                    self.assertEqual(receive(preserved_stream, 14), b"after rollback")
            finally:
                command("disable", "--runtime", "--now", *instances, check=False)
                command("stop", target, check=False)
                template_path.unlink(missing_ok=True)
                target_path.unlink(missing_ok=True)
                command("daemon-reload", check=False)
                command("reset-failed", *instances, target, check=False)
