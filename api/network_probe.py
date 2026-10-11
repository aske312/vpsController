"""Read-only preparation of probes from existing route consumers.

A plan is not evidence of readiness. Credentials stay in memory and must never
be included in the shared Network registry or an HTTP response.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import ipaddress
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


class ProbeUnavailable(ValueError):
    pass


def source_revision(paths: dict[str, Path]) -> dict:
    """File identities detect credential/config replacement without hashing secrets."""
    result = {}
    for key, path in paths.items():
        stat = path.stat()
        result[key] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    return result


PRODUCERS = {"direct_vless", "mihomo_vless", "direct_tuic", "direct_hysteria2"}


def evidence_key(root: Path, *, create: bool = False) -> bytes:
    """Private local key: registry seals cannot be used to guess credentials."""
    path = root / "network-probe.key"
    if create and not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".probe-key-", delete=False) as output:
            temporary = Path(output.name)
            try:
                os.chmod(temporary, 0o600)
                output.write(secrets.token_bytes(32))
                output.flush()
                os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                output.close()
                temporary.unlink(missing_ok=True)
    key = path.read_bytes()
    if len(key) != 32:
        raise ProbeUnavailable("Ключ проверки повреждён; выполните новую проверку после восстановления ключа")
    return key


def plan_seal(plan: ProbePlan, key: bytes) -> str:
    value = json.dumps([plan.fingerprint, plan.client, plan.server_behavior], sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def plan_from_sources(binding: dict, proof: dict, paths: dict[str, Path]) -> ProbePlan:
    producer, consumer_id = proof["producer"], proof["consumer_id"]
    consumer = {"id": consumer_id, "owner": "mihomo" if producer == "mihomo_vless" else "direct",
                "kind": binding["kind"], "address": binding["address"], "reference": "snapshot"}
    config = paths["config"].read_text(encoding="utf-8")
    if producer == "mihomo_vless":
        return prepare_mihomo_vless(binding, [consumer], json.loads(paths["profiles"].read_text(encoding="utf-8")),
            json.loads(config), {paths["gateway_route"].stem: json.loads(paths["gateway_route"].read_text(encoding="utf-8"))})
    clients = json.loads(paths["clients"].read_text(encoding="utf-8"))
    if producer == "direct_vless":
        environment = dict(line.split("=", 1) for line in paths["environment"].read_text(encoding="utf-8").splitlines() if "=" in line)
        return prepare_direct_vless(binding, [consumer], clients, json.loads(config), environment)
    import yaml
    protocol = producer.removeprefix("direct_")
    return prepare_direct_quic(binding, [consumer], clients, protocol,
        json.loads(config) if protocol == "tuic" else yaml.safe_load(config),
        json.loads(paths["settings"].read_text(encoding="utf-8")), paths["certificate"].read_text(encoding="utf-8"),
        proof["server_name"], json.loads(paths["users"].read_text(encoding="utf-8")) if protocol == "hysteria2" else None)


def evidence_current(check: dict, paths: dict[str, Path], *, binding: dict | None = None, key_root: Path | None = None) -> bool:
    proof = check.get("proof")
    if not proof:
        return True  # Older evidence is still subject to the shared revision gate.
    try:
        if proof.get("plan_seal"):
            if binding is None or key_root is None or proof.get("producer") not in PRODUCERS:
                return False
            before = source_revision(paths)
            plan = plan_from_sources(binding, proof, paths)
            return (proof.get("method") == f"{plan.producer}_nonce_v1"
                    and before == source_revision(paths)
                    and hmac.compare_digest(proof["plan_seal"], plan_seal(plan, evidence_key(key_root))))
        return (proof.get("method") in {f"{producer}_nonce_v1" for producer in PRODUCERS}
                and proof.get("source_revision") == source_revision(paths))
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False


def same_plan(before: ProbePlan, after: ProbePlan) -> bool:
    # Credentials are compared only in memory, never hashed or persisted.
    return before.fingerprint == after.fingerprint and before.client == after.client and before.server_behavior == after.server_behavior


def _receive(connection: socket.socket, size: int) -> bytes:
    result = b""
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            break
        result += chunk
    return result


class OriginWitness:
    """One short-lived challenge at this origin, rejecting other hosts' traffic."""
    def __init__(self, address: str, timeout: float, network: str = "tcp"):
        self.address = str(ipaddress.IPv4Address(address))
        self.challenge = secrets.token_bytes(32)
        self.response = secrets.token_bytes(32)
        self.seen = threading.Event()
        self.stop = threading.Event()
        self.deadline = time.monotonic() + timeout
        self.network = network
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if network == "udp" else socket.SOCK_STREAM)
        try:
            # Bind the origin's real address: production Xray blocks loopback targets.
            self.socket.bind((self.address, 0))
            if network != "udp":
                self.socket.listen(4)
            self.socket.settimeout(0.1)
        except BaseException:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        if self.network == "udp":
            while not self.stop.is_set() and time.monotonic() < self.deadline:
                try:
                    challenge, peer = self.socket.recvfrom(512)
                    if peer[0] == self.address and secrets.compare_digest(challenge, self.challenge):
                        self.seen.set()
                        self.socket.sendto(self.response, peer)
                        return
                except socket.timeout:
                    continue
                except OSError:
                    return
            return
        while not self.stop.is_set() and time.monotonic() < self.deadline:
            try:
                connection, peer = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                if peer[0] != self.address:
                    continue
                try:
                    connection.settimeout(min(1, max(0.01, self.deadline - time.monotonic())))
                    if secrets.compare_digest(_receive(connection, 32), self.challenge):
                        self.seen.set()
                        connection.sendall(self.response)
                        return
                except OSError:
                    continue

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.socket.close()
        self.thread.join(timeout=1.2)


