"""Expire only owned TCP sockets that have never received payload (Linux).

libev 3.3.5 server.c:new_server clamps the configured timeout to jconf.h's
MIN_TCP_IDLE_TIMEOUT (24 hours), including unauthenticated empty connections.
Keep a separate first-payload deadline without terminating established flows.
Wire layouts: Linux UAPI inet_diag.h / tcp.h; destruction uses socket cookies.
"""
import errno
import os
from pathlib import Path
import socket
import struct
import time


def decode_diag(payload):
    if len(payload) < 72 or payload[1] != 1:  # TCP_ESTABLISHED only
        return None
    received = None
    offset = 72
    while offset + 4 <= len(payload):
        length, kind = struct.unpack_from("=HH", payload, offset)
        if length < 4 or offset + length > len(payload):
            return None
        if kind == 2 and length >= 140:  # INET_DIAG_INFO / tcpi_bytes_received
            received = struct.unpack_from("=Q", payload, offset + 4 + 128)[0]
        offset += (length + 3) & ~3
    return {"family": payload[0], "identity": payload[4:52],
            "port": struct.unpack_from("!H", payload, 4)[0],
            "inode": struct.unpack_from("=I", payload, 68)[0], "received": received,
            "queued": struct.unpack_from("=I", payload, 56)[0]}


class SocketDiagnostics:
    def exchange(self, family, identity=None, destroy=False):
        # inet_diag_req_v2 + inet_diag_sockid. A real cookie is mandatory when
        # destroying: a reused endpoint tuple must never identify a new socket.
        if destroy and (identity is None or identity[40:48] == b"\xff" * 8):
            raise ValueError("Socket destruction requires its kernel cookie")
        dumping = identity is None
        identity = identity or (b"\0" * 40 + b"\xff" * 8)
        request = struct.pack("=BBBBI", family, socket.IPPROTO_TCP, 2, 0, 1 << 1) + identity
        flags = 1 | (0x300 if dumping else 4)  # REQUEST, DUMP or ACK
        message = struct.pack("=IHHII", 16 + len(request), 21 if destroy else 20, flags, 1, 0) + request
        result = []
        with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 4) as channel:
            channel.settimeout(1)
            channel.bind((0, 0))
            channel.sendto(message, (0, 0))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                packet, _, received_flags, address = channel.recvmsg(1 << 20)
                if address[0] != 0 or received_flags & socket.MSG_TRUNC:
                    raise OSError("Incomplete kernel socket diagnostic reply")
                offset = 0
                while offset + 16 <= len(packet):
                    length, kind, reply_flags, seq, _ = struct.unpack_from("=IHHII", packet, offset)
                    if length < 16 or offset + length > len(packet) or seq != 1 or reply_flags & 0x10:
                        raise OSError("Interrupted socket diagnostic dump")
                    payload = packet[offset + 16:offset + length]
                    if kind == 2:  # NLMSG_ERROR, including successful ACK
                        code = struct.unpack_from("=i", payload)[0]
                        if code:
                            raise OSError(-code, os.strerror(-code))
                        return result
                    if kind == 3:  # NLMSG_DONE
                        if len(payload) >= 4 and struct.unpack_from("=i", payload)[0]:
                            raise OSError("Socket diagnostic dump failed")
                        return result
                    row = decode_diag(payload)
                    if row is not None:
                        result.append(row)
                    offset += (length + 3) & ~3
            raise TimeoutError("Socket diagnostic deadline exceeded")

    def snapshot(self):
        rows = []
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                rows.extend(self.exchange(family))
            except OSError as exc:
                if family != socket.AF_INET6 or exc.errno not in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                    raise
        return rows

    def refresh(self, row):
        rows = self.exchange(row["family"], row["identity"])
        return rows[0] if rows else None

    def destroy(self, row):
        self.exchange(row["family"], row["identity"], destroy=True)


def owned_socket_inodes(pid):
    result = set()
    for path in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            result.add(int(target[8:-1]))
    return result


class EmptyConnections:
    def __init__(self, pid, port, timeout=30, diagnostics=None, owned=None):
        self.pid, self.port, self.timeout = pid, port, timeout
        self.diagnostics = diagnostics or SocketDiagnostics()
        self.owned = owned or (lambda: owned_socket_inodes(pid))
        self.first_seen = {}

    def eligible(self, row, inodes):
        return row is not None and row["inode"] in inodes and row["port"] == self.port and row["received"] == 0 and row["queued"] == 0

    def sweep(self, now=None, stopped=lambda: False):
        now = time.monotonic() if now is None else now
        inodes = self.owned()
        candidates = {}
        for row in self.diagnostics.snapshot():
            if self.eligible(row, inodes):
                key = (row["family"], row["identity"], row["inode"])
                candidates[key] = row
        self.first_seen = {key: self.first_seen.get(key, now) for key in candidates}
        closed = 0
        deadline = time.monotonic() + 2
        for key, row in candidates.items():
            if stopped() or time.monotonic() >= deadline:
                break
            if now - self.first_seen[key] < self.timeout:
                continue
            try:
                # Re-read counters immediately before destruction, and ensure
                # the socket still belongs to this ss-server, not another unit.
                fresh = self.diagnostics.refresh(row)
                if self.eligible(fresh, inodes) and fresh["identity"] == row["identity"] and fresh["inode"] == row["inode"] and not stopped():
                    self.diagnostics.destroy(fresh)
                    closed += 1
                self.first_seen.pop(key, None)
            except OSError as exc:
                if exc.errno not in (errno.ENOENT, errno.ESRCH, errno.ESTALE):
                    raise
                self.first_seen.pop(key, None)
        return closed
