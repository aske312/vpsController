"""Isolated real Xray/TLS tests; never reload the installed application.

Run with PRIVACY_XRAY_BIN and PROBE_ORIGIN_IP (an IPv4 address assigned to this
host). Only ephemeral ports, temporary credentials and child processes are used.
"""
import copy
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
import network_probe


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class TlsFront:
    def __init__(self, certificate, key, backend):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate, key)
        self.context.set_alpn_protocols(["h2", "http/1.1"])
        self.backend = backend
        self.stop = threading.Event()
        self.connections = []
        self.threads = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections.append(client)
            thread = threading.Thread(target=self.forward, args=(client,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def forward(self, client):
        try:
            client.settimeout(2)
            secure = self.context.wrap_socket(client, server_side=True)
            backend = socket.create_connection(("127.0.0.1", self.backend), timeout=2)
            self.connections.extend([secure, backend])
            secure.settimeout(0.2)
            backend.settimeout(0.2)
            def copy_stream(source, target):
                while not self.stop.is_set():
                    try:
                        chunk = source.recv(65536)
                        if not chunk:
                            break
                        target.sendall(chunk)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
            thread = threading.Thread(target=copy_stream, args=(secure, backend), daemon=True)
            self.threads.append(thread)
            thread.start()
            copy_stream(backend, secure)
        except OSError:
            pass

    def close(self):
        self.stop.set()
        self.listener.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=1)
        for thread in self.threads:
            thread.join(timeout=1)


@unittest.skipUnless(os.environ.get("PRIVACY_XRAY_BIN") and os.environ.get("PROBE_ORIGIN_IP"),
                     "requires isolated Xray and an assigned origin IPv4")
class NetworkProbeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pro-probe-fixture-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = os.environ.get("PRIVACY_XRAY_BIN", "")
        self.origin = os.environ["PROBE_ORIGIN_IP"]
        self.cert, self.key = self.root / "cert.pem", self.root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=probe.local", "-addext", "subjectAltName=DNS:probe.local",
                        "-keyout", str(self.key), "-out", str(self.cert)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    def fixture(self, transport):
        credential = str(uuid.uuid4())
        key, settings = {"ws": ("wsSettings", {"path": "/isolated-probe"}),
                         "xhttp": ("xhttpSettings", {"path": "/isolated-probe", "mode": "auto"}),
                         "grpc": ("grpcSettings", {"serviceName": "isolated-probe"})}[transport]
        port = free_port()
        inbound = {"tag": "vless-tls", "listen": "127.0.0.1", "port": port, "protocol": "vless",
                   "settings": {"clients": [{"id": credential}], "decryption": "none"},
                   "streamSettings": {"network": transport, key: settings}}
        config = {"log": {"loglevel": "none"}, "inbounds": [inbound],
                  "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "blocked", "protocol": "blackhole"}],
                  "routing": {"rules": [{"type": "field", "ip": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"], "outboundTag": "blocked"}]}}
        path = self.root / f"server-{transport}.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        process = subprocess.Popen([self.binary, "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.addCleanup(stop)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                self.assertIsNone(process.poll(), "isolated Xray did not start")
                time.sleep(0.05)
        front = TlsFront(self.cert, self.key, port)
        self.addCleanup(front.close)
        binding = {"id": "isolated-binding", "state": "active", "kind": "tls_relay", "address": "probe.local", "desired_revision": 1}
        consumers = [{"id": "direct:fixture", "owner": "direct", "kind": "tls_relay", "address": "probe.local", "reference": "snapshot"}]
        clients = [{"id": "fixture", "protocol": "vless-reality-xhttp", "public_key": credential, "vless_routes": ["tls"]}]
        plan = network_probe.prepare_direct_vless(binding, consumers, clients, {"inbounds": [inbound]}, {"TLS_ENABLED": "yes", "TLS_DOMAIN": "probe.local"})
        # The fixture substitutes ephemeral TLS/loopback instead of touching :443.
        remote = plan.client["settings"]["vnext"][0]
        remote.update(address="127.0.0.1", port=front.port)
        plan.client["streamSettings"]["tlsSettings"]["certificates"] = [{"certificateFile": str(self.cert), "usage": "verify"}]
        return plan

    def test_ws_xhttp_grpc_real_round_trip_and_cleanup(self):
        for transport in ("ws", "xhttp", "grpc"):
            with self.subTest(transport=transport):
                plan = self.fixture(transport)
                result = network_probe.execute_direct_vless(plan, self.binary, self.origin, timeout=5)
                self.assertEqual(result["state"], "verified", result)
                self.assertNotIn(plan.client["settings"]["vnext"][0]["users"][0]["id"], json.dumps(result))

    def test_live_valid_connection_cannot_confirm_invalid_credential(self):
        plan = self.fixture("ws")
        results = []
        thread = threading.Thread(target=lambda: results.append(network_probe.execute_direct_vless(plan, self.binary, self.origin, timeout=4)))
        thread.start()
        broken = copy.deepcopy(plan)
        broken.client["settings"]["vnext"][0]["users"][0]["id"] = str(uuid.uuid4())
        failed = network_probe.execute_direct_vless(broken, self.binary, self.origin, timeout=2)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["state"], "verified", results)
        self.assertEqual(failed["state"], "failed", failed)

    def test_wrong_tls_identity_and_path_fail_closed(self):
        plan = self.fixture("ws")
        wrong_name = copy.deepcopy(plan)
        wrong_name.client["streamSettings"]["tlsSettings"]["serverName"] = "different.local"
        self.assertEqual(network_probe.execute_direct_vless(wrong_name, self.binary, self.origin, timeout=2)["state"], "failed")
        wrong_path = copy.deepcopy(plan)
        wrong_path.client["streamSettings"]["wsSettings"]["path"] = "/wrong-path"
        self.assertEqual(network_probe.execute_direct_vless(wrong_path, self.binary, self.origin, timeout=2)["state"], "failed")


@unittest.skipUnless(os.environ.get("PRIVACY_SINGBOX_BIN") and os.environ.get("PROBE_ORIGIN_IP"),
                     "requires isolated sing-box and an assigned origin IPv4")
class QuicProbeIntegrationTests(unittest.TestCase):
    def setUp(self):
        NetworkProbeIntegrationTests.setUp(self)
        self.binary = os.environ["PRIVACY_SINGBOX_BIN"]

    def fixture(self, protocol):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        user_id = str(uuid.uuid4())
        password = str(uuid.uuid4())
        inbound = {"type": protocol, "tag": "isolated-quic", "listen": "127.0.0.1", "listen_port": port,
                   "users": [{"name": "phone", "password": password, **({"uuid": user_id} if protocol == "tuic" else {})}],
                   "tls": {"enabled": True, "server_name": "probe.local", "alpn": ["h3"], "certificate_path": str(self.cert), "key_path": str(self.key)}}
        if protocol == "hysteria2":
            inbound.update(up_mbps=100, down_mbps=100, obfs={"type": "salamander", "password": "isolated-obfs"})
        else:
            inbound.update(congestion_control="bbr", auth_timeout="3s", zero_rtt_handshake=False, heartbeat="10s")
        config = {"log": {"disabled": True}, "inbounds": [inbound], "outbounds": [{"type": "direct"}]}
        path = self.root / f"server-{protocol}.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        subprocess.run([self.binary, "check", "-c", str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        process = subprocess.Popen([self.binary, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.addCleanup(stop)
        time.sleep(0.1)
        self.assertIsNone(process.poll())
        binding = {"id": "isolated-udp", "state": "active", "kind": "udp_relay", "address": "127.0.0.1", "desired_revision": 1}
        refs = [{"id": "direct:phone", "owner": "direct", "kind": "udp_relay", "address": "127.0.0.1", "reference": "snapshot"}]
        clients = [{"id": "phone", "protocol": protocol, "public_key": user_id, "port": port, "channel_mode": "udp_relay", "route_endpoint": "127.0.0.1"}]
        if protocol == "hysteria2":
            # Native Direct auth uses "id:password"; the isolated sing-box user
            # has the same accepted auth string without installing an auth service.
            server = {"listen": f":{port}", "tls": {"cert": str(self.cert), "key": str(self.key)}, "auth": {"type": "http"},
                      "obfs": {"type": "salamander", "salamander": {"password": "isolated-obfs"}}}
            users = {"phone": password}
            inbound["users"][0]["password"] = f"phone:{password}"
            stop()
            path.write_text(json.dumps(config))
            process = subprocess.Popen([self.binary, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.1)
        else:
            server, users = config, None
        return network_probe.prepare_direct_quic(binding, refs, clients, protocol, server, {"port": port}, self.cert.read_text(), "probe.local", users)

    def test_hysteria2_and_tuic_real_udp_round_trip(self):
        for protocol in ("hysteria2", "tuic"):
            with self.subTest(protocol=protocol):
                plan = self.fixture(protocol)
                result = network_probe.execute_quic(plan, self.binary, self.origin, timeout=5)
                self.assertEqual(result["state"], "verified", result)
                self.assertNotIn(plan.client["password"], json.dumps(result))

    def test_wrong_quic_auth_cannot_borrow_successful_neighbor(self):
        plan = self.fixture("tuic")
        results = []
        thread = threading.Thread(target=lambda: results.append(network_probe.execute_quic(plan, self.binary, self.origin, timeout=4)))
        thread.start()
        broken = copy.deepcopy(plan)
        broken.client["password"] = "incorrect-auth"
        result = network_probe.execute_quic(broken, self.binary, self.origin, timeout=2)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["state"], "verified", results)
        self.assertEqual(result["state"], "failed", result)

    def test_wrong_quic_tls_identity_and_obfs_fail_closed(self):
        plan = self.fixture("hysteria2")
        broken = copy.deepcopy(plan)
        broken.client["tls"]["server_name"] = "different.local"
        self.assertEqual(network_probe.execute_quic(broken, self.binary, self.origin, timeout=2)["state"], "failed")
        broken = copy.deepcopy(plan)
        broken.client["obfs"]["password"] = "incorrect-obfs"
        self.assertEqual(network_probe.execute_quic(broken, self.binary, self.origin, timeout=2)["state"], "failed")