def execute_direct_vless(plan: ProbePlan, binary: str, origin_address: str, *, timeout: float = 12) -> dict:
    """A separate client, one VLESS outbound, a nonce round-trip to this origin.

    Never changes a service, listener, credential or routing configuration. No
    direct fallback exists. Success also needs a witnessed request at this VPS.
    """
    timeout = max(0.2, min(float(timeout), 15))
    deadline = time.monotonic() + timeout
    process = None
    try:
        with OriginWitness(origin_address, timeout) as witness:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            outbound = copy.deepcopy(plan.client)
            outbound["tag"] = "probe-route"
            config = {"log": {"loglevel": "none"},
                      "inbounds": [{"tag": "probe-input", "listen": "127.0.0.1", "port": port,
                                    "protocol": "dokodemo-door", "settings": {
                                        "address": witness.address, "port": witness.port, "network": "tcp"}}],
                      "outbounds": [outbound],
                      "routing": {"rules": [{"type": "field", "inboundTag": ["probe-input"],
                                               "outboundTag": "probe-route"}]}}
            with tempfile.TemporaryDirectory(prefix="vps-network-probe-") as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                os.chmod(path, 0o600)
                try:
                    process = subprocess.Popen([binary, "run", "-config", str(path)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=min(0.2, max(0.01, deadline - time.monotonic()))) as client:
                                client.settimeout(max(0.01, deadline - time.monotonic()))
                                client.sendall(witness.challenge)
                                response = _receive(client, 32)
                            if secrets.compare_digest(response, witness.response) and witness.seen.is_set():
                                return {"state": "verified", "method": f"{plan.producer}_nonce_v1", "producer": plan.producer,
                                        "fingerprint": plan.fingerprint, "consumer_id": plan.consumer_id,
                                        "origin_address": witness.address}
                            break
                        except ConnectionRefusedError:
                            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                        except OSError:
                            break
                    return {"state": "failed", "message": "Маршрут не подтвердил обмен данными с текущим origin"}
                finally:
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"state": "unavailable", "message": "Изолированный клиент проверки или origin responder недоступен"}


@dataclass(frozen=True)
class ProbePlan:
    binding_id: str
    revision: int
    consumer_id: str
    address: str
    port: int
    listener_tag: str
    listener_port: int
    transport: str
    fingerprint: str
    client: dict = field(repr=False)
    producer: str = "direct_vless"
    engine: str = "xray"
    server_behavior: dict = field(default_factory=dict, repr=False)

    def summary(self) -> dict:
        return {"state": "planned", "binding_id": self.binding_id,
                "revision": self.revision, "consumer_id": self.consumer_id,
                "port": self.port, "transport": self.transport,
                "fingerprint": self.fingerprint, "producer": self.producer}

    def evidence(self, key: bytes) -> dict:
        tls = self.client.get("tls", {})
        return {"producer": self.producer, "consumer_id": self.consumer_id,
                "protocol": "vless" if self.engine == "xray" else self.producer.removeprefix("direct_"),
                "transport": self.transport, "vantage_point": "server", "plan_seal": plan_seal(self, key),
                **({"server_name": tls["server_name"]} if tls.get("server_name") else {})}


