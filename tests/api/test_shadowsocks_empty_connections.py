import importlib.util
import os
from pathlib import Path
import socket
import struct
import sys
import unittest

PATH = Path(__file__).resolve().parents[2] / "protocol-images/mihomo/modules/transport-shadowsocks/empty_connections.py"
spec = importlib.util.spec_from_file_location("empty_connections", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(cookie=1, received=0, port=31001, inode=10, queued=0):
    return {"family": socket.AF_INET, "identity": b"\0" * 40 + struct.pack("=II", cookie, 0),
            "inode": inode, "received": received, "port": port, "queued": queued}


class Diagnostics:
    def __init__(self, rows):
        self.rows, self.destroyed = rows, []
        self.updated = None

    def snapshot(self):
        return self.rows

    def refresh(self, value):
        return self.updated if self.updated is not None else value

    def destroy(self, value):
        self.destroyed.append(value)


class EmptyConnectionTests(unittest.TestCase):
    def monitor(self, rows):
        backend = Diagnostics(rows)
        monitor = module.EmptyConnections(123, 31001, diagnostics=backend, owned=lambda: {10})
        return monitor, backend

    def test_only_own_empty_socket_expires_after_deadline(self):
        monitor, backend = self.monitor([row(), row(2, received=1), row(3, port=9999), row(4, inode=11), row(5, received=None), row(6, queued=1)])
        self.assertEqual(monitor.sweep(0), 0)
        self.assertEqual(monitor.sweep(29), 0)
        self.assertEqual(monitor.sweep(30), 1)
        self.assertEqual(backend.destroyed, [row()])

    def test_data_arriving_before_final_check_prevents_close(self):
        monitor, backend = self.monitor([row()])
        monitor.sweep(0)
        backend.updated = row(received=1)
        self.assertEqual(monitor.sweep(31), 0)
        self.assertFalse(backend.destroyed)

    def test_reused_endpoint_cookie_gets_new_deadline(self):
        monitor, backend = self.monitor([row()])
        monitor.sweep(0)
        backend.rows = [row(2)]
        self.assertEqual(monitor.sweep(31), 0)
        self.assertEqual(monitor.sweep(61), 1)
        self.assertEqual(backend.destroyed, [row(2)])

    def test_missing_socket_removed_and_stop_never_closes(self):
        monitor, backend = self.monitor([row()])
        monitor.sweep(0)
        self.assertEqual(monitor.sweep(31, stopped=lambda: True), 0)
        backend.rows = []
        monitor.sweep(32)
        self.assertFalse(monitor.first_seen)
        self.assertFalse(backend.destroyed)

    def test_refreshed_cookie_and_inode_must_match(self):
        for replacement in (row(2), row(inode=11)):
            monitor, backend = self.monitor([row()])
            monitor.sweep(0)
            backend.updated = replacement
            self.assertEqual(monitor.sweep(31), 0)

    def test_kernel_tcp_info_decoder_fails_closed_on_missing_counters(self):
        payload = bytearray(72)
        payload[0], payload[1] = socket.AF_INET, 1
        struct.pack_into("!H", payload, 4, 31001)
        struct.pack_into("=I", payload, 68, 10)
        self.assertIsNone(module.decode_diag(payload)["received"])
        tcp_info = bytearray(136)
        struct.pack_into("=Q", tcp_info, 128, 1234)
        decoded = module.decode_diag(payload + struct.pack("=HH", 140, 2) + tcp_info)
        self.assertEqual((decoded["received"], decoded["port"], decoded["inode"]), (1234, 31001, 10))

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "Linux root/CAP_NET_ADMIN required")
    def test_real_kernel_closes_empty_ipv4_and_ipv6_but_keeps_payload_flow(self):
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with self.subTest(family=family), socket.socket(family) as listener:
                listener.bind((host, 0))
                listener.listen()
                port = listener.getsockname()[1]
                with socket.create_connection((host, port)) as empty, socket.create_connection((host, port)) as active:
                    accepted_empty, _ = listener.accept()
                    accepted_active, _ = listener.accept()
                    with accepted_empty, accepted_active:
                        active.sendall(b"payload")
                        self.assertEqual(accepted_active.recv(7), b"payload")
                        monitor = module.EmptyConnections(os.getpid(), port, timeout=1)
                        self.assertEqual(monitor.sweep(0), 0)
                        self.assertEqual(monitor.sweep(2), 1)
                        empty.settimeout(1)
                        try:
                            self.assertEqual(empty.recv(1), b"")
                        except ConnectionResetError:
                            pass
                        active.sendall(b"still alive")
                        self.assertEqual(accepted_active.recv(11), b"still alive")
