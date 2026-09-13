"""Small DNS policy helpers; no daemon, query history or OS resolver fallback."""
import copy
import json
import secrets
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


BOOTSTRAP_URLS = {
    "cloudflare": "https+local://1.1.1.1/dns-query",
    "google": "https+local://8.8.8.8/dns-query",
    "dns-sb": "https+local://185.222.222.222/dns-query",
}


def build_xray_dns(servers: list[str], bootstrap_id: str = "cloudflare") -> dict:
    """Bootstrap matches only resolver hostnames, never ordinary site queries."""
    if bootstrap_id not in BOOTSTRAP_URLS:
        raise ValueError("Unknown bootstrap provider")
    hosts = []
    for server in servers:
        if server.startswith("https://"):
            parsed = urlsplit(server)
            if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Invalid DoH URL")
            try:
                import ipaddress
                ipaddress.ip_address(parsed.hostname)
            except ValueError:
                hosts.append("full:" + parsed.hostname)
    upstreams = list(servers)
    if hosts:
        upstreams.insert(0, {
            "address": BOOTSTRAP_URLS[bootstrap_id],
            "domains": list(dict.fromkeys(hosts)),
            "skipFallback": True,
        })
    return {"servers": upstreams, "queryStrategy": "UseIP", "disableFallbackIfMatch": True}


def successful_dns_response(response: bytes, query: bytes) -> bool:
    if len(query) < 12 or len(response) < len(query):
        return False
    identifier, flags, questions, answers, _, _ = struct.unpack("!6H", response[:12])
    return (
        identifier == struct.unpack("!H", query[:2])[0]
        and flags & 0x8000 != 0
        and flags & 0x020F == 0  # No truncation and RCODE=NOERROR.
        and questions == 1 and answers > 0
        and response[12:len(query)] == query[12:]
    )


def dns_probe_query() -> bytes:
    return struct.pack("!6H", secrets.randbelow(65536), 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00\x00\x01\x00\x01"


def probe_xray_dns(dns_config: dict, binary: str) -> None:
    """Exercise the actual Xray DNS path on loopback before touching live state."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = {
        "log": {"loglevel": "none"},
        "dns": copy.deepcopy(dns_config),
        "inbounds": [{"tag": "dns-check", "listen": "127.0.0.1", "port": port,
                      "protocol": "dokodemo-door", "settings": {"address": "1.1.1.1", "port": 53, "network": "udp"}}],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "ForceIP"},
             "streamSettings": {"sockopt": {"domainStrategy": "ForceIP"}}},
            {"tag": "dns-answer", "protocol": "dns"},
        ],
        "routing": {"rules": [{"type": "field", "inboundTag": ["dns-check"], "outboundTag": "dns-answer"}]},
    }
    with tempfile.TemporaryDirectory(prefix="vps-dns-check-") as directory:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        process = subprocess.Popen([binary, "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 9
            while time.monotonic() < deadline and process.poll() is None:
                query = dns_probe_query()
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                        client.settimeout(min(3, max(0.1, deadline - time.monotonic())))
                        client.connect(("127.0.0.1", port))
                        client.send(query)
                        response = client.recv(4096)
                    if successful_dns_response(response, query):
                        return
                except OSError:
                    pass
                time.sleep(0.1)
            raise ValueError("Xray DNS preflight failed")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