def prepare_direct_vless(binding: dict, consumers: list[dict], clients: list[dict],
                         config: dict, environment: dict) -> ProbePlan:
    """Select one live Direct credential without adding users or listeners."""
    if binding.get("state") != "active":
        raise ProbeUnavailable("Маршрут удалён из Network")
    kind = binding.get("kind")
    if kind not in {"cdn", "tls_relay"}:
        raise ProbeUnavailable("Для UDP-маршрута пока нет поддержанного клиента проверки")
    prefix = "CDN" if kind == "cdn" else "TLS"
    if environment.get(f"{prefix}_ENABLED") != "yes":
        raise ProbeUnavailable("Соответствующий VLESS listener отключён")
    address = str(binding["address"]).strip().lower()
    if str(environment.get(f"{prefix}_DOMAIN", "")).strip().lower() != address:
        raise ProbeUnavailable("Адрес маршрута не совпадает с действующей конфигурацией listener")
    tags = {"vless-cdn", "vless-cdn-websocket", "vless-tls-websocket"} if kind == "cdn" else {"vless-tls"}
    inbounds = [item for item in config.get("inbounds", [])
                if item.get("protocol") == "vless" and item.get("tag") in tags]
    if len(inbounds) != 1:
        raise ProbeUnavailable("Не найден однозначный VLESS listener маршрута")
    inbound = inbounds[0]
    listener_port = inbound.get("port")
    if type(listener_port) is not int or not 1 <= listener_port <= 65535:
        raise ProbeUnavailable("Порт listener не определён")
    stream = inbound.get("streamSettings", {})
    transport = stream.get("network")
    if transport not in {"ws", "websocket", "xhttp", "grpc"} or stream.get("security", "none") != "none":
        raise ProbeUnavailable("Транспорт listener пока не поддержан проверкой")
    references = {item["id"] for item in consumers if item.get("owner") == "direct"
                  and item.get("kind") == kind and item.get("address") == address
                  and item.get("reference") != "legacy-uncertain"}
    route = "cdn" if kind == "cdn" else "tls"
    for consumer in clients:
        consumer_id = f"direct:{consumer.get('id', '')}"
        if consumer_id not in references or consumer.get("protocol") != "vless-reality-xhttp" or route not in consumer.get("vless_routes", []):
            continue
        credential = consumer.get("public_key")
        live = [item for item in inbound.get("settings", {}).get("clients", [])
                if credential and item.get("id") == credential]
        if len(live) != 1 or live[0].get("flow"):
            continue
        # These existing Direct exports use TLS/443 terminated before Xray.
        # Do not infer a remote relay port from the local origin listener port.
        if binding.get("listen_port") not in {None, 443}:
            raise ProbeUnavailable("Внешний порт отличается от существующего Direct-экспорта")
        client_stream = {"network": transport, "security": "tls",
                         "tlsSettings": {"serverName": address, "allowInsecure": False}}
        key = {"ws": "wsSettings", "websocket": "wsSettings", "xhttp": "xhttpSettings", "grpc": "grpcSettings"}[transport]
        client_stream[key] = copy.deepcopy(stream.get(key, {}))
        outbound = {"protocol": "vless", "settings": {"vnext": [{"address": address, "port": 443,
                    "users": [{"id": credential, "encryption": "none"}]}]}, "streamSettings": client_stream}
        # Hash only nonsecret metadata; never persist a credential-derived hash.
        metadata = {"binding_id": binding["id"], "revision": binding["desired_revision"],
                    "consumer_id": consumer_id, "address": address,
                    "listener": {"tag": inbound["tag"], "port": listener_port, "stream": client_stream}}
        fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ProbePlan(binding["id"], binding["desired_revision"], consumer_id, address, 443,
                         inbound["tag"], listener_port, transport, fingerprint, outbound,
                         server_behavior={"routing": config.get("routing"), "outbounds": config.get("outbounds"),
                                          "settings": {key: value for key, value in inbound.get("settings", {}).items() if key != "clients"}})
    raise ProbeUnavailable("Нет существующего Direct-подключения с действующими credentials для этого маршрута")


