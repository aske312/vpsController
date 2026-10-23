"""Run as root in a disposable network namespace, never against host firewall.

sudo unshare --net python tests/subscription_limits_smoke.py
"""
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "protocol-images/mihomo"))
import subscription_limits as limits


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        data, channel = self.request
        channel.sendto(data, self.client_address)


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def client(mark=None):
    channel = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    channel.settimeout(.12)
    if mark is not None:
        channel.setsockopt(socket.SOL_SOCKET, 36, struct.pack("I", mark))  # SO_MARK
    channel.connect(("10.203.0.2", 19998))
    return channel


def exchange(channel, payload=b"subscription-test"):
    try:
        channel.send(payload)
        return channel.recv(65536) == payload
    except (socket.timeout, PermissionError, ConnectionRefusedError):
        return False


def main():
    if "--echo" in sys.argv:
        with socketserver.UDPServer(("0.0.0.0", 19998), Echo) as server:
            server.serve_forever()
        return
    assert os.geteuid() == 0, "Root is required in a disposable namespace"
    assert os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"), "Refusing to alter the host network namespace"
    run("ip", "link", "set", "lo", "up")
    process = subprocess.Popen(["unshare", "--net", sys.executable, __file__, "--echo"])
    channels = []
    try:
        for _ in range(100):
            if os.readlink(f"/proc/{process.pid}/ns/net") != os.readlink("/proc/self/ns/net"):
                break
            time.sleep(.02)
        run("ip", "link", "add", "lim-local", "type", "veth", "peer", "name", "lim-remote")
        run("ip", "link", "set", "lim-remote", "netns", str(process.pid))
        run("ip", "addr", "add", "10.203.0.1/24", "dev", "lim-local")
        run("ip", "link", "set", "lim-local", "up")
        for args in (("link", "set", "lo", "up"), ("addr", "add", "10.203.0.2/24", "dev", "lim-remote"), ("link", "set", "lim-remote", "up")):
            run("nsenter", "-t", str(process.pid), "-n", "ip", *args)
        profile = {"id": "smoke", "mark": limits.profile_mark("smoke"), "limits": dict(limits.UNLIMIT),
                   "expires_at": None, "xray_users": [], "quic_users": {}, "shadowsocks": [], "peers": []}
        other = {**profile, "id": "other", "mark": limits.profile_mark("other"), "limits": dict(limits.UNLIMIT)}
        policy = {"schema": 1, "profiles": [profile, other]}
        with tempfile.TemporaryDirectory(prefix="subscription-kernel-") as directory:
            root = Path(directory)
            limits.update(policy, root)
            channels.extend([client(profile["mark"]), client(profile["mark"]), client(other["mark"]), client()])
            for _ in range(50):
                if exchange(channels[2]):
                    break
                assert process.poll() is None, "Echo process exited"
                time.sleep(.02)
            for channel in channels:
                assert exchange(channel), "Initial marked/unmarked traffic must work"
            # Small quotas are for this isolated kernel test only. API accepts
            # whole GB values; nftables itself always receives integer bytes.
            profile["limits"]["traffic_gb"] = .000064
            limits.update(policy, root)
            successes = 0
            for i in range(24):
                successes += exchange(channels[i % 2], b"q" * 4096)
            assert 0 < successes < 12, successes
            assert not exchange(channels[0]) and not exchange(channels[1]), "Both devices must share one exhausted quota"
            assert exchange(channels[2]), "Other/unmarked traffic must remain available"
            assert exchange(channels[3]), "Unmanaged traffic must remain available"
            usage, _ = limits.kernel_state(policy)
            total = usage["smoke"]["rx_bytes"] + usage["smoke"]["tx_bytes"]
            assert 0 < total <= 64000, total
            limits.update(policy, root, force=True)
            restored, _ = limits.kernel_state(policy)
            assert restored["smoke"]["rx_bytes"] + restored["smoke"]["tx_bytes"] >= total, "Reapplying rules must not reset usage"
            assert restored["smoke"]["quota_used_bytes"] >= 64000, "Quota exhaustion must survive a refresh"
            assert not exchange(channels[0]), "Refreshing policy must not reclaim exhausted quota"
            profile["limits"] = dict(limits.UNLIMIT)
            limits.update(policy, root)
            assert exchange(channels[0]) and exchange(channels[1]), "Unlimit must restore both devices"
            with socketserver.UDPServer(("127.0.0.1", 19999), Echo) as local:
                worker = threading.Thread(target=local.serve_forever)
                worker.start()
                loop = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                channels.append(loop)
                loop.settimeout(.2)
                loop.setsockopt(socket.SOL_SOCKET, 36, struct.pack("I", profile["mark"]))
                loop.connect(("127.0.0.1", 19999))
                try:
                    before, _ = limits.kernel_state(policy)
                    assert exchange(loop, b"l" * 1000), "Managed local redirects must remain usable"
                    after, _ = limits.kernel_state(policy)
                    for direction in ("rx_bytes", "tx_bytes"):
                        delta = after["smoke"][direction] - before["smoke"][direction]
                        assert 1000 <= delta <= 1100, ("Loopback traffic counted more than once", direction, delta)
                finally:
                    local.shutdown()
                    worker.join(timeout=2)
            profile["expires_at"] = int(time.time()) - 1
            limits.update(policy, root)
            assert not exchange(channels[0]) and not exchange(channels[1]), "Expiry must block existing sessions"
            assert exchange(channels[2]), "Expiry must be profile-scoped"
            profile["expires_at"] = None
            profile["limits"]["speed_mbps"] = 1
            limits.update(policy, root)
            assert exchange(channels[0]), "Renewed subscription must work"
            finish = time.monotonic() + 4
            def send():
                while time.monotonic() < finish:
                    for channel in channels[:2]:
                        try:
                            channel.send(b"s" * 1200)
                        except PermissionError:
                            pass  # Kernel rate policing rejected this datagram.
                    time.sleep(.0005)
            sender = threading.Thread(target=send)
            sender.start()
            received = 0
            while time.monotonic() < finish:
                for channel in channels[:2]:
                    try:
                        received += len(channel.recv(65536))
                    except socket.timeout:
                        pass
            sender.join()
            assert 12000 < received < 1000000, received
            assert exchange(channels[2]), "Rate limit must not affect other traffic"
            # Check the Shadowsocks cgroup classifier against a real cgroup,
            # without applying it to the test runner's own unmarked sockets.
            group = next(row.split("::", 1)[1] for row in Path("/proc/self/cgroup").read_text().splitlines() if "::" in row)
            group_path = Path("/sys/fs/cgroup") / group.lstrip("/")
            if group_path.is_dir() and group != "/":
                with_ss = json.loads(json.dumps(policy))
                with_ss["profiles"][0]["shadowsocks"] = [{"unit": "fixture.service", "port": 31000}]
                groups = {"fixture.service": {"level": len(Path(group).parts) - 1, "id": group_path.stat().st_ino, "path": group}}
                rules = limits.nft_rules(with_ss, restored, groups, existing=True)
                result = subprocess.run(["nft", "-c", "-f", "-"], input=rules, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                with_ss["profiles"][0]["expires_at"] = int(time.time()) - 1
                with_ss["profiles"][0]["limits"] = dict(limits.UNLIMIT)
                current, _ = limits.kernel_state(policy)
                rules = limits.nft_rules(with_ss, current, groups, existing=True)
                limits.command("nft", "-f", "-", input_text=rules)
                assert not exchange(channels[3]), "Shadowsocks cgroup traffic must use the profile policy"
                excluded = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                channels.append(excluded)
                excluded.bind(("10.203.0.1", 31000))
                excluded.settimeout(.2)
                excluded.connect(("10.203.0.2", 19998))
                assert exchange(excluded), "Shadowsocks encrypted client leg must not be counted a second time"
            print(json.dumps({"shared_quota": "passed", "usage_restore": "passed", "unlimit": "passed", "expiry": "passed", "profile_isolation": "passed", "aggregate_rate_bytes_over_4s": received}))
    finally:
        for channel in channels:
            channel.close()
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