def mihomo_source_paths(config_root: Path, profiles: Path, consumer_id: str, kind: str) -> dict[str, Path]:
    parts = consumer_id.split(":")
    if len(parts) != 3 or parts[0] != "mihomo" or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) for item in parts[1:]):
        raise ProbeUnavailable("Некорректная identity существующего Mihomo consumer")
    name = ("tls-" if kind == "tls_relay" else "") + "-".join(parts[1:])
    return {"config": config_root / "reality" / "config.json", "profiles": profiles,
            "gateway_route": config_root / "reality" / "caddy-routes" / f"{name}.json"}


def scoped_source_paths(producer: str, kind: str, consumer_id: str, *, direct_paths: dict[str, Path],
                        mihomo_root: Path, data_root: Path, quic_roots: dict[str, Path]) -> dict[str, Path]:
    if producer == "direct_vless":
        return direct_paths
    if producer == "mihomo_vless":
        return mihomo_source_paths(mihomo_root, data_root / "mihomo" / "profiles.json", consumer_id, kind)
    if producer in {"direct_tuic", "direct_hysteria2"}:
        protocol = producer.removeprefix("direct_")
        root = quic_roots[protocol]
        result = {"config": root / ("config.json" if protocol == "tuic" else "config.yaml"), "settings": root / "settings.json",
                  "certificate": root / "server.crt", "clients": direct_paths["clients"]}
        if protocol == "hysteria2": result["users"] = root / "users.json"
        return result
    raise ProbeUnavailable("Неизвестный producer результата проверки")


def _fingerprint(binding: dict, consumer_id: str, listener: dict, producer: str) -> str:
    metadata = {"binding_id": binding["id"], "revision": binding["desired_revision"],
                "address": binding["address"], "consumer_id": consumer_id, "producer": producer, "listener": listener}
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare_mihomo_vless(binding: dict, consumers: list[dict], profiles: list[dict], config: dict,
                          descriptors: dict[str, dict]) -> ProbePlan:
    if binding.get("state") != "active" or binding.get("kind") not in {"cdn", "tls_relay"}:
        raise ProbeUnavailable("Маршрут не поддержан Mihomo VLESS probe")
    kind, address = binding["kind"], binding["address"]
    variant = "cdn" if kind == "cdn" else "tls"
    if binding.get("listen_port") not in {None, 443}:
        raise ProbeUnavailable("Внешний порт не совпадает с Mihomo VLESS export")
    references = {item["id"] for item in consumers if item.get("owner") == "mihomo"
                  and item.get("kind") == kind and item.get("address") == address and item.get("reference") == "snapshot"}
    for profile in profiles:
        for connection in profile.get("connections", []):
            consumer_id = f"mihomo:{profile.get('id', '')}:{connection.get('id', '')}"
            if consumer_id not in references or connection.get("component") != "transport-reality":
                continue
            credential = connection.get("credential", {})
            settings = connection.get("settings", {})
            mode = settings.get("route_mode") or credential.get("route_mode") or "direct"
            if (variant == "cdn" and mode not in {"cdn", "both"}) or (variant == "tls" and mode != "tls"):
                continue
            if not credential.get(f"{variant}_enabled") or credential.get(f"{variant}_domain") != address or settings.get(f"{variant}_domain", address) != address:
                continue
            route_id = f"{profile['id']}-{connection['id']}"
            tag = f"mihomo-vless-{variant}-{route_id}"
            matches = [item for item in config.get("inbounds", []) if item.get("protocol") == "vless" and item.get("tag") == tag]
            descriptor = descriptors.get(("tls-" if variant == "tls" else "") + route_id, {})
            if len(matches) != 1:
                continue
            inbound = matches[0]
            stream = inbound.get("streamSettings", {})
            transport = stream.get("network")
            key = {"ws": "wsSettings", "websocket": "wsSettings", "xhttp": "xhttpSettings", "grpc": "grpcSettings", "httpupgrade": "httpupgradeSettings"}.get(transport)
            live_users = [item for item in inbound.get("settings", {}).get("clients", []) if credential.get("uuid") and item.get("id") == credential["uuid"]]
            if not key or stream.get("security", "none") != "none" or len(live_users) != 1 or live_users[0].get("flow"):
                continue
            port = inbound.get("port")
            live_path = stream.get(key, {}).get("serviceName") if transport == "grpc" else stream.get(key, {}).get("path")
            normalize = lambda value: str(value or "").lstrip("/") if transport == "grpc" else str(value or "")
            if (type(port) is not int or not 1 <= port <= 65535 or credential.get(f"{variant}_port") != port
                    or descriptor.get("port") != port or descriptor.get("domain") != address
                    or normalize(descriptor.get("path")) != normalize(live_path)
                    or normalize(credential.get(f"{variant}_path")) != normalize(live_path)
                    or {"ws": "websocket"}.get(transport, transport) != {"ws": "websocket"}.get(descriptor.get("transport"), descriptor.get("transport"))):
                continue
            encryption = credential.get("encryption") or "none"
            if (inbound.get("settings", {}).get("decryption", "none") == "none") != (encryption == "none"):
                continue
            client_stream = {"network": transport, "security": "tls", "tlsSettings": {"serverName": address, "allowInsecure": False}, key: copy.deepcopy(stream.get(key, {}))}
            outbound = {"protocol": "vless", "settings": {"vnext": [{"address": address, "port": 443, "users": [{"id": credential["uuid"], "encryption": encryption}]}]}, "streamSettings": client_stream}
            fingerprint = _fingerprint(binding, consumer_id, {"tag": tag, "port": port, "stream": client_stream,
                "gateway": {key: descriptor.get(key) for key in ("domain", "path", "port", "transport")}}, "mihomo_vless")
            return ProbePlan(binding["id"], binding["desired_revision"], consumer_id, address, 443, tag, port, transport, fingerprint, outbound, producer="mihomo_vless",
                             server_behavior={"routing": config.get("routing"), "outbounds": config.get("outbounds"),
                                              "settings": {key: value for key, value in inbound.get("settings", {}).items() if key != "clients"}})
    raise ProbeUnavailable("Нет однозначного существующего Mihomo VLESS consumer с live listener и gateway route")


def prepare_direct_quic(binding: dict, consumers: list[dict], clients: list[dict], protocol: str,
                        config: dict, settings: dict, certificate: str, server_name: str,
                        users: dict | None = None) -> ProbePlan:
    if binding.get("state") != "active" or binding.get("kind") != "udp_relay" or protocol not in {"tuic", "hysteria2"}:
        raise ProbeUnavailable("UDP probe поддерживает существующие Hysteria2/TUIC consumers")
    address = binding["address"]
    references = {item["id"] for item in consumers if item.get("owner") == "direct" and item.get("kind") == "udp_relay"
                  and item.get("address") == address and item.get("reference") == "snapshot"}
    if not certificate or not server_name:
        raise ProbeUnavailable("TLS identity UDP listener недоступна")
    for client in clients:
        consumer_id = f"direct:{client.get('id', '')}"
        if consumer_id not in references or client.get("protocol") != protocol or client.get("channel_mode") != "udp_relay" or client.get("route_endpoint") != address:
            continue
        port = client.get("port")
        if type(port) is not int or not 1 <= port <= 65535 or settings.get("port") != port or binding.get("listen_port") not in {None, port}:
            continue
        tls = {"enabled": True, "server_name": server_name, "insecure": False, "certificate": certificate, "alpn": ["h3"]}
        if protocol == "tuic":
            matches = [row for row in config.get("inbounds", []) if row.get("type") == "tuic" and row.get("listen_port") == port]
            if len(matches) != 1:
                continue
            inbound = matches[0]
            matched = [row for row in inbound.get("users", []) if row.get("uuid") == client.get("public_key") and row.get("name") == client["id"] and row.get("password")]
            if len(matched) != 1 or not inbound.get("tls", {}).get("enabled"):
                continue
            options = client.get("settings", {})
            outbound = {"type": "tuic", "server": address, "server_port": port, "uuid": matched[0]["uuid"], "password": matched[0]["password"],
                        "congestion_control": options.get("congestion_control", "bbr"), "heartbeat": options.get("heartbeat", "10s"),
                        "udp_relay_mode": "native", "zero_rtt_handshake": False, "tls": tls}
            tag = str(inbound.get("tag") or "tuic")
        else:
            password = (users or {}).get(client["id"])
            if not isinstance(password, str) or not password or config.get("listen") not in {f":{port}", f"0.0.0.0:{port}", f"[::]:{port}"} or config.get("auth", {}).get("type") != "http":
                continue
            tls_config = config.get("tls", {})
            if not tls_config.get("cert") or not tls_config.get("key"):
                continue
            outbound = {"type": "hysteria2", "server": address, "server_port": port, "password": f"{client['id']}:{password}", "tls": tls,
                        "up_mbps": int(client.get("settings", {}).get("up_mbps", 100)), "down_mbps": int(client.get("settings", {}).get("down_mbps", 100))}
            if config.get("obfs"):
                obfs = config["obfs"]
                password = obfs.get("salamander", {}).get("password")
                if obfs.get("type") != "salamander" or not password:
                    continue
                outbound["obfs"] = {"type": "salamander", "password": password}
            tag = "hysteria2"
        producer = f"direct_{protocol}"
        fingerprint = _fingerprint(binding, consumer_id, {"tag": tag, "port": port, "protocol": protocol, "server_name": server_name}, producer)
        return ProbePlan(binding["id"], binding["desired_revision"], consumer_id, address, port, tag, port, "quic", fingerprint, outbound, producer=producer, engine="singbox",
                         server_behavior=({"route": config.get("route"), "outbounds": config.get("outbounds"),
                                           "inbound": {key: value for key, value in inbound.items() if key != "users"}} if protocol == "tuic" else config))
    raise ProbeUnavailable("Нет существующего UDP consumer с актуальными credentials и портом listener")


def _socks_address(connection: socket.socket) -> tuple[str, int]:
    kind = _receive(connection, 1)
    if kind == b"\x01":
        raw = _receive(connection, 4)
        if len(raw) != 4: raise OSError("Incomplete SOCKS address")
        address = socket.inet_ntoa(raw)
    elif kind == b"\x04":
        raw = _receive(connection, 16)
        if len(raw) != 16: raise OSError("Incomplete SOCKS address")
        address = socket.inet_ntop(socket.AF_INET6, raw)
    else:
        raise OSError("Unsupported SOCKS relay address")
    raw = _receive(connection, 2)
    if len(raw) != 2: raise OSError("Incomplete SOCKS relay port")
    return address, int.from_bytes(raw, "big")


def execute_quic(plan: ProbePlan, binary: str, origin_address: str, *, timeout: float = 12) -> dict:
    timeout = max(0.2, min(float(timeout), 15))
    deadline = time.monotonic() + timeout
    process = None
    try:
        with OriginWitness(origin_address, timeout, "udp") as witness:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            outbound = copy.deepcopy(plan.client)
            outbound["tag"] = "probe-route"
            config = {"log": {"disabled": True}, "inbounds": [{"type": "mixed", "tag": "probe-input", "listen": "127.0.0.1", "listen_port": port}],
                      "outbounds": [outbound], "route": {"final": "probe-route"}}
            with tempfile.TemporaryDirectory(prefix="vps-network-probe-") as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                os.chmod(path, 0o600)
                try:
                    process = subprocess.Popen([binary, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            control = socket.create_connection(("127.0.0.1", port), timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                            break
                        except ConnectionRefusedError:
                            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    else:
                        return {"state": "failed", "message": "Изолированный QUIC-клиент не запустился"}
                    with control:
                        control.settimeout(max(0.01, deadline - time.monotonic()))
                        control.sendall(b"\x05\x01\x00")
                        if _receive(control, 2) != b"\x05\x00": raise OSError("SOCKS negotiation failed")
                        control.sendall(b"\x05\x03\x00\x01\x7f\x00\x00\x01\x00\x00")
                        if _receive(control, 3) != b"\x05\x00\x00": raise OSError("UDP association failed")
                        relay, relay_port = _socks_address(control)
                        if relay not in {"127.0.0.1", "0.0.0.0"} or not relay_port: raise OSError("Nonlocal SOCKS relay")
                        header = b"\x00\x00\x00\x01" + socket.inet_aton(witness.address) + witness.port.to_bytes(2, "big")
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                            udp.connect(("127.0.0.1", relay_port))
                            udp.settimeout(max(0.01, deadline - time.monotonic()))
                            udp.send(header + witness.challenge)
                            response = udp.recv(512)
                        if response[:10] == header and secrets.compare_digest(response[10:], witness.response) and witness.seen.is_set():
                            return {"state": "verified", "method": f"{plan.producer}_nonce_v1", "producer": plan.producer,
                                    "fingerprint": plan.fingerprint, "consumer_id": plan.consumer_id, "origin_address": witness.address}
                        return {"state": "failed", "message": "UDP-маршрут не подтвердил обмен датаграммами с текущим origin"}
                finally:
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"state": "failed" if process is not None else "unavailable", "message": "QUIC/UDP handshake и обмен с текущим origin не подтверждены"}
