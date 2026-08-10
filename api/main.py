from __future__ import annotations

import hmac
import base64
import ipaddress
import json
import csv
import logging
import os
import re
import secrets
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import uuid
import platform
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="312.net Infrastructure API", version="0.1.0")
logger = logging.getLogger("vps-control.api")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
basic_auth = HTTPBasic(auto_error=False)
SERVER_NAME = os.getenv("SERVER_NAME", "Unknown location")
PUBLIC_IP = os.getenv("PUBLIC_IP", "")
SERVER_CITY = os.getenv("SERVER_CITY", "Unknown")
SERVER_COUNTRY = os.getenv("SERVER_COUNTRY", "Unknown")
SERVER_COUNTRY_CODE = os.getenv("SERVER_COUNTRY_CODE", "")
WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
AWG_INTERFACE = os.getenv("AWG_INTERFACE", "awg0")
WG_PORT = int(os.getenv("WG_PORT", "51820"))
AWG_PORT = int(os.getenv("AWG_PORT", "51822"))
WG_SUBNET = ipaddress.ip_network(os.getenv("WG_SUBNET", "10.72.0.0/24"))
AWG_SUBNET = ipaddress.ip_network(os.getenv("AWG_SUBNET", "10.73.0.0/24"))
WG_CONFIG = Path(os.getenv("WG_CONFIG", f"/etc/wireguard/{WG_INTERFACE}.conf"))
AWG_CONFIG = Path(os.getenv("AWG_CONFIG", f"/etc/amnezia/amneziawg/{AWG_INTERFACE}.conf"))
try:
    WG_MTU = int(os.getenv("WG_MTU", "1280"))
except ValueError:
    WG_MTU = 1280
if not 1280 <= WG_MTU <= 1420:
    WG_MTU = 1280
AWG_MTU = int(os.getenv("AWG_MTU", "1280"))
WG_DNS = os.getenv("WG_DNS", "1.1.1.1, 1.0.0.1")
AWG_DNS = os.getenv("AWG_DNS", "1.1.1.1, 1.0.0.1")
WG_KEEPALIVE = int(os.getenv("WG_KEEPALIVE", "25"))
AWG_KEEPALIVE = int(os.getenv("AWG_KEEPALIVE", "25"))
try:
    SHADOWSOCKS_PORT_START = int(os.getenv("SHADOWSOCKS_PORT_START", "30000"))
except ValueError:
    SHADOWSOCKS_PORT_START = 30000
if not 1024 <= SHADOWSOCKS_PORT_START <= 55535:
    SHADOWSOCKS_PORT_START = 30000
# A stream proxy has no WireGuard-style handshake.  Consider it online only
# while its byte counters or authenticated requests were observed recently;
# this prevents iOS keepalive sockets from being reported as usable traffic.
STREAM_ACTIVITY_WINDOW_S = 30
SHADOWSOCKS_CONFIG_DIR = Path("/etc/vps-control/shadowsocks/clients")
VLESS_CONFIG_DIR = Path("/etc/vps-control/vless-reality-xhttp")
VLESS_CONFIG = VLESS_CONFIG_DIR / "config.json"
VLESS_ENV = VLESS_CONFIG_DIR / "reality.env"
AWG_PROFILE = {
    "Jc": os.getenv("AWG_JC", "6"), "Jmin": os.getenv("AWG_JMIN", "8"), "Jmax": os.getenv("AWG_JMAX", "80"),
    "S1": os.getenv("AWG_S1", "64"), "S2": os.getenv("AWG_S2", "112"),
    "H1": os.getenv("AWG_H1", "150000000"), "H2": os.getenv("AWG_H2", "600000000"),
    "H3": os.getenv("AWG_H3", "1000000000"), "H4": os.getenv("AWG_H4", "1400000000"),
}
DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/vps-control"))
ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/vps-control.env"))
CLIENTS_FILE = DATA_DIR / "clients.json"
ACTION_FILE = DATA_DIR / "application-action.json"
AUTOMATION_FILE = DATA_DIR / "automation.json"
SERVICE_MODE_FILE = DATA_DIR / "service-mode.json"
TEST_BACKUP_DIR = DATA_DIR / "test-app-backup"
UPDATES_FILE = DATA_DIR / "security-updates.json"
APP_VERSION_FILE = DATA_DIR / "application-version.json"
LOGGING_CONFIG_FILE = Path("/etc/vps-control-logging.conf")
INSTALL_DIR = Path(os.getenv("INSTALL_DIR", "/opt/vps-control"))
CONTROL_COMMAND = os.getenv("CONTROL_COMMAND", "/usr/local/sbin/vps-control")
PROTOCOL_IMAGES_DIR = INSTALL_DIR / "protocol-images"
MONITOR_DIR = DATA_DIR / "monitor"
DNS_SETTINGS_FILE = DATA_DIR / "dns-settings.json"
updates_refresh_lock = threading.Lock()
app_version_refresh_lock = threading.Lock()
resource_check_lock = threading.Lock()
network_diagnostic_lock = threading.Lock()
resource_check_cache: dict[str, dict] = {}
network_diagnostic_cache: dict[str, dict] = {}
client_quality_cache: dict[str, dict] = {}
stream_stats_cache: dict[str, dict] = {}
XRAY_BIN = "/usr/local/lib/vps-control-vless-reality-xhttp/xray"
client_mutation_lock = threading.Lock()
cpu_usage_lock = threading.Lock()
cpu_previous: tuple[int, int] | None = None
RESOURCE_TARGETS = (
    ("Google", "https://www.google.com/generate_204"),
    ("YouTube", "https://www.youtube.com/"),
    ("Instagram", "https://www.instagram.com/"),
    ("Facebook", "https://www.facebook.com/"),
    ("WhatsApp", "https://www.whatsapp.com/"),
    ("X / Twitter", "https://x.com/"),
    ("Reddit", "https://www.reddit.com/"),
    ("Wikipedia", "https://www.wikipedia.org/"),
    ("Yandex", "https://ya.ru/"),
    ("VK", "https://vk.com/"),
    ("Telegram", "https://telegram.org/"),
    ("Rutube", "https://rutube.ru/"),
    ("Mail.ru", "https://mail.ru/"),
    ("Cloudflare", "https://1.1.1.1/"),
    ("GitHub", "https://github.com/"),
    ("Microsoft", "https://www.microsoft.com/"),
)

DNS_PROVIDERS = (
    {"id": "yandex-basic", "name": "Яндекс DNS — базовый", "country": "RU", "addresses": ["77.88.8.8", "77.88.8.1"], "doh_url": "https://common.dot.dns.yandex.net/dns-query", "filter": "Без фильтрации"},
    {"id": "yandex-safe", "name": "Яндекс DNS — безопасный", "country": "RU", "addresses": ["77.88.8.88", "77.88.8.2"], "filter": "Вредоносные сайты"},
    {"id": "yandex-family", "name": "Яндекс DNS — семейный", "country": "RU", "addresses": ["77.88.8.7", "77.88.8.3"], "filter": "Вредоносные и взрослые сайты"},
    {"id": "skydns", "name": "SkyDNS", "country": "RU", "addresses": ["193.58.251.251"], "filter": "Российская фильтрация"},
    {"id": "nsdi", "name": "НСДИ", "country": "RU", "addresses": ["195.208.4.1", "195.208.5.1"], "filter": "Национальная система доменных имён"},
    {"id": "safedns", "name": "SafeDNS", "country": "INT", "addresses": ["195.46.39.39", "195.46.39.40"], "filter": "Безопасность и категории"},
    {"id": "cloudflare", "name": "Cloudflare", "country": "INT", "addresses": ["1.1.1.1", "1.0.0.1"], "doh_url": "https://cloudflare-dns.com/dns-query", "filter": "Без фильтрации"},
    {"id": "cloudflare-malware", "name": "Cloudflare Malware", "country": "INT", "addresses": ["1.1.1.2", "1.0.0.2"], "filter": "Вредоносные сайты"},
    {"id": "google", "name": "Google Public DNS", "country": "INT", "addresses": ["8.8.8.8", "8.8.4.4"], "doh_url": "https://dns.google/dns-query", "filter": "Без фильтрации"},
    {"id": "quad9", "name": "Quad9 Secure", "country": "INT", "addresses": ["9.9.9.9", "149.112.112.112"], "doh_url": "https://dns.quad9.net/dns-query", "filter": "Вредоносные сайты"},
    {"id": "adguard", "name": "AdGuard DNS", "country": "INT", "addresses": ["94.140.14.14", "94.140.15.15"], "doh_url": "https://dns.adguard-dns.com/dns-query", "filter": "Реклама и трекеры"},
    {"id": "opendns", "name": "OpenDNS", "country": "INT", "addresses": ["208.67.222.222", "208.67.220.220"], "filter": "Базовая защита"},
    {"id": "cleanbrowsing", "name": "CleanBrowsing Security", "country": "INT", "addresses": ["185.228.168.9", "185.228.169.9"], "filter": "Вредоносные сайты"},
)


def require_token(credentials: HTTPBasicCredentials | None = Depends(basic_auth)) -> None:
    if credentials is None or not ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Administrator credentials are required", headers={"WWW-Authenticate": "Basic"})
    valid_user = hmac.compare_digest(credentials.username, ADMIN_USER)
    valid_password = hmac.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Invalid administrator credentials", headers={"WWW-Authenticate": "Basic"})


def system_boot_time() -> datetime | None:
    try:
        value = next(line.split()[1] for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OSError, StopIteration, ValueError):
        return None


def run(*args: str, timeout: int = 8, check: bool = False) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        if check:
            raise HTTPException(status_code=409, detail=f"{args[0]} is not installed")
        return ""
    except subprocess.TimeoutExpired:
        if check:
            raise HTTPException(status_code=504, detail=f"{args[0]} timed out")
        return ""
    if check and result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or f"{args[0]} failed")
    return result.stdout.strip()


def command_succeeds(*args: str, timeout: int = 8) -> bool:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_with_input(args: list[str], value: str) -> str:
    try:
        result = subprocess.run(args, input=value, capture_output=True, text=True, timeout=8, check=False)
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail=f"{args[0]} is not installed")
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or f"{args[0]} failed")
    return result.stdout.strip()


def cached_resource_availability(
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"],
) -> dict:
    cached = resource_check_cache.get(protocol)
    return {key: value for key, value in (cached or {}).items() if not key.startswith("_")}


def check_resource_availability(
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"],
) -> dict:
    cached = resource_check_cache.get(protocol)
    if not resource_check_lock.acquire(blocking=False):
        return cached_resource_availability(protocol)

    def check(target: tuple[str, str]) -> dict:
        name, url = target
        started = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "curl", "--silent", "--show-error", "--location", "--output", "/dev/null",
                    "--write-out", "%{http_code}", "--connect-timeout", "2", "--max-time", "4", url,
                ],
                capture_output=True, text=True, timeout=5, check=False,
            )
            code = int(result.stdout.strip() or 0)
            available = result.returncode == 0 and 100 <= code < 500
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            code = 0
            available = False
        return {
            "name": name,
            "available": available,
            "status_code": code or None,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            items = list(pool.map(check, RESOURCE_TARGETS))
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
            "_cached_at": time.time(),
        }
        resource_check_cache[protocol] = result
        return {key: value for key, value in result.items() if not key.startswith("_")}
    finally:
        resource_check_lock.release()


def read_clients() -> list[dict]:
    if not CLIENTS_FILE.exists():
        return []
    try:
        return json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def write_clients(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CLIENTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CLIENTS_FILE)


def interface_dump(protocol: Literal["wg", "awg"], include_quality: bool = True) -> list[dict]:
    command = "wg" if protocol == "wg" else "awg"
    interface = WG_INTERFACE if protocol == "wg" else AWG_INTERFACE
    output = run(command, "show", interface, "dump")
    rows = output.splitlines()
    if len(rows) < 2:
        return []
    clients = read_clients()
    by_key = {item["public_key"]: item for item in clients if item["protocol"] == protocol}
    now = int(time.time())
    peers = []
    for row in rows[1:]:
        columns = row.split("\t")
        if len(columns) < 8:
            continue
        key, _, endpoint, allowed, handshake, rx, tx, keepalive = columns[:8]
        meta = by_key.get(key, {})
        peers.append({
                "id": meta.get("id", key[:12]),
                "name": meta.get("name", f"Клиент {key[:8]}"),
                "protocol": protocol,
                "public_key": key,
                "endpoint": endpoint or None,
                "address": allowed.split(",")[0],
                "handshake_age_s": now - int(handshake) if int(handshake) else None,
                "rx_bytes": int(rx),
                "tx_bytes": int(tx),
                "keepalive": 0 if keepalive == "off" else int(keepalive),
                "enabled": True,
        })
    online_peers = [peer for peer in peers if peer["handshake_age_s"] is not None and peer["handshake_age_s"] < 180]
    if include_quality and online_peers:
        with ThreadPoolExecutor(max_workers=min(6, len(online_peers))) as pool:
            qualities = dict(pool.map(lambda peer: (peer["id"], client_connection_quality(peer)), online_peers))
        for peer in online_peers:
            peer.update(qualities[peer["id"]])
    for peer in peers:
        if include_quality and peer not in online_peers:
            peer.update({"quality": "offline", "latency_ms": None, "jitter_ms": None, "packet_loss_percent": None, "latency_source": "server_icmp_tunnel_ip", "quality_reason": "Нет активного handshake"})
    return peers


def recent_xray_activity() -> dict[str, int]:
    """Return seconds since the latest authenticated request for each Xray user."""
    output = run(
        "journalctl", "-u", "vps-control-vless-reality-xhttp.service", "--since", "24 hours ago",
        "--no-pager", "-o", "short-unix", "-n", "2000", timeout=4,
    )
    now = time.time()
    activity: dict[str, int] = {}
    for line in output.splitlines():
        match = re.search(r"^(\d+(?:\.\d+)?)\s+.*\bemail:\s*([^\s]+)", line)
        if match:
            activity[match.group(2)] = max(0, int(now - float(match.group(1))))
    return activity


def stream_sample(key: str, rx: int, tx: int, fallback_age: int | None = None) -> tuple[int | None, float, float]:
    now = time.time()
    previous = stream_stats_cache.get(key)
    # Journal activity is only a bootstrap value.  Do not re-apply it on every
    # poll: an old accepted request (or a long-lived idle socket) must not keep
    # the connection permanently online.  Once sampled, only byte deltas move
    # last_activity forward.
    last_activity = (
        (previous or {}).get("last_activity", 0)
        if previous
        else (now - fallback_age if fallback_age is not None else 0)
    )
    rx_bps = tx_bps = 0.0
    if previous:
        elapsed = max(now - previous["sampled_at"], 0.1)
        rx_delta = max(0, rx - previous["rx"])
        tx_delta = max(0, tx - previous["tx"])
        rx_bps, tx_bps = rx_delta / elapsed, tx_delta / elapsed
        if rx_delta or tx_delta:
            last_activity = now
    stream_stats_cache[key] = {
        "rx": rx, "tx": tx, "sampled_at": now, "last_activity": last_activity,
        "rx_bps": rx_bps, "tx_bps": tx_bps,
    }
    return (int(now - last_activity) if last_activity else None), round(rx_bps, 2), round(tx_bps, 2)


def xray_user_stats(email: str, journal_age: int | None = None) -> tuple[int, int, int | None, float, float]:
    output = run(XRAY_BIN, "api", "statsquery", "-server=127.0.0.1:10085", "-pattern", f"user>>>{email}>>>traffic>>>", timeout=3)
    uplink = downlink = 0
    try:
        payload = json.loads(output)
        for item in payload.get("stat", []):
            name = str(item.get("name", ""))
            value = int(item.get("value", 0))
            if name.endswith(">>>uplink"):
                uplink = value
            elif name.endswith(">>>downlink"):
                downlink = value
    except (TypeError, ValueError, json.JSONDecodeError):
        # Older Xray builds returned human-readable lines instead of JSON.
        for line in output.splitlines():
            match = re.search(r"(?:uplink|downlink):\s*(\d+)", line)
            if match:
                if "uplink" in line:
                    uplink = int(match.group(1))
                else:
                    downlink = int(match.group(1))
    age, rx_bps, tx_bps = stream_sample(f"vrx:{email}", uplink, downlink, journal_age)
    return uplink, downlink, age, rx_bps, tx_bps


def service_bytes(unit: str) -> tuple[int, int]:
    def value(name: str) -> int:
        try:
            return int(run("systemctl", "show", unit, "--property=" + name, "--value") or 0)
        except ValueError:
            return 0
    return value("IPIngressBytes"), value("IPEgressBytes")


def shadowsocks_connection_details(port: int) -> tuple[int, list[str]]:
    output = run("ss", "-Htn", "state", "established", f"( sport = :{port} )")
    sources: set[str] = set()
    count = 0
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 4:
            continue
        count += 1
        source = columns[3].rsplit(":", 1)[0]
        sources.add(source.strip("[]"))
    return count, sorted(sources)


def shadowsocks_connections(port: int) -> int:
    return shadowsocks_connection_details(port)[0]


def stream_proxy_dump() -> list[dict]:
    peers = []
    xray_activity = recent_xray_activity()
    for item in read_clients():
        protocol = item.get("protocol")
        if protocol == "shadowsocks":
            unit = f'vps-control-shadowsocks@{item.get("id", "")}.service'
            active = run("systemctl", "is-active", unit) == "active"
            address = f'{PUBLIC_IP}:{item.get("port", "—")}'
        elif protocol == "vless-reality-xhttp":
            active = run("systemctl", "is-active", "vps-control-vless-reality-xhttp.service") == "active"
            address = f'{PUBLIC_IP}:{item.get("port", 443)}'
            email = f'{item.get("id", "")}@312.net'
            rx_bytes, tx_bytes, handshake_age, rx_bps, tx_bps = xray_user_stats(email, xray_activity.get(email))
            active_connections = 1 if handshake_age is not None and handshake_age < STREAM_ACTIVITY_WINDOW_S else 0
        else:
            continue
        if protocol == "shadowsocks":
            rx_bytes, tx_bytes = service_bytes(unit)
            active_connections, active_sources = shadowsocks_connection_details(int(item.get("port", 0)))
            handshake_age, rx_bps, tx_bps = stream_sample(f'ss:{item.get("id", "")}', rx_bytes, tx_bytes)
            if not active_connections:
                handshake_age = None
        # For Shadowsocks, raw byte deltas also include unauthenticated scans.
        # Only an established TCP socket proves a live client. Xray activity is
        # authenticated and can safely use the recent-activity window.
        online = active and (
            active_connections > 0
            and handshake_age is not None
            and handshake_age < STREAM_ACTIVITY_WINDOW_S
            if protocol == "shadowsocks"
            else handshake_age is not None and handshake_age < STREAM_ACTIVITY_WINDOW_S
        )
        peers.append({
            "id": item.get("id", ""), "name": item.get("name", "Подключение"), "protocol": protocol,
            "public_key": item.get("public_key", item.get("id", "")), "endpoint": address, "address": address,
            "handshake_age_s": handshake_age, "rx_bytes": rx_bytes, "tx_bytes": tx_bytes, "enabled": True,
            "rx_bps": rx_bps, "tx_bps": tx_bps, "active_connections": active_connections,
            "active_sources": active_sources if protocol == "shadowsocks" else [],
            "quality": "stable" if online else "offline", "latency_ms": None, "jitter_ms": None, "packet_loss_percent": None, "latency_source": "not_supported",
            "quality_reason": (f"Активных соединений: {active_connections}" if online else "Нет недавней активности") if active else "Служба подключения остановлена",
        })
    return peers


def all_client_dump(include_quality: bool = True) -> list[dict]:
    return interface_dump("wg", include_quality) + interface_dump("awg", include_quality) + stream_proxy_dump()


def client_connection_quality(peer: dict) -> dict:
    cache_key = f'{peer["protocol"]}:{peer["id"]}'
    cached = client_quality_cache.get(cache_key)
    if cached and time.time() - cached["_cached_at"] < 30:
        return {key: value for key, value in cached.items() if key != "_cached_at"}
    address = str(peer.get("address", "")).split("/")[0]
    output = run("ping", "-n", "-q", "-c", "5", "-i", "0.2", "-W", "1", address)
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
    latency_match = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/([\d.]+)", output)
    loss = float(loss_match.group(1)) if loss_match else None
    latency = round(float(latency_match.group(1)), 1) if latency_match else None
    jitter = round(float(latency_match.group(2)), 1) if latency_match else None
    if latency is None:
        # A fresh WireGuard handshake proves the tunnel is active. Some clients
        # reject ICMP completely, which must not be reported as packet loss.
        loss = None
    if (loss is not None and loss >= 20) or (latency is not None and latency >= 500) or (jitter is not None and jitter >= 80):
        quality, reason = "error", "Существенные потери или критическая задержка"
    elif (loss is not None and loss > 0) or (latency is not None and latency >= 150) or (jitter is not None and jitter >= 30):
        quality, reason = "warning", "Небольшие потери или высокая задержка"
    elif latency is not None:
        quality, reason = "stable", "Соединение стабильно"
    else:
        quality, reason = "stable", "Туннель активен · ICMP недоступен"
    result = {
        "quality": quality, "latency_ms": latency, "jitter_ms": jitter, "packet_loss_percent": loss,
        "latency_source": "server_icmp_tunnel_ip", "sample_size": 5,
        "quality_reason": reason, "_cached_at": time.time(),
    }
    client_quality_cache[cache_key] = result
    return {key: value for key, value in result.items() if key != "_cached_at"}


def protocol_history(protocol: Literal["wg", "awg"], period_hours: int = 24) -> dict:
    path = MONITOR_DIR / f"{protocol}.csv"
    cutoff = int(time.time()) - period_hours * 3600
    rows: list[dict] = []
    if path.exists():
        try:
            with path.open(encoding="utf-8", newline="") as source:
                rows = [row for row in csv.DictReader(source) if int(row.get("epoch", 0)) >= cutoff]
        except (OSError, ValueError):
            rows = []
    if not rows:
        return {
            "period_hours": period_hours, "samples": 0, "availability_percent": None,
            "monitoring_gaps": 0, "service_interruptions": 0, "inactive_connection_periods": 0,
            "external_loss_percent": None, "latency_avg_ms": None, "latency_max_ms": None, "jitter_avg_ms": None,
            "interface_errors": 0, "interface_dropped": 0, "uplink_errors": 0, "uplink_dropped": 0,
            "conntrack_peak_percent": None,
            "received_bytes": 0, "transmitted_bytes": 0, "average_rx_bps": 0,
            "average_tx_bps": 0, "peak_rx_bps": 0, "peak_tx_bps": 0, "events": [],
        }

    # Installation samples describe setup time, not service downtime. Start the
    # availability window with the first observed active sample.
    first_active = next((index for index, row in enumerate(rows) if row.get("service_active") == "1"), None)
    if first_active is None:
        return {
            "period_hours": period_hours, "samples": 0, "availability_percent": None,
            "monitoring_gaps": 0, "service_interruptions": 0, "inactive_connection_periods": 0,
            "external_loss_percent": None, "latency_avg_ms": None, "latency_max_ms": None, "jitter_avg_ms": None,
            "interface_errors": 0, "interface_dropped": 0, "uplink_errors": 0, "uplink_dropped": 0,
            "conntrack_peak_percent": None,
            "received_bytes": 0, "transmitted_bytes": 0, "average_rx_bps": 0,
            "average_tx_bps": 0, "peak_rx_bps": 0, "peak_tx_bps": 0, "events": [],
        }
    rows = rows[first_active:]

    def number(row: dict, key: str, default: float = 0) -> float:
        try:
            return float(row.get(key, default))
        except (TypeError, ValueError):
            return default

    active_samples = sum(1 for row in rows if number(row, "service_active") == 1)
    service_interruptions = 0
    inactive_periods = 0
    monitoring_gaps = 0
    received = transmitted = peak_rx = peak_tx = 0.0
    latencies: list[float] = []
    jitters: list[float] = []
    loss_samples: list[float] = []
    conntrack_peak_percent = 0.0
    events: list[dict] = []
    previous = None
    for row in rows:
        for prefix in ("ping_1_1_1", "ping_8_8_8_8"):
            value = number(row, f"{prefix}_ms", -1)
            if value >= 0:
                latencies.append(value)
            jitter = number(row, f"{prefix}_jitter", -1)
            if jitter >= 0:
                jitters.append(jitter)
            explicit_loss = row.get(f"{prefix}_loss")
            if explicit_loss not in (None, ""):
                loss_samples.append(max(0, min(100, number(row, f"{prefix}_loss"))))
            elif value < 0:
                loss_samples.append(100)
            else:
                loss_samples.append(0)
        conntrack_max = number(row, "conntrack_max")
        if conntrack_max > 0:
            conntrack_peak_percent = max(
                conntrack_peak_percent, number(row, "conntrack_count") / conntrack_max * 100
            )
        if previous:
            elapsed = number(row, "epoch") - number(previous, "epoch")
            if elapsed > 150:
                monitoring_gaps += 1
                events.append({"at": row.get("timestamp"), "type": "monitor_gap", "seconds": int(elapsed)})
            if number(previous, "service_active") == 1 and number(row, "service_active") == 0:
                service_interruptions += 1
                events.append({"at": row.get("timestamp"), "type": "service_down"})
            if number(previous, "online_peers") > 0 and number(row, "online_peers") == 0:
                inactive_periods += 1
                events.append({"at": row.get("timestamp"), "type": "peers_offline"})
            if 0 < elapsed <= 150:
                rx_delta = max(0, number(row, "rx_bytes") - number(previous, "rx_bytes"))
                tx_delta = max(0, number(row, "tx_bytes") - number(previous, "tx_bytes"))
                received += rx_delta
                transmitted += tx_delta
                peak_rx = max(peak_rx, rx_delta / elapsed)
                peak_tx = max(peak_tx, tx_delta / elapsed)
        previous = row
    elapsed_total = max(1, number(rows[-1], "epoch") - number(rows[0], "epoch"))
    return {
        "period_hours": period_hours,
        "samples": len(rows),
        "availability_percent": round(active_samples / len(rows) * 100, 2),
        "monitoring_gaps": monitoring_gaps,
        "service_interruptions": service_interruptions,
        "inactive_connection_periods": inactive_periods,
        "external_loss_percent": round(sum(loss_samples) / len(loss_samples), 2) if loss_samples else None,
        "latency_avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "latency_max_ms": round(max(latencies), 2) if latencies else None,
        "jitter_avg_ms": round(sum(jitters) / len(jitters), 2) if jitters else None,
        "interface_errors": int(
            max(0, number(rows[-1], "rx_errors") - number(rows[0], "rx_errors"))
            + max(0, number(rows[-1], "tx_errors") - number(rows[0], "tx_errors"))
        ),
        "interface_dropped": int(
            max(0, number(rows[-1], "rx_dropped") - number(rows[0], "rx_dropped"))
            + max(0, number(rows[-1], "tx_dropped") - number(rows[0], "tx_dropped"))
        ),
        "uplink_errors": int(
            max(0, number(rows[-1], "uplink_rx_errors") - number(rows[0], "uplink_rx_errors"))
            + max(0, number(rows[-1], "uplink_tx_errors") - number(rows[0], "uplink_tx_errors"))
        ),
        "uplink_dropped": int(
            max(0, number(rows[-1], "uplink_rx_dropped") - number(rows[0], "uplink_rx_dropped"))
            + max(0, number(rows[-1], "uplink_tx_dropped") - number(rows[0], "uplink_tx_dropped"))
        ),
        "conntrack_peak_percent": round(conntrack_peak_percent, 2) if conntrack_peak_percent else None,
        "received_bytes": int(received),
        "transmitted_bytes": int(transmitted),
        "average_rx_bps": round(received / elapsed_total, 2),
        "average_tx_bps": round(transmitted / elapsed_total, 2),
        "peak_rx_bps": round(peak_rx, 2),
        "peak_tx_bps": round(peak_tx, 2),
        "events": events[-12:][::-1],
    }


def read_kernel_number(path: str) -> int:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return 0


def network_diagnostics(protocol: Literal["wg", "awg"], history: dict, force: bool = False) -> dict:
    cached = network_diagnostic_cache.get(protocol)
    if cached and not force and time.time() - cached["_cached_at"] < 45:
        return {key: value for key, value in cached.items() if key != "_cached_at"}
    if not network_diagnostic_lock.acquire(blocking=False):
        return {
            key: value for key, value in (cached or {
                "checked_at": None, "status": "pending", "score": None, "checks": [], "findings": [],
            }).items() if key != "_cached_at"
        }
    try:
        interface = WG_INTERFACE if protocol == "wg" else AWG_INTERFACE
        port = WG_PORT if protocol == "wg" else AWG_PORT
        route_rows: list[dict] = []
        try:
            route_rows = json.loads(run("ip", "-j", "-4", "route", "show", "default") or "[]")
        except json.JSONDecodeError:
            pass
        route = route_rows[0] if route_rows else {}
        uplink = str(route.get("dev", ""))
        gateway = str(route.get("gateway", ""))

        def link_stat(device: str, name: str) -> int:
            return read_kernel_number(f"/sys/class/net/{device}/statistics/{name}") if device else 0

        tunnel_mtu = 0
        uplink_mtu = 0
        try:
            links = json.loads(run("ip", "-j", "link", "show", "dev", interface) or "[]")
            tunnel_mtu = int(links[0].get("mtu", 0)) if links else 0
            uplinks = json.loads(run("ip", "-j", "link", "show", "dev", uplink) or "[]") if uplink else []
            uplink_mtu = int(uplinks[0].get("mtu", 0)) if uplinks else 0
        except (json.JSONDecodeError, ValueError):
            pass

        ping_output = run("ping", "-n", "-q", "-c", "5", "-i", "0.2", "-W", "1", "1.1.1.1", timeout=8)
        loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", ping_output)
        rtt_match = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/([\d.]+)", ping_output)
        live_loss = float(loss_match.group(1)) if loss_match else 100.0
        live_latency = round(float(rtt_match.group(1)), 2) if rtt_match else None
        live_jitter = round(float(rtt_match.group(2)), 2) if rtt_match else None

        dns_started = time.monotonic()
        dns_ok = command_succeeds("getent", "ahostsv4", "github.com", timeout=4)
        dns_ms = round((time.monotonic() - dns_started) * 1000)
        https_output = run(
            "curl", "--silent", "--show-error", "--output", "/dev/null",
            "--write-out", "%{http_code} %{time_connect} %{time_total}",
            "--connect-timeout", "3", "--max-time", "8", "https://www.google.com/generate_204",
            timeout=9,
        )
        https_parts = https_output.split()
        https_code = int(https_parts[0]) if https_parts and https_parts[0].isdigit() else 0
        https_connect_ms = round(float(https_parts[1]) * 1000) if len(https_parts) > 1 else None
        https_total_ms = round(float(https_parts[2]) * 1000) if len(https_parts) > 2 else None
        https_ok = 200 <= https_code < 400

        listener_output = run("ss", "-H", "-lun")
        udp_listening = any(
            re.search(rf"(?:^|[:.]){port}(?:\s|$)", line) for line in listener_output.splitlines()
        )
        unit = f"{'wg-quick' if protocol == 'wg' else 'awg-quick'}@{interface}.service"
        protocol_service_active = run("systemctl", "is-active", unit) == "active"
        forwarding = read_kernel_number("/proc/sys/net/ipv4/ip_forward") == 1
        conntrack_count = read_kernel_number("/proc/sys/net/netfilter/nf_conntrack_count")
        conntrack_max = read_kernel_number("/proc/sys/net/netfilter/nf_conntrack_max")
        conntrack_percent = round(conntrack_count / conntrack_max * 100, 1) if conntrack_max else None
        cpu_count = max(1, os.cpu_count() or 1)
        load1 = os.getloadavg()[0]
        load_percent = round(load1 / cpu_count * 100, 1)
        memory_total, memory_available = memory_info()
        memory_used_percent = round((memory_total - memory_available) / memory_total * 100, 1) if memory_total else None
        failed_units = len([line for line in run("systemctl", "--failed", "--no-legend", "--plain").splitlines() if line.strip()])

        safe_payload = max(1200, min(1472, (uplink_mtu or 1500) - 28))
        pmtu_ok = command_succeeds(
            "ping", "-n", "-c", "1", "-W", "2", "-M", "do", "-s", str(safe_payload), "1.1.1.1",
            timeout=4,
        )
        tunnel_budget = max(0, (uplink_mtu or 1500) - (80 if protocol == "awg" else 60))
        mtu_safe = bool(tunnel_mtu and tunnel_mtu <= tunnel_budget)

        uplink_errors = sum(link_stat(uplink, key) for key in ("rx_errors", "tx_errors"))
        uplink_dropped = sum(link_stat(uplink, key) for key in ("rx_dropped", "tx_dropped"))
        checks = [
            {"id": "route", "name": "Маршрут в интернет", "ok": bool(uplink), "value": f"{uplink or 'нет'} · gateway {gateway or '—'}"},
            {"id": "dns", "name": "DNS", "ok": dns_ok, "value": f"{dns_ms} мс" if dns_ok else "имя не разрешается"},
            {"id": "https", "name": "Внешний HTTPS", "ok": https_ok, "value": f"HTTP {https_code or '—'} · {https_total_ms or '—'} мс"},
            {"id": "protocol_service", "name": "Служба протокола", "ok": protocol_service_active, "value": "active" if protocol_service_active else "не запущена"},
            {"id": "udp", "name": f"UDP {port}", "ok": udp_listening, "value": "порт прослушивается" if udp_listening else "порт не найден"},
            {"id": "forwarding", "name": "Маршрутизация IPv4", "ok": forwarding, "value": "включена" if forwarding else "выключена"},
            {"id": "mtu", "name": "MTU туннеля", "ok": mtu_safe, "value": f"{tunnel_mtu or '—'} · бюджет до {tunnel_budget or '—'}"},
            {"id": "pmtu", "name": "Path MTU", "ok": pmtu_ok, "value": f"payload {safe_payload} B" if pmtu_ok else "крупный пакет не прошёл"},
            {"id": "load", "name": "Нагрузка VPS", "ok": load_percent < 90, "value": f"{load_percent}% · load {load1:.2f}/{cpu_count} CPU"},
            {"id": "services", "name": "Системные службы", "ok": failed_units == 0, "value": f"ошибок: {failed_units}"},
        ]
        findings: list[dict] = []

        def finding(severity: str, code: str, title: str, detail: str, action: str) -> None:
            findings.append({"severity": severity, "code": code, "title": title, "detail": detail, "action": action})

        historical_loss = history.get("external_loss_percent")
        if live_loss >= 20 or (historical_loss is not None and historical_loss >= 10):
            finding("critical", "packet_loss", "Критические потери пакетов", f"Сейчас {live_loss:.1f}%, за 24 часа {historical_loss or 0:.1f}%.", "Проверить маршрут и канал провайдера VPS; сравнить MTR до 1.1.1.1 и 8.8.8.8.")
        elif live_loss > 0 or (historical_loss is not None and historical_loss >= 2):
            finding("warning", "packet_loss", "Нестабильная доставка пакетов", f"Сейчас {live_loss:.1f}%, за 24 часа {historical_loss or 0:.1f}%.", "Снять MTR в обе стороны и проверить, на каком участке начинается потеря.")
        historical_jitter = history.get("jitter_avg_ms")
        if (live_jitter is not None and live_jitter >= 30) or (historical_jitter is not None and historical_jitter >= 30):
            finding("warning", "rtt_variation", "Высокий разброс RTT", f"mdev сейчас {live_jitter or 0:.1f} мс, за 24 часа {historical_jitter or 0:.1f} мс.", "Проверить загрузку канала, очереди и регион размещения VPS.")
        if not mtu_safe or not pmtu_ok:
            finding("critical" if not mtu_safe else "warning", "mtu", "Риск фрагментации или blackhole MTU", f"MTU туннеля {tunnel_mtu or 'не определён'}, внешний MTU {uplink_mtu or 'не определён'}.", "Уменьшить MTU туннеля и повторить проверку крупных пакетов с DF.")
        if history.get("uplink_dropped", 0) > 0 or uplink_dropped > 0:
            finding("warning", "uplink_drops", "Drops на внешнем интерфейсе", f"За 24 часа: {history.get('uplink_dropped', 0)}, всего на интерфейсе: {uplink_dropped}.", "Проверить перегрузку vNIC, qdisc, softnet и лимиты хостинга.")
        if history.get("interface_dropped", 0) > 0:
            finding("warning", "tunnel_drops", "Drops на интерфейсе туннеля", f"За 24 часа: {history.get('interface_dropped', 0)}.", "Проверить MTU, очереди и сетевые буферы UDP.")
        if uplink_errors > 0 or history.get("uplink_errors", 0) > 0:
            finding("critical", "uplink_errors", "Ошибки внешнего интерфейса", f"Текущее значение: {uplink_errors}.", "Передать показатели rx/tx errors провайдеру VPS.")
        if conntrack_percent is not None and conntrack_percent >= 80:
            finding("critical", "conntrack", "Таблица conntrack почти заполнена", f"{conntrack_count} из {conntrack_max} ({conntrack_percent}%).", "Найти всплеск соединений и увеличить nf_conntrack_max только после оценки памяти.")
        if load_percent >= 90:
            finding("warning", "server_load", "Высокая нагрузка VPS", f"Load1 {load1:.2f} при {cpu_count} CPU ({load_percent}%).", "Проверить процессы, CPU steal и конкуренцию за ресурсы на стороне хостинга.")
        if memory_used_percent is not None and memory_used_percent >= 90:
            finding("warning", "memory", "Недостаточно свободной памяти", f"Использовано {memory_used_percent}% памяти.", "Проверить OOM, swap и процессы с максимальным потреблением памяти.")
        if failed_units:
            finding("warning", "failed_services", "Есть службы в состоянии failed", f"Количество: {failed_units}.", "Открыть раздел «Службы» и проверить journalctl для отказавших unit.")
        if not dns_ok:
            finding("critical", "dns", "DNS не работает", "Сервер не смог разрешить github.com.", "Проверить resolv.conf, systemd-resolved и доступность DNS-серверов.")
        if not https_ok:
            finding("critical", "https", "Нет стабильного внешнего HTTPS", f"Ответ HTTP: {https_code or 'нет'}.", "Проверить DNS, маршрут, firewall и доступ провайдера.")
        if not protocol_service_active or not udp_listening or not forwarding:
            finding("critical", "protocol_path", "Трафик протокола не может проходить", f"Служба: {protocol_service_active}; UDP listener: {udp_listening}; IPv4 forwarding: {forwarding}.", "Восстановить службу протокола, UDP listener и net.ipv4.ip_forward.")

        critical = sum(1 for item in findings if item["severity"] == "critical")
        warnings = sum(1 for item in findings if item["severity"] == "warning")
        score = max(0, 100 - critical * 30 - warnings * 10)
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "critical" if critical else "warning" if warnings else "healthy",
            "score": score,
            "live": {
                "loss_percent": live_loss, "latency_ms": live_latency, "jitter_ms": live_jitter,
                "dns_ms": dns_ms, "https_connect_ms": https_connect_ms, "https_total_ms": https_total_ms,
            },
            "network": {
                "uplink": uplink, "gateway": gateway, "uplink_mtu": uplink_mtu,
                "tunnel_mtu": tunnel_mtu, "conntrack_count": conntrack_count,
                "conntrack_max": conntrack_max, "conntrack_percent": conntrack_percent,
                "load_percent": load_percent, "memory_used_percent": memory_used_percent,
                "failed_units": failed_units,
            },
            "checks": checks, "findings": findings, "_cached_at": time.time(),
        }
        network_diagnostic_cache[protocol] = result
        return {key: value for key, value in result.items() if key != "_cached_at"}
    finally:
        network_diagnostic_lock.release()


def cached_network_diagnostics(protocol: Literal["wg", "awg"]) -> dict:
    cached = network_diagnostic_cache.get(protocol)
    return {
        key: value for key, value in (cached or {
            "checked_at": None, "status": "pending", "score": None, "checks": [], "findings": [],
        }).items() if key != "_cached_at"
    }


def memory_info() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.strip().split()[0]) * 1024
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


def disk_info() -> tuple[int, int]:
    stat = os.statvfs("/")
    total = stat.f_blocks * stat.f_frsize
    available = stat.f_bavail * stat.f_frsize
    return total, available


def network_info() -> tuple[int, int]:
    received = transmitted = 0
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        interface, values = line.split(":", 1)
        if interface.strip() == "lo":
            continue
        columns = values.split()
        if len(columns) >= 9:
            received += int(columns[0])
            transmitted += int(columns[8])
    return received, transmitted


def cpu_usage_percent() -> float:
    global cpu_previous
    try:
        values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    except (OSError, ValueError, IndexError):
        return 0.0
    total = sum(values)
    idle = sum(values[index] for index in (3, 4) if index < len(values))
    with cpu_usage_lock:
        previous = cpu_previous
        cpu_previous = (total, idle)
    if not previous:
        time.sleep(0.1)
        return cpu_usage_percent()
    total_delta = total - previous[0]
    idle_delta = idle - previous[1]
    if total_delta <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100)), 1)


def refresh_updates_cache() -> None:
    if not updates_refresh_lock.acquire(blocking=False):
        return
    try:
        try:
            result = subprocess.run(
                ["bash", "-lc", "apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep '^Inst ' || true"],
                capture_output=True, text=True, timeout=150, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return
        output = result.stdout.strip()
        lines = output.splitlines()
        status = {
            "available": len(lines),
            "security": sum(1 for line in lines if "security" in line.lower() or "-security" in line.lower()),
            "kernel_available": any(re.search(r"^Inst linux-(image|headers|generic|virtual)", line) for line in lines),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = UPDATES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(UPDATES_FILE)
    finally:
        updates_refresh_lock.release()


def update_status() -> dict:
    cached: dict = {}
    try:
        cached = json.loads(UPDATES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    age = time.time() - UPDATES_FILE.stat().st_mtime if UPDATES_FILE.exists() else float("inf")
    if age > 900 and not updates_refresh_lock.locked():
        threading.Thread(target=refresh_updates_cache, daemon=True).start()
    if cached:
        return {**cached, "source": "apt-get simulation", "refreshing": age > 900}
    fallback = run("bash", "-lc", "apt list --upgradable 2>/dev/null | tail -n +2", timeout=10).splitlines()
    return {
        "available": len(fallback),
        "security": sum(1 for line in fallback if "security" in line.lower() or "-security" in line.lower()),
        "kernel_available": any(line.startswith(("linux-image", "linux-headers", "linux-generic", "linux-virtual")) for line in fallback),
        "checked_at": None,
        "source": "apt cache (предварительно)",
        "refreshing": True,
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "server": SERVER_NAME, "timestamp": datetime.now(timezone.utc).isoformat()}


class BootstrapRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AdminPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=16, max_length=128)
    confirm_password: str = Field(min_length=16, max_length=128)


@app.get("/api/auth/status")
def auth_status() -> dict:
    return {"configured": bool(ADMIN_USER and ADMIN_PASSWORD), "username": ADMIN_USER}


@app.put("/api/security/admin-password")
def change_admin_password(payload: AdminPasswordChange, _: None = Depends(require_token)) -> dict:
    global ADMIN_PASSWORD
    if not hmac.compare_digest(payload.current_password, ADMIN_PASSWORD):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Новые пароли не совпадают")
    password = payload.new_password
    if hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(status_code=400, detail="Новый пароль должен отличаться от текущего")
    if any(ord(character) < 33 or ord(character) > 126 for character in password):
        raise HTTPException(status_code=400, detail="Используйте печатные латинские символы без пробелов")
    categories = sum((
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    ))
    if categories < 3:
        raise HTTPException(status_code=400, detail="Используйте минимум три группы: строчные, заглавные, цифры и спецсимволы")
    if password.lower() in {"password", "changeme", "change-me", "vpscontrol.312", "vpsadmin-2026-7qm!rk2#"}:
        raise HTTPException(status_code=400, detail="Choose a non-default administrator password")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        # EnvironmentFile is also sourced by the shell monitor. JSON quoting
        # protects quotes, but '$' and backticks still expand inside double
        # quotes, so escape them explicitly before persisting the value.
        encoded = json.dumps(password, ensure_ascii=False).replace("$", "\\$").replace("`", "\\`")
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith("ADMIN_PASSWORD="):
                lines[index] = f"ADMIN_PASSWORD={encoded}"
                replaced = True
                break
        if not replaced:
            lines.append(f"ADMIN_PASSWORD={encoded}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(ENV_FILE, 0o600)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Unable to persist administrator password") from exc
    ADMIN_PASSWORD = password
    os.environ["ADMIN_PASSWORD"] = password
    return {"changed": True, "reauthenticate": True}


@app.get("/api/overview")
def overview(_: None = Depends(require_token)) -> dict:
    mem_total, mem_available = memory_info()
    disk_total, disk_available = disk_info()
    uptime_s = float(Path("/proc/uptime").read_text().split()[0])
    load = os.getloadavg()
    cpu_percent = cpu_usage_percent()
    network_rx, network_tx = network_info()
    return {
        "server": {
            "name": SERVER_NAME,
            "public_ip": PUBLIC_IP,
            "city": SERVER_CITY,
            "country": SERVER_COUNTRY,
            "country_code": SERVER_COUNTRY_CODE,
            "uptime_s": uptime_s,
        },
        "resources": {
            "load1": load[0],
            "load5": load[1],
            "load15": load[2],
            "cpu_percent": cpu_percent,
            "cpu_count": os.cpu_count() or 1,
            "memory_total": mem_total,
            "memory_available": mem_available,
            "disk_total": disk_total,
            "disk_available": disk_available,
            "network_rx": network_rx,
            "network_tx": network_tx,
        },
        "protocols": {
            "wg": {"interface": WG_INTERFACE, "port": WG_PORT, "active": bool(run("wg", "show", WG_INTERFACE))},
            "awg": {"interface": AWG_INTERFACE, "port": AWG_PORT, "active": bool(run("awg", "show", AWG_INTERFACE))},
        },
    }


@app.get("/api/security")
def security(_: None = Depends(require_token)) -> dict:
    failed = run(
        "bash",
        "-lc",
        "journalctl --since '24 hours ago' -u ssh -u sshd --no-pager 2>/dev/null "
        "| grep -Ec 'Failed password|Invalid user|authentication failure' || true",
    )
    accepted = run(
        "bash",
        "-lc",
        "journalctl --since '24 hours ago' -u ssh -u sshd --no-pager 2>/dev/null "
        "| grep -c 'Accepted ' || true",
    )
    listeners = run("ss", "-Hlntup").splitlines()
    ufw_config = Path("/etc/ufw/ufw.conf")
    ufw_rules_file = Path("/etc/ufw/user.rules")
    ufw_enabled = (
        ufw_config.exists()
        and any(line.strip() == "ENABLED=yes" for line in ufw_config.read_text(encoding="utf-8").splitlines())
    )
    ufw_rules = (
        [line.removeprefix("### tuple ###").strip() for line in ufw_rules_file.read_text(encoding="utf-8").splitlines()
         if line.startswith("### tuple ###")]
        if ufw_rules_file.exists()
        else []
    )
    ssh_config = run("/usr/sbin/sshd", "-T")
    ssh_values = {
        parts[0]: parts[1]
        for line in ssh_config.splitlines()
        if len(parts := line.split(maxsplit=1)) == 2
    }
    updates_state = update_status()
    sudo_users = run(
        "bash", "-lc",
        "getent group sudo | cut -d: -f4 | tr ',' '\\n' | sed '/^$/d'",
    ).splitlines()
    login_users = run(
        "bash", "-lc",
        "getent passwd | awk -F: '$7 !~ /(nologin|false)$/ {print $1}'",
    ).splitlines()
    fail2ban_output = run("fail2ban-client", "status", "sshd")
    currently_banned = re.search(r"Currently banned:\s*(\d+)", fail2ban_output)
    total_banned = re.search(r"Total banned:\s*(\d+)", fail2ban_output)
    apparmor_output = run("aa-status")
    apparmor_profiles = re.search(r"(\d+) profiles are loaded", apparmor_output)
    ssh_active = (
        run("systemctl", "is-active", "ssh") == "active"
        or run("systemctl", "is-active", "ssh.socket") == "active"
    )
    ssh_listening = any(re.search(r"(^|[\[\]:.])22\s", line) for line in listeners)
    ssh_active_connections = len(run(
        "bash", "-lc",
        "ss -Htn state established '( sport = :22 )' 2>/dev/null | sed '/^$/d'",
    ).splitlines())
    ssh_public_rule = False
    for rule in ufw_rules:
        parts = rule.split()
        if len(parts) >= 6 and parts[2] == "22":
            source = parts[5]
            if source in ("0.0.0.0/0", "::/0"):
                ssh_public_rule = True
                break
    access_mode = os.getenv("ACCESS_MODE", "external")
    http_port = str(os.getenv("HTTP_PORT", "80"))
    panel_listening = any(
        re.search(rf"(?:0\.0\.0\.0|\*|\[::\]):{re.escape(http_port)}\b", line)
        for line in listeners
    )
    panel_public_rule = False
    panel_vpn_interfaces = set()
    for rule in ufw_rules:
        parts = rule.split()
        if len(parts) < 6 or parts[2] != http_port or parts[0] != "allow":
            continue
        interface_tokens = {
            part.removeprefix("in_")
            for part in parts
            if part.startswith("in_")
        }
        vpn_interfaces = interface_tokens.intersection({WG_INTERFACE, AWG_INTERFACE})
        panel_vpn_interfaces.update(vpn_interfaces)
        if parts[5] in ("0.0.0.0/0", "::/0") and not vpn_interfaces:
            panel_public_rule = True
    panel_publicly_accessible = (
        ufw_enabled and panel_listening and panel_public_rule
    )
    panel_access_consistent = (
        panel_publicly_accessible
        if access_mode == "external"
        else not panel_publicly_accessible
        and {WG_INTERFACE, AWG_INTERFACE}.issubset(panel_vpn_interfaces)
    )
    legacy_services = {}
    for name in ("openvpn.service", "strongswan-starter.service", "xl2tpd.service"):
        enabled = run("systemctl", "is-enabled", name)
        active = run("systemctl", "is-active", name)
        legacy_services[name] = {"enabled": enabled or "not-found", "active": active == "active"}
    env_file = Path("/etc/vps-control.env")
    control_file = Path(CONTROL_COMMAND)
    env_mode = env_file.stat().st_mode & 0o777 if env_file.exists() else None
    control_mode = control_file.stat().st_mode & 0o777 if control_file.exists() else None
    api_local_listener = any(re.search(r"127\.0\.0\.1:8000\b", line) for line in listeners)
    api_public_listener = any(
        re.search(r"(?:0\.0\.0\.0|\*|\[::\]):8000\b", line)
        for line in listeners
    )
    weak_tokens = {"", "changeme", "change-me", "secret", "admin", "password"}
    forwarding_enabled = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    uplink_interface = run(
        "bash", "-lc", "ip -4 route show default | awk 'NR==1 {print $5}'"
    )
    global_stateful_return = command_succeeds(
        "iptables", "-C", "ufw-before-forward", "-m", "conntrack",
        "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
    )
    protocol_policies = {}
    for protocol, interface, subnet in (
        ("wg", WG_INTERFACE, WG_SUBNET),
        ("awg", AWG_INTERFACE, AWG_SUBNET),
    ):
        installed = Path(f"/sys/class/net/{interface}").exists()
        direct_route_allowed = installed and bool(uplink_interface) and command_succeeds(
            "iptables", "-C", "FORWARD", "-i", interface, "-o", uplink_interface,
            "-s", str(subnet), "-j", "ACCEPT",
        )
        route_allowed = (
            not installed
            or bool(uplink_interface)
            and (
                command_succeeds(
                    "iptables", "-C", "ufw-user-forward",
                    "-i", interface, "-o", uplink_interface,
                    "-s", str(subnet), "-j", "ACCEPT",
                )
                or direct_route_allowed
                or command_succeeds(
                    "iptables", "-C", "FORWARD", "-i", interface, "-j", "ACCEPT",
                )
            )
        )
        return_allowed = not installed or global_stateful_return or command_succeeds(
            "iptables", "-C", "FORWARD", "-o", interface, "-m", "conntrack",
            "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
        )
        nat_enabled = (
            not installed
            or bool(uplink_interface)
            and command_succeeds(
                "iptables", "-t", "nat", "-C", "POSTROUTING",
                "-s", str(subnet), "-o", uplink_interface, "-j", "MASQUERADE",
            )
        )
        protocol_policies[protocol] = {
            "installed": installed,
            "return_allowed": return_allowed,
            "route_allowed": route_allowed,
            "nat_enabled": nat_enabled,
            "healthy": not installed or (
                forwarding_enabled and return_allowed and route_allowed and nat_enabled
            ),
        }
    stateful_return = global_stateful_return or all(
        not policy["installed"] or policy["return_allowed"]
        for policy in protocol_policies.values()
    )
    return {
        "failed_ssh_records_24h": int(failed or 0),
        "accepted_ssh_24h": int(accepted or 0),
        "fail2ban_active": run("systemctl", "is-active", "fail2ban") == "active",
        "fail2ban": {
            "active": run("systemctl", "is-active", "fail2ban") == "active",
            "jail_active": bool(fail2ban_output),
            "currently_banned": int(currently_banned.group(1)) if currently_banned else 0,
            "total_banned": int(total_banned.group(1)) if total_banned else 0,
        },
        "firewall": {
            "active": ufw_enabled and run("systemctl", "is-active", "ufw") == "active",
            "backend": "ufw",
            "rules": ufw_rules,
            "forwarding_enabled": forwarding_enabled,
            "stateful_return": stateful_return,
            "uplink_interface": uplink_interface,
            "protocol_policies": protocol_policies,
            "panel_access": {
                "mode": access_mode,
                "port": int(http_port),
                "listening": panel_listening,
                "public_rule": panel_public_rule,
                "publicly_accessible": panel_publicly_accessible,
                "vpn_only": not panel_publicly_accessible,
                "allowed_interfaces": sorted(panel_vpn_interfaces),
                "consistent": panel_access_consistent,
            },
            "vpn_policy_healthy": (
                ufw_enabled
                and forwarding_enabled
                and stateful_return
                and all(policy["healthy"] for policy in protocol_policies.values())
            ),
        },
        "ssh": {
            "active": ssh_active,
            "password_authentication": ssh_values.get("passwordauthentication", "unknown"),
            "permit_root_login": ssh_values.get("permitrootlogin", "unknown"),
            "publicly_allowed": ssh_active and ssh_listening and ssh_public_rule,
            "active_connections": ssh_active_connections,
            "listen_addresses": [
                value for key, value in ssh_values.items() if key == "listenaddress"
            ],
            "max_auth_tries": ssh_values.get("maxauthtries", "unknown"),
            "x11_forwarding": ssh_values.get("x11forwarding", "unknown"),
            "tcp_forwarding": ssh_values.get("allowtcpforwarding", "unknown"),
        },
        "updates": {
            **updates_state,
            "reboot_required": Path("/var/run/reboot-required").exists(),
            "automatic": (
                run("systemctl", "is-enabled", "unattended-upgrades") == "enabled"
                or run("systemctl", "is-active", "unattended-upgrades") == "active"
            ),
        },
        "application_version": application_version_status(),
        "application_security": {
            "admin_password_strong": len(ADMIN_PASSWORD) >= 16 and ADMIN_PASSWORD.lower() not in weak_tokens,
            "cors_restricted": bool(CORS_ORIGINS) and "*" not in CORS_ORIGINS,
            "secrets_protected": (
                env_mode is not None
                and env_mode & 0o077 == 0
                and env_file.stat().st_uid == 0
            ),
            "secrets_mode": f"{env_mode:04o}" if env_mode is not None else "missing",
            "api_local_only": api_local_listener and not api_public_listener,
            "control_command_protected": (
                control_mode is not None
                and control_mode & 0o022 == 0
                and control_file.stat().st_uid == 0
            ),
            "control_command_mode": f"{control_mode:04o}" if control_mode is not None else "missing",
        },
        "system": {
            "kernel": platform.release(),
            "ipv4_forwarding": forwarding_enabled,
            "syn_cookies": Path("/proc/sys/net/ipv4/tcp_syncookies").read_text().strip() == "1",
            "rp_filter": run("sysctl", "-n", "net.ipv4.conf.all.rp_filter") == "1",
            "rp_filter_mode": int(run("sysctl", "-n", "net.ipv4.conf.all.rp_filter") or 0),
            "rp_filter_valid": (
                run("sysctl", "-n", "net.ipv4.conf.all.rp_filter") in ("1", "2")
                or (
                    Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
                    and any(Path(f"/sys/class/net/{interface}").exists() for interface in (WG_INTERFACE, AWG_INTERFACE))
                )
            ),
            "redirects_disabled": all(
                run("sysctl", "-n", key) == "0"
                for key in (
                    "net.ipv4.conf.all.accept_redirects",
                    "net.ipv4.conf.default.accept_redirects",
                    "net.ipv4.conf.all.send_redirects",
                    "net.ipv4.conf.default.send_redirects",
                )
            ),
            "source_route_disabled": all(
                run("sysctl", "-n", key) == "0"
                for key in (
                    "net.ipv4.conf.all.accept_source_route",
                    "net.ipv4.conf.default.accept_source_route",
                )
            ),
            "dmesg_restricted": run("sysctl", "-n", "kernel.dmesg_restrict") == "1",
            "auditd_active": run("systemctl", "is-active", "auditd") == "active",
            "sudo_users": sudo_users,
            "login_users": login_users,
            "apparmor": {
                "active": (
                    Path("/sys/module/apparmor/parameters/enabled").exists()
                    and Path("/sys/module/apparmor/parameters/enabled").read_text().strip().lower() == "y"
                ),
                "profiles": int(apparmor_profiles.group(1)) if apparmor_profiles else 0,
            },
        },
        "listeners": listeners,
        "listener_summary": {
            "tcp": len([line for line in listeners if line.split(maxsplit=1)[0].lower() == "tcp"]),
            "udp": len([line for line in listeners if line.split(maxsplit=1)[0].lower() == "udp"]),
            "local_only": len([line for line in listeners if "127.0.0.1:" in line or "[::1]:" in line]),
        },
        "legacy_services": legacy_services,
    }


@app.get("/api/security/logs")
def security_logs(
    source: Literal["ssh", "firewall", "system"] = "ssh",
    lines: int = 120,
    _: None = Depends(require_token),
) -> dict:
    lines = max(20, min(lines, 400))
    commands = {
        "ssh": ["journalctl", "-r", "-u", "ssh", "-u", "sshd", "-n", str(lines), "--no-pager", "-o", "short-iso"],
        "firewall": ["journalctl", "-r", "-u", "ufw", "-n", str(lines), "--no-pager", "-o", "short-iso"],
        "system": ["journalctl", "-r", "-p", "warning", "-n", str(lines), "--no-pager", "-o", "short-iso"],
    }
    return {"source": source, "lines": run(*commands[source], timeout=12).splitlines()}


def protocol_image_manifests() -> dict[str, dict]:
    images: dict[str, dict] = {}
    if not PROTOCOL_IMAGES_DIR.exists():
        return images
    for manifest_path in PROTOCOL_IMAGES_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        image_id = str(manifest.get("id", ""))
        installer = str(manifest.get("installer", ""))
        uninstaller = str(manifest.get("uninstaller", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", image_id):
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]+", installer) or not (manifest_path.parent / installer).is_file():
            continue
        if uninstaller and (
            not re.fullmatch(r"[A-Za-z0-9._-]+", uninstaller)
            or not (manifest_path.parent / uninstaller).is_file()
        ):
            continue
        interface_env = str(manifest.get("interface_env", ""))
        interface = os.getenv(interface_env, "") if interface_env else ""
        service_template = str(manifest.get("service", ""))
        # Stream proxies do not create a tunnel interface. Their manifests use a
        # fixed systemd unit, while WG-like images may still interpolate one.
        service = service_template.replace("{interface}", interface) if service_template else ""
        images[image_id] = {
            "id": image_id,
            "name": str(manifest.get("name", image_id)),
            "version": str(manifest.get("version", "")),
            "description": str(manifest.get("description", "")),
            "category": str(manifest.get("category", "network")),
            "category_name": str(manifest.get("category_name", "Сетевые модули")),
            "interface": interface,
            # Installed and running are different states. A stopped tunnel must
            # remain manageable instead of being offered for installation again.
            "installed": bool(service and run("systemctl", "show", service, "--property=LoadState", "--value") == "loaded"),
            "active": bool(service and run("systemctl", "is-active", service) == "active"),
            "removable": bool(uninstaller),
        }
    return images


@app.get("/api/protocol-images")
def protocol_images(_: None = Depends(require_token)) -> dict:
    return {"items": list(protocol_image_manifests().values())}


@app.post("/api/protocol-images/{image_id}/install")
def install_protocol_image(image_id: str, _: None = Depends(require_token)) -> dict:
    if image_id not in protocol_image_manifests():
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if ACTION_FILE.exists():
        try:
            previous_unit = json.loads(ACTION_FILE.read_text(encoding="utf-8")).get("unit", "")
            if previous_unit and run("systemctl", "is-active", previous_unit) in ("active", "activating"):
                raise HTTPException(status_code=409, detail="Another application action is already running")
        except (json.JSONDecodeError, OSError):
            pass
    unit = f"vps-control-protocol-{image_id}-{int(time.time())}"
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec",
            CONTROL_COMMAND, "protocol-install", image_id,
        ],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to install protocol image")
    action = {
        "unit": f"{unit}.service",
        "action": f"protocol-install:{image_id}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "state": "activating",
        "progress": 3,
        "message": "Запуск установки протокола",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTION_FILE.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")
    os.chmod(ACTION_FILE, 0o600)
    return action


@app.delete("/api/protocol-images/{image_id}")
def remove_protocol_image(image_id: str, _: None = Depends(require_token)) -> dict:
    image = protocol_image_manifests().get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if not image.get("removable"):
        raise HTTPException(status_code=409, detail="Protocol does not support removal")
    if os.getenv("ACCESS_MODE", "external") == "vpn" and image_id in ("wg", "awg"):
        alternate_id = "awg" if image_id == "wg" else "wg"
        alternate_interface = AWG_INTERFACE if alternate_id == "awg" else WG_INTERFACE
        alternate_tool = "awg" if alternate_id == "awg" else "wg"
        alternate_unit = f"{alternate_tool}-quick@{alternate_interface}.service"
        if not (
            Path(f"/sys/class/net/{alternate_interface}").exists()
            and run("systemctl", "is-active", alternate_unit) == "active"
        ):
            raise HTTPException(
                status_code=409,
                detail="The last active VPN module cannot be removed while panel access is VPN-only",
            )
    if ACTION_FILE.exists():
        try:
            previous_unit = json.loads(ACTION_FILE.read_text(encoding="utf-8")).get("unit", "")
            if previous_unit and run("systemctl", "is-active", previous_unit) in ("active", "activating"):
                raise HTTPException(status_code=409, detail="Another application action is already running")
        except (json.JSONDecodeError, OSError):
            pass
    unit = f"vps-control-protocol-remove-{image_id}-{int(time.time())}"
    result = subprocess.run(
        ["systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec", CONTROL_COMMAND, "protocol-remove", image_id],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to remove protocol")
    action = {
        "unit": f"{unit}.service", "action": f"protocol-remove:{image_id}",
        "started_at": datetime.now(timezone.utc).isoformat(), "state": "activating",
        "progress": 3, "message": "Запуск удаления протокола",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTION_FILE.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")
    os.chmod(ACTION_FILE, 0o600)
    return action


@app.get("/api/application/status")
def application_status(_: None = Depends(require_token)) -> dict:
    action = {}
    if ACTION_FILE.exists():
        try:
            action = json.loads(ACTION_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            action = {}
    unit = action.get("unit", "")
    if unit:
        recorded_state = action.get("state", "")
        if recorded_state in ("rebooting", "powering-off"):
            started_at = action.get("started_at", "")
            try:
                started_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                started_time = None
            boot_time = system_boot_time()
            resolved_state = "succeeded" if boot_time and started_time and boot_time > started_time else recorded_state
            # The transient unit is intentionally collected after completion;
            # do not query it again while the server is coming back online.
            result = action.get("result", "success")
        elif recorded_state in ("succeeded", "failed"):
            resolved_state = recorded_state
            result = action.get("result", "success" if recorded_state == "succeeded" else "failed")
        else:
            active_state = run("systemctl", "is-active", unit) or "unknown"
            result = run("systemctl", "show", unit, "--property=Result", "--value") or "unknown"
            if active_state in ("active", "activating"):
                resolved_state = active_state
            elif result == "success":
                resolved_state = "succeeded"
            elif active_state == "failed" or result == "failed":
                resolved_state = "failed"
            else:
                # A collected transient unit may disappear between the two
                # systemctl calls. Treat that as a completed operation.
                resolved_state = "finished"
        action["state"] = resolved_state
        action["result"] = result
        try:
            ACTION_FILE.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")
            os.chmod(ACTION_FILE, 0o600)
        except OSError:
            pass
    web_unit_loaded = run("systemctl", "show", "vps-control-web.service", "--property=LoadState", "--value") == "loaded"
    caddy_unit_loaded = run("systemctl", "show", "caddy.service", "--property=LoadState", "--value") == "loaded"
    legacy_container_names = run("docker", "ps", "--format", "{{.Names}}") if not (web_unit_loaded and caddy_unit_loaded) else ""
    legacy_runtime = any(name.startswith(("vps-control-web-", "vps-control-gateway-")) for name in legacy_container_names.splitlines())
    containers = []
    for service, unit, component_name, purpose in (
        ("web", "vps-control-web.service", "Веб-интерфейс", "Отдаёт интерфейс управления сервером"),
        ("gateway", "caddy.service", "Сетевой шлюз", "Публикует панель и направляет запросы к API"),
    ):
        active = run("systemctl", "is-active", unit) == "active"
        containers.append({
            "Service": service, "State": "running" if active else "stopped",
            "component_name": component_name, "purpose": purpose, "healthy": active,
            "status_text": "systemd-служба запущена" if active else "systemd-служба остановлена или неисправна",
        })
    return {
        "api": {
            "active": run("systemctl", "is-active", "vps-control-api.service") == "active",
            "enabled": run("systemctl", "is-enabled", "vps-control-api.service") == "enabled",
        },
        "containers": containers,
        "action": action,
        "service_mode": {
            "active": SERVICE_MODE_FILE.exists(),
            "rollback_available": (DATA_DIR / "test-app-backup").is_dir(),
        },
        "runtime": {
            "mode": "systemd" if web_unit_loaded and caddy_unit_loaded else "legacy-docker" if legacy_runtime else "incomplete",
            "migration_required": legacy_runtime,
        },
    }


class ApplicationAction(BaseModel):
    action: Literal["restart", "update", "test-update", "test-rollback", "network-check", "integrity-check", "identity", "secure", "kernel-update", "vpn-firewall", "optimize", "reboot", "poweroff"]


@app.post("/api/application/action")
def application_action(payload: ApplicationAction, _: None = Depends(require_token)) -> dict:
    if payload.action in ("test-update", "test-rollback") and not SERVICE_MODE_FILE.exists():
        raise HTTPException(status_code=409, detail="Test version requires active service mode")
    if ACTION_FILE.exists():
        try:
            previous = json.loads(ACTION_FILE.read_text(encoding="utf-8"))
            previous_unit = previous.get("unit", "")
            if previous_unit and run("systemctl", "is-active", previous_unit) in ("active", "activating"):
                raise HTTPException(status_code=409, detail="Another application action is already running")
        except json.JSONDecodeError:
            pass
    unit = f"vps-control-action-{int(time.time())}"
    bundled_command = INSTALL_DIR / "scripts" / "vps-control.sh"
    command = (
        ["/bin/bash", str(bundled_command), payload.action]
        if bundled_command.exists()
        else [CONTROL_COMMAND, payload.action]
    )
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--collect",
            "--property=Type=exec", *command,
        ],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to start application action")
    action = {
        "unit": f"{unit}.service",
        "action": payload.action,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "state": "activating",
        "progress": 3,
        "message": "Команда передана серверу",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTION_FILE.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")
    os.chmod(ACTION_FILE, 0o600)
    return action


@app.get("/api/application/logs")
def application_logs(lines: int = 160, _: None = Depends(require_token)) -> dict:
    lines = max(20, min(lines, 400))
    units = ["vps-control-api.service"]
    if ACTION_FILE.exists():
        try:
            action_unit = json.loads(ACTION_FILE.read_text(encoding="utf-8")).get("unit")
            if action_unit:
                units.insert(0, action_unit)
        except (json.JSONDecodeError, OSError):
            pass
    args = ["journalctl", "-r"]
    for unit in units:
        args.extend(["-u", unit])
    args.extend(["-n", str(lines), "--no-pager", "-o", "short-iso"])
    return {"lines": run(*args, timeout=12).splitlines()}


def managed_services() -> dict[str, dict]:
    return {
        "api": {
            "name": "API 312.net", "unit": "vps-control-api.service",
            "controls": ["restart"], "disabled_controls": ["stop"],
        },
        "web": {"name": "Web 312.net", "unit": "vps-control-web.service", "controls": ["restart"], "disabled_controls": ["stop"]},
        "gateway": {"name": "Caddy", "unit": "caddy.service", "controls": ["restart"], "disabled_controls": ["stop"]},
        "wg": {"name": "WireGuard", "unit": f"wg-quick@{WG_INTERFACE}.service", "controls": ["start", "stop", "restart"]},
        "awg": {"name": "AmneziaWG", "unit": f"awg-quick@{AWG_INTERFACE}.service", "controls": ["start", "stop", "restart"]},
        "monitor": {"name": "Мониторинг VPN", "unit": "vpn-monitor.timer", "controls": ["start", "stop", "restart"]},
        "fail2ban": {"name": "Fail2ban", "unit": "fail2ban.service", "controls": ["start", "stop", "restart"]},
        "updates": {
            "name": "Автообновления Ubuntu", "unit": "unattended-upgrades.service",
            "controls": ["start", "stop", "restart"],
        },
        "ssh": {
            "name": "SSH · критическая служба", "unit": "ssh.service",
            "controls": ["start", "stop", "restart"], "disabled_controls": [],
        },
    }


def installed_build_commit() -> str:
    try:
        return (INSTALL_DIR / ".build-commit").read_text(encoding="utf-8").strip()
    except OSError:
        return os.getenv("BUILD_COMMIT", "unknown").strip()


def expected_application_branch() -> str:
    return "main" if SERVICE_MODE_FILE.exists() and TEST_BACKUP_DIR.is_dir() else "stabl"


def application_repository_url() -> str:
    configured = os.getenv("APP_REPOSITORY_URL", "").strip()
    if configured:
        return configured
    manager_config = Path("/etc/vps-control-manager.conf")
    if not manager_config.exists():
        return ""
    return run(
        "bash", "-lc",
        'source /etc/vps-control-manager.conf 2>/dev/null; printf "%s" "${REMOTE_URL:-}"',
        timeout=3,
    )


def refresh_application_version_cache() -> None:
    if not app_version_refresh_lock.acquire(blocking=False):
        return
    try:
        current = installed_build_commit()
        branch = expected_application_branch()
        repository = application_repository_url()
        latest = ""
        error = ""
        if not repository:
            error = "Источник обновлений не настроен"
        else:
            output = run("git", "ls-remote", repository, f"refs/heads/{branch}", timeout=15)
            latest = output.split()[0] if output else ""
            if not latest:
                error = f"Не удалось проверить ветку {branch}"
        current_known = bool(current and current != "unknown")
        status = {
            "branch": branch,
            "current_commit": current,
            "latest_commit": latest[:12],
            "outdated": (not latest.startswith(current)) if latest and current_known else None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": error or ("" if current_known else "Код установленной сборки неизвестен"),
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = APP_VERSION_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(APP_VERSION_FILE)
    finally:
        app_version_refresh_lock.release()


def application_version_status() -> dict:
    cached: dict = {}
    try:
        cached = json.loads(APP_VERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    age = time.time() - APP_VERSION_FILE.stat().st_mtime if APP_VERSION_FILE.exists() else float("inf")
    expected_branch = expected_application_branch()
    installed_commit = installed_build_commit()
    refreshing = (
        age > 600
        or cached.get("branch") != expected_branch
        or cached.get("current_commit") != installed_commit
    )
    if refreshing and not app_version_refresh_lock.locked():
        threading.Thread(target=refresh_application_version_cache, daemon=True).start()
    if cached:
        return {**cached, "refreshing": refreshing}
    return {
        "branch": expected_branch, "current_commit": installed_commit, "latest_commit": "",
        "outdated": None, "checked_at": None, "error": "", "refreshing": True,
    }


def service_details(service_id: str, definition: dict) -> dict:
    unit = definition["unit"]
    properties = {}
    for line in run(
        "systemctl", "show", unit,
        "--property=LoadState,ActiveState,SubState,UnitFileState,NRestarts,ActiveEnterTimestamp,Description",
    ).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    details = {
        "id": service_id,
        "name": definition["name"],
        "unit": unit,
        "installed": properties.get("LoadState") == "loaded",
        "active": properties.get("ActiveState") == "active",
        "state": properties.get("ActiveState", "unknown"),
        "substate": properties.get("SubState", "unknown"),
        "enabled": properties.get("UnitFileState") in ("enabled", "enabled-runtime", "static"),
        "unit_file_state": properties.get("UnitFileState", "unknown"),
        "restarts": int(properties.get("NRestarts") or 0),
        "active_since": properties.get("ActiveEnterTimestamp", ""),
        "description": properties.get("Description", ""),
        "controls": definition["controls"],
        "disabled_controls": definition.get("disabled_controls", []),
    }
    if service_id == "ssh":
        socket_active = run("systemctl", "is-active", "ssh.socket") == "active"
        socket_enabled = run("systemctl", "is-enabled", "ssh.socket") in ("enabled", "enabled-runtime", "static")
        details["active"] = details["active"] or socket_active
        details["enabled"] = details["enabled"] or socket_enabled
        details["unit"] = "ssh.service + ssh.socket"
        details["substate"] = "socket activation" if socket_active and properties.get("ActiveState") != "active" else details["substate"]
    return details


def default_automation() -> dict:
    return {
        "reboot": {"enabled": False, "cadence": "weekly", "weekday": "Sun", "hour": 4, "minute": 0},
        "cleanup": {"enabled": False, "cadence": "weekly", "weekday": "Sun", "hour": 3, "minute": 0},
        "update": {"enabled": False, "cadence": "weekly", "weekday": "Sun", "hour": 2, "minute": 30},
    }


def read_automation() -> dict:
    try:
        stored = json.loads(AUTOMATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    defaults = default_automation()
    return {key: {**defaults[key], **stored.get(key, {})} for key in defaults}


def timer_details(timer_id: str) -> dict:
    unit = f"vps-control-auto-{timer_id}.timer"
    values = {}
    for line in run(
        "systemctl", "show", unit,
        "--property=LoadState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime",
    ).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "unit": unit,
        "installed": values.get("LoadState") == "loaded",
        "active": values.get("ActiveState") == "active",
        "last_trigger": values.get("LastTriggerUSec", ""),
        "next_run": values.get("NextElapseUSecRealtime", ""),
    }


@app.get("/api/services")
def services_status(_: None = Depends(require_token)) -> dict:
    failed = run("systemctl", "--failed", "--no-legend", "--plain").splitlines()
    items = []
    for service_id, definition in managed_services().items():
        details = service_details(service_id, definition)
        # Optional modules (WG/AWG, monitoring, fail2ban, etc.) are
        # not shown until their systemd unit is actually installed.
        module_configured = {
            "wg": WG_CONFIG.exists(),
            "awg": AWG_CONFIG.exists(),
            "monitor": WG_CONFIG.exists() or AWG_CONFIG.exists(),
        }.get(service_id, True)
        if details["installed"] and module_configured:
            items.append(details)
    vpn_urls = []
    for interface in (WG_INTERFACE, AWG_INTERFACE):
        address = run("bash", "-lc", f"ip -o -4 addr show dev {interface} 2>/dev/null | awk 'NR==1 {{split($4,a,\"/\"); print a[1]}}'")
        if address:
            vpn_urls.append(f"http://{address}:{os.getenv('HTTP_PORT', '80')}")
    logging_values = {}
    if LOGGING_CONFIG_FILE.exists():
        for line in LOGGING_CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                logging_values[key.strip()] = value.strip().strip('"')
    try:
        retention_days = max(0, min(365, int(logging_values.get("LOG_RETENTION_DAYS", "30") or 30)))
    except ValueError:
        retention_days = 30
    return {
        "items": items,
        "failed_units": len([line for line in failed if line.strip()]),
        "reboot_required": Path("/var/run/reboot-required").exists(),
        "automation": read_automation(),
        "timers": {
            "reboot": timer_details("reboot"), "cleanup": timer_details("cleanup"),
            "update": timer_details("update"),
        },
        "panel_access": {
            "mode": os.getenv("ACCESS_MODE", "external"),
            "public": os.getenv("ACCESS_MODE", "external") != "vpn",
            "vpn_urls": vpn_urls,
        },
        "service_mode": {"active": SERVICE_MODE_FILE.exists()},
        "logging": {
            "persistent": logging_values.get("LOG_PERSISTENT", "yes") == "yes",
            "retention_days": retention_days,
            "automatic_cleanup": retention_days > 0,
            "disk_usage": run("journalctl", "--disk-usage"),
        },
    }


@app.get("/api/live-status")
def live_status(_: None = Depends(require_token)) -> dict:
    """Cheap sub-second telemetry without diagnostics, package checks or ICMP."""
    mem_total, mem_available = memory_info()
    disk_total, disk_available = disk_info()
    load = os.getloadavg()
    network_rx, network_tx = network_info()
    ufw_config = Path("/etc/ufw/ufw.conf")
    live_clients = all_client_dump(include_quality=False)

    def protocol_live(protocol: str, interface: str) -> dict:
        protocol_clients = [client for client in live_clients if client["protocol"] == protocol]
        statistics = Path(f"/sys/class/net/{interface}/statistics")
        try:
            received = int((statistics / "rx_bytes").read_text())
            transmitted = int((statistics / "tx_bytes").read_text())
        except (OSError, ValueError):
            received = transmitted = 0
        return {
            "active": Path(f"/sys/class/net/{interface}").exists(),
            "peers": len(protocol_clients),
            "online_peers": len([
                client for client in protocol_clients
                if client.get("handshake_age_s") is not None and client["handshake_age_s"] < (
                    STREAM_ACTIVITY_WINDOW_S if protocol in ("shadowsocks", "vless-reality-xhttp") else 180
                )
            ]),
            "interface_rx_bytes": received,
            "interface_tx_bytes": transmitted,
        }

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "resources": {
            "load1": load[0],
            "cpu_percent": cpu_usage_percent(),
            "cpu_count": os.cpu_count() or 1,
            "memory_total": mem_total,
            "memory_available": mem_available,
            "disk_total": disk_total,
            "disk_available": disk_available,
            "network_rx": network_rx,
            "network_tx": network_tx,
        },
        "protocols": {
            "wg": protocol_live("wg", WG_INTERFACE),
            "awg": protocol_live("awg", AWG_INTERFACE),
        },
        "clients": live_clients,
        "security": {
            "firewall_active": (
                ufw_config.exists()
                and "ENABLED=yes" in ufw_config.read_text(encoding="utf-8")
            ),
            "fail2ban_active": Path("/run/fail2ban/fail2ban.sock").exists(),
            "ssh_listening": any(
                re.search(r"(^|[\[\]:.])22\s", line)
                for line in run("ss", "-Hlnt").splitlines()
            ),
        },
    }


class ServiceAction(BaseModel):
    action: Literal["start", "stop", "restart"]


@app.post("/api/services/{service_id}/action")
def manage_service(service_id: str, payload: ServiceAction, _: None = Depends(require_token)) -> dict:
    definition = managed_services().get(service_id)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown managed service")
    if payload.action not in definition["controls"]:
        raise HTTPException(status_code=409, detail="Action is not allowed for this service")
    if service_id in ("wg", "awg") and payload.action == "stop" and os.getenv("ACCESS_MODE", "external") == "vpn":
        alternate_id = "awg" if service_id == "wg" else "wg"
        alternate_definition = managed_services()[alternate_id]
        alternate_ready = (
            service_details(alternate_id, alternate_definition)["active"]
            and Path(f"/sys/class/net/{AWG_INTERFACE if alternate_id == 'awg' else WG_INTERFACE}").exists()
        )
        if not alternate_ready:
            raise HTTPException(
                status_code=409,
                detail="The last active VPN cannot be stopped while panel access is VPN-only",
            )
    if service_id == "ssh" and payload.action == "stop":
        vpn_ready = any(
            Path(f"/sys/class/net/{interface}").exists()
            and run("systemctl", "is-active", f"{tool}-quick@{interface}.service") == "active"
            for interface, tool in ((WG_INTERFACE, "wg"), (AWG_INTERFACE, "awg"))
        )
        panel_ready = (
            run("systemctl", "is-active", "vps-control-api.service") == "active"
            and run("systemctl", "is-active", "vps-control-web.service") == "active"
            and run("systemctl", "is-active", "caddy.service") == "active"
        )
        if not vpn_ready or not panel_ready:
            raise HTTPException(
                status_code=409,
                detail="SSH cannot be stopped until the VPN and control panel recovery path are active",
            )
    if service_id == "ssh":
        if payload.action == "stop":
            run("systemctl", "stop", "ssh.socket", "ssh.service", timeout=30, check=True)
        elif payload.action == "start":
            run("systemctl", "start", "ssh.socket", "ssh.service", timeout=30, check=True)
        else:
            run("systemctl", "restart", "ssh.socket", "ssh.service", timeout=30, check=True)
    else:
        run("systemctl", payload.action, definition["unit"], timeout=30, check=True)
    return service_details(service_id, definition)


class PanelAccessSettings(BaseModel):
    mode: Literal["external", "vpn"]


@app.put("/api/services/panel-access")
def update_panel_access(payload: PanelAccessSettings, _: None = Depends(require_token)) -> dict:
    if payload.mode == "vpn":
        available_interfaces = [
            interface for interface in (WG_INTERFACE, AWG_INTERFACE)
            if Path(f"/sys/class/net/{interface}").exists()
        ]
        if not available_interfaces:
            raise HTTPException(status_code=409, detail="No active VPN interface is available")
    unit = f"vps-control-access-{int(time.time())}"
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec",
            CONTROL_COMMAND, "access-mode", payload.mode,
        ],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to change panel access")
    return {"mode": payload.mode, "state": "activating", "unit": f"{unit}.service"}


class ServiceModeSettings(BaseModel):
    active: bool


@app.put("/api/services/service-mode")
def update_service_mode(payload: ServiceModeSettings, _: None = Depends(require_token)) -> dict:
    unit = f"vps-control-service-mode-{int(time.time())}"
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec",
            CONTROL_COMMAND, "service-mode", "enable" if payload.active else "disable",
        ],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to change service mode")
    return {"active": payload.active, "state": "activating", "unit": f"{unit}.service"}


class LoggingSettings(BaseModel):
    persistent: bool
    retention_days: int = Field(ge=0, le=365)


def start_control_task(name: str, *arguments: str) -> dict:
    unit = f"vps-control-{name}-{int(time.time())}"
    bundled_command = INSTALL_DIR / "scripts" / "vps-control.sh"
    command = (
        ["/bin/bash", str(bundled_command), *arguments]
        if bundled_command.exists()
        else [CONTROL_COMMAND, *arguments]
    )
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--wait", "--collect",
            "--property=Type=exec", *command,
        ],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or f"Unable to run {name}")
    return {"state": "finished", "unit": f"{unit}.service"}


@app.put("/api/services/logging")
def update_logging(payload: LoggingSettings, _: None = Depends(require_token)) -> dict:
    return start_control_task(
        "logging-config",
        "logging-config",
        "enable" if payload.persistent else "disable",
        str(payload.retention_days),
    )


@app.post("/api/services/logging/clear")
def clear_logs(_: None = Depends(require_token)) -> dict:
    return start_control_task("logs-clear", "logs-clear")


class AutomationSchedule(BaseModel):
    enabled: bool
    cadence: Literal["daily", "weekly", "monthly"]
    weekday: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] = "Sun"
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class AutomationSettings(BaseModel):
    reboot: AutomationSchedule
    cleanup: AutomationSchedule
    update: AutomationSchedule


@app.put("/api/services/automation")
def update_automation(payload: AutomationSettings, _: None = Depends(require_token)) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AUTOMATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(AUTOMATION_FILE)
    unit = f"vps-control-automation-apply-{int(time.time())}"
    bundled_command = INSTALL_DIR / "scripts" / "vps-control.sh"
    command = (
        ["/bin/bash", str(bundled_command), "automation-apply"]
        if bundled_command.exists()
        else [CONTROL_COMMAND, "automation-apply"]
    )
    result = subprocess.run(
        [
            "systemd-run", f"--unit={unit}", "--wait", "--collect", "--property=Type=exec",
            *command,
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or "Unable to apply automation settings")
    return {
        "automation": read_automation(),
        "timers": {
            "reboot": timer_details("reboot"), "cleanup": timer_details("cleanup"),
            "update": timer_details("update"),
        },
    }


class DnsCustomResolver(BaseModel):
    name: str = Field(default="Собственный DNS", min_length=2, max_length=48)
    addresses: list[str] = Field(min_length=1, max_length=4)
    doh_url: str = Field(default="", max_length=256)


class DnsSettingsUpdate(BaseModel):
    selected_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    apply_wg: bool = True
    apply_awg: bool = True
    apply_shadowsocks: bool = True
    apply_vrx: bool = True
    prefer_encrypted: bool = False
    fallback_enabled: bool = True
    custom: DnsCustomResolver | None = None


class DnsCheckRequest(BaseModel):
    provider_id: str | None = Field(default=None, max_length=64)


@app.get("/api/clients")
def clients(_: None = Depends(require_token)) -> dict:
    return {"items": all_client_dump()}


@app.get("/api/dns")
def dns_status(_: None = Depends(require_token)) -> dict:
    settings = read_dns_settings()
    providers = dns_provider_list(settings)
    selected = next((item for item in providers if item["id"] == settings["selected_id"]), None)
    selected_addresses = list(selected["addresses"] if selected else [])
    if not settings["fallback_enabled"]:
        selected_addresses = selected_addresses[:1]
    expected = ", ".join(selected_addresses)
    expected_vrx = list(selected_addresses)
    if settings["prefer_encrypted"] and selected and selected.get("doh_url"):
        expected_vrx.insert(0, selected["doh_url"])
    effects = {
        "wg": current_env_value("WG_DNS", WG_DNS),
        "awg": current_env_value("AWG_DNS", AWG_DNS),
        "shadowsocks": current_env_value("SHADOWSOCKS_DNS", "не настроен"),
        "vless-reality-xhttp": current_env_value("VRX_DNS", "не настроен"),
    }
    return {
        "settings": settings,
        "providers": providers,
        "protocol_effect": effects,
        "protocol_effect_details": {
            "wg": {"value": effects["wg"], "scope": "new_profiles", "changes_existing": False, "matches_selected": effects["wg"] == expected},
            "awg": {"value": effects["awg"], "scope": "new_profiles", "changes_existing": False, "matches_selected": effects["awg"] == expected},
            "shadowsocks": {"value": effects["shadowsocks"], "scope": "client_recommendation", "changes_existing": False, "matches_selected": effects["shadowsocks"] == expected},
            "vless-reality-xhttp": {"value": effects["vless-reality-xhttp"], "scope": "server_xray", "changes_existing": True, "matches_selected": effects["vless-reality-xhttp"] == ", ".join(expected_vrx)},
        },
    }


@app.put("/api/dns/settings")
def update_dns_settings(payload: DnsSettingsUpdate, _: None = Depends(require_token)) -> dict:
    data = payload.model_dump()
    if data.get("custom"):
        for address in data["custom"]["addresses"]:
            try:
                ipaddress.ip_address(address)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Некорректный DNS-адрес: {address}") from exc
        doh_url = data["custom"].get("doh_url", "")
        if doh_url and not re.fullmatch(r"https://[^\s]+", doh_url):
            raise HTTPException(status_code=422, detail="DoH URL должен использовать HTTPS")
    providers = dns_provider_list(data)
    selected = next((item for item in providers if item["id"] == data["selected_id"]), None)
    if not selected:
        raise HTTPException(status_code=422, detail="Выбранный DNS-профиль не найден")
    selected_addresses = selected["addresses"] if data["fallback_enabled"] else selected["addresses"][:1]
    addresses = ", ".join(selected_addresses)
    vrx_servers = list(selected_addresses)
    if data["prefer_encrypted"] and selected.get("doh_url"):
        vrx_servers.insert(0, selected["doh_url"])
    vrx_addresses = ", ".join(vrx_servers)
    env_updates = {}
    if data["apply_wg"]:
        env_updates["WG_DNS"] = addresses
    if data["apply_awg"]:
        env_updates["AWG_DNS"] = addresses
    if data["apply_shadowsocks"]:
        env_updates["SHADOWSOCKS_DNS"] = addresses
    if data["apply_vrx"]:
        env_updates["VRX_DNS"] = vrx_addresses
    env_original = ENV_FILE.read_bytes() if ENV_FILE.exists() else None
    vrx_original = VLESS_CONFIG.read_bytes() if data["apply_vrx"] and VLESS_CONFIG.exists() else None
    settings_original = DNS_SETTINGS_FILE.read_bytes() if DNS_SETTINGS_FILE.exists() else None
    temporary = DNS_SETTINGS_FILE.with_suffix(".tmp")
    try:
        if env_updates:
            persist_env_values(env_updates)
        if vrx_original is not None:
            apply_vrx_dns(vrx_servers)
        DNS_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(DNS_SETTINGS_FILE)
    except Exception as exc:
        logger.exception("DNS settings transaction failed; restoring previous state")
        if env_original is None:
            ENV_FILE.unlink(missing_ok=True)
        else:
            ENV_FILE.write_bytes(env_original)
            os.chmod(ENV_FILE, 0o600)
        if settings_original is None:
            DNS_SETTINGS_FILE.unlink(missing_ok=True)
        else:
            DNS_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            DNS_SETTINGS_FILE.write_bytes(settings_original)
            os.chmod(DNS_SETTINGS_FILE, 0o600)
        if vrx_original is not None:
            VLESS_CONFIG.write_bytes(vrx_original)
            os.chmod(VLESS_CONFIG, 0o640)
            os.chown(VLESS_CONFIG, 0, 65534)
            try:
                restart_vless_service()
            except Exception:
                logger.exception("VRX restart failed during DNS rollback")
        detail = exc.detail if isinstance(exc, HTTPException) else "Не удалось сохранить DNS; предыдущие настройки восстановлены"
        raise HTTPException(status_code=500, detail=detail) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return dns_status()


@app.post("/api/dns/check")
def check_dns(payload: DnsCheckRequest, _: None = Depends(require_token)) -> dict:
    providers = dns_provider_list()
    if payload.provider_id:
        providers = [item for item in providers if item["id"] == payload.provider_id]
        if not providers:
            raise HTTPException(status_code=404, detail="DNS-профиль не найден")
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(providers)))) as pool:
        results = list(pool.map(check_dns_provider, providers))
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "items": results}


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[\w .-]+$")
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"]


class ProtocolSettingsUpdate(BaseModel):
    mtu: int | None = Field(default=None, ge=1280, le=1420)
    timeout: int | None = Field(default=None, ge=30, le=3600)
    udp_mtu: int | None = Field(default=None, ge=576, le=1500)
    mode: Literal["tcp_only", "tcp_and_udp"] | None = None
    no_delay: bool | None = None
    xhttp_mode: Literal["auto", "stream-one", "stream-up", "packet-up"] | None = None
    dns: str | None = Field(default=None, min_length=3, max_length=512)
    keepalive: int | None = Field(default=None, ge=0, le=300)
    loglevel: Literal["debug", "info", "warning", "error", "none"] | None = None
    xpadding: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^\d+(?:-\d+)?$")
    sni: str | None = Field(default=None, min_length=4, max_length=253, pattern=r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
    xmux_concurrency: int | None = Field(default=None, ge=1, le=64)


def key(command: str) -> str:
    return run("bash", "-lc", command, check=True)


def current_env_value(name: str, fallback: str) -> str:
    try:
        line = next((row for row in reversed(ENV_FILE.read_text(encoding="utf-8").splitlines()) if row.startswith(name + "=")), "")
        value = line.split("=", 1)[1].strip() if line else ""
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        return value or fallback
    except OSError:
        return fallback


def read_dns_settings() -> dict:
    defaults = {"selected_id": "yandex-basic", "apply_wg": True, "apply_awg": True, "apply_shadowsocks": True, "apply_vrx": True, "prefer_encrypted": False, "fallback_enabled": True, "custom": None}
    try:
        saved = json.loads(DNS_SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**defaults, **saved}
    except (OSError, json.JSONDecodeError, TypeError):
        return defaults


def dns_provider_list(settings: dict | None = None) -> list[dict]:
    providers = [dict(item) for item in DNS_PROVIDERS]
    custom = (settings or read_dns_settings()).get("custom")
    if custom:
        providers.append({"id": "custom", "country": "CUSTOM", "filter": "Пользовательский", **custom})
    return providers


def apply_vrx_dns(addresses: list[str]) -> None:
    """Apply server-side Xray resolution without replacing a working config on validation failure."""
    original = VLESS_CONFIG.read_bytes()
    temporary = VLESS_CONFIG.with_suffix(".dns.tmp.json")
    try:
        config = json.loads(original.decode("utf-8"))
        config["dns"] = {"servers": addresses, "queryStrategy": "UseIP"}
        config.setdefault("routing", {})["domainStrategy"] = "IPIfNonMatch"
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, 65534)
        result = subprocess.run([XRAY_BIN, "run", "-test", "-config", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Xray rejected DNS configuration")
        temporary.replace(VLESS_CONFIG)
        restart_vless_service()
    except Exception as exc:
        VLESS_CONFIG.write_bytes(original)
        os.chmod(VLESS_CONFIG, 0o640)
        os.chown(VLESS_CONFIG, 0, 65534)
        raise HTTPException(status_code=500, detail="Не удалось применить DNS к VRX; рабочая конфигурация восстановлена") from exc
    finally:
        temporary.unlink(missing_ok=True)


def dns_wire_query(address: str, tcp: bool = False, timeout: float = 2.0) -> tuple[bool, float | None]:
    transaction = secrets.randbelow(65536)
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in "example.com".split(".")) + b"\0"
    packet = struct.pack("!HHHHHH", transaction, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", 1, 1)
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    started = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.connect((address, 53))
            if tcp:
                sock.sendall(struct.pack("!H", len(packet)) + packet)
                length = sock.recv(2)
                if len(length) != 2:
                    return False, None
                response = sock.recv(struct.unpack("!H", length)[0])
            else:
                sock.send(packet)
                response = sock.recv(4096)
        valid = len(response) >= 12 and struct.unpack("!H", response[:2])[0] == transaction and bool(response[2] & 0x80)
        return valid, round((time.monotonic() - started) * 1000, 1) if valid else None
    except (OSError, socket.timeout):
        return False, None


def check_dns_provider(provider: dict) -> dict:
    address = str(provider.get("addresses", [""])[0])
    udp_ok, udp_ms = dns_wire_query(address)
    tcp_ok, tcp_ms = dns_wire_query(address, tcp=True)
    doh_ok, doh_ms = False, None
    doh_url = str(provider.get("doh_url", ""))
    if doh_url:
        started = time.monotonic()
        try:
            result = subprocess.run(["curl", "-fsS", "--max-time", "4", "-H", "accept: application/dns-json", f"{doh_url}?name=example.com&type=A"], capture_output=True, timeout=5, check=False)
            doh_ok = result.returncode == 0 and bool(result.stdout)
            doh_ms = round((time.monotonic() - started) * 1000, 1) if doh_ok else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    available = udp_ok or tcp_ok or doh_ok
    latencies = [value for value in (udp_ms, tcp_ms, doh_ms) if value is not None]
    return {"id": provider["id"], "available": available, "udp_ok": udp_ok, "udp_ms": udp_ms, "tcp_ok": tcp_ok, "tcp_ms": tcp_ms, "doh_ok": doh_ok, "doh_ms": doh_ms, "latency_ms": min(latencies) if latencies else None}


def shadowsocks_firewall(action: Literal["add", "delete"], port: int) -> None:
    result = subprocess.run(
        [
            "systemd-run", "--wait", "--collect", "--pipe", "--quiet", "--property=Type=oneshot",
            CONTROL_COMMAND, "client-firewall", action, str(port),
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Unable to update Shadowsocks firewall rule")


def restart_vless_service() -> None:
    unit = "vps-control-vless-reality-xhttp.service"
    run("systemctl", "restart", unit, timeout=20, check=True)
    for _ in range(10):
        if run("systemctl", "is-active", unit) == "active":
            return
        time.sleep(0.2)
    raise RuntimeError("VLESS service did not become active")


def restore_vless_config(original: bytes, token: str) -> None:
    rollback = VLESS_CONFIG_DIR / f".config-{token}.rollback"
    try:
        rollback.write_bytes(original)
        os.chmod(rollback, 0o640)
        os.chown(rollback, 0, 65534)
        rollback.replace(VLESS_CONFIG)
        restart_vless_service()
    finally:
        rollback.unlink(missing_ok=True)


def next_address(protocol: Literal["wg", "awg"]) -> ipaddress.IPv4Address:
    network = WG_SUBNET if protocol == "wg" else AWG_SUBNET
    used = {
        ipaddress.ip_interface(item["address"]).ip
        for item in read_clients()
        if item["protocol"] == protocol and item.get("address")
    }
    for address in list(network.hosts())[1:]:
        if address not in used:
            return address
    raise HTTPException(status_code=409, detail="No free client addresses")


def append_peer(config: Path, client_id: str, public_key: str, psk: str, address: str) -> None:
    if not config.exists():
        raise HTTPException(status_code=409, detail=f"Server config does not exist: {config}")
    block = (
        f"\n# vps-control:{client_id}:begin\n[Peer]\nPublicKey = {public_key}\n"
        f"PresharedKey = {psk}\nAllowedIPs = {address}/32\n# vps-control:{client_id}:end\n"
    )
    with config.open("a", encoding="utf-8") as handle:
        handle.write(block)


@app.post("/api/clients")
def create_client(payload: ClientCreate, _: None = Depends(require_token)) -> dict:
    client_id = secrets.token_hex(8)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", payload.name).strip(".-") or "client"
    if payload.protocol == "shadowsocks":
        if run("systemctl", "show", "vps-control-shadowsocks.target", "--property=LoadState", "--value") != "loaded":
            raise HTTPException(status_code=409, detail="shadowsocks protocol is not installed")
        used_ports = {int(item["port"]) for item in read_clients() if item.get("protocol") == "shadowsocks" and item.get("port")}
        port = next((candidate for candidate in range(SHADOWSOCKS_PORT_START, min(65536, SHADOWSOCKS_PORT_START + 10000)) if candidate not in used_ports), None)
        if port is None:
            raise HTTPException(status_code=409, detail="No free Shadowsocks ports")
        password = secrets.token_urlsafe(32)
        method = "chacha20-ietf-poly1305"
        config_path = SHADOWSOCKS_CONFIG_DIR / f"{client_id}.json"
        try:
            SHADOWSOCKS_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps({
                "server": "0.0.0.0", "server_port": port, "password": password, "method": method,
                "timeout": 300, "mode": "tcp_and_udp", "fast_open": False,
                "no_delay": True, "mtu": 1200,
            }, indent=2), encoding="utf-8")
            os.chmod(config_path, 0o600)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Unable to save Shadowsocks client configuration") from exc
        try:
            shadowsocks_firewall("add", port)
            run("systemctl", "enable", "--now", f"vps-control-shadowsocks@{client_id}.service", timeout=20, check=True)
            if run("systemctl", "is-active", f"vps-control-shadowsocks@{client_id}.service") != "active":
                raise RuntimeError("Shadowsocks client service did not become active")
        except Exception:
            run("systemctl", "disable", "--now", f"vps-control-shadowsocks@{client_id}.service")
            try:
                shadowsocks_firewall("delete", port)
            except Exception:
                pass
            config_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Unable to start Shadowsocks client service")
        userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
        client_config = f"ss://{userinfo}@{PUBLIC_IP}:{port}#{urllib.parse.quote(payload.name)}"
        items = read_clients()
        items.append({"id": client_id, "name": payload.name, "protocol": payload.protocol, "public_key": client_id, "port": port, "created_at": datetime.now(timezone.utc).isoformat()})
        write_clients(items)
        return {"id": client_id, "filename": f"{safe_name}-shadowsocks.txt", "config": client_config}

    if payload.protocol == "vless-reality-xhttp":
        with client_mutation_lock:
            if not VLESS_CONFIG.exists() or not VLESS_ENV.exists():
                raise HTTPException(status_code=409, detail="vless-reality-xhttp protocol is not installed")
            # Xray detects the input format from the filename extension.
            tmp = VLESS_CONFIG_DIR / f".config-{client_id}.tmp.json"
            replaced = False
            stage = "чтение конфигурации"
            try:
                original = VLESS_CONFIG.read_bytes()
                config_data = json.loads(original.decode("utf-8"))
                reality = dict(line.split("=", 1) for line in VLESS_ENV.read_text(encoding="utf-8").splitlines() if "=" in line)
                inbound_clients = config_data["inbounds"][0]["settings"]["clients"]
                client_uuid = str(uuid.uuid4())
                inbound_clients.append({"id": client_uuid, "email": f"{client_id}@312.net"})
                stage = "запись временной конфигурации"
                tmp.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(tmp, 0o640)
                os.chown(tmp, 0, 65534)
                stage = "проверка конфигурации Xray"
                xray = "/usr/local/lib/vps-control-vless-reality-xhttp/xray"
                result = subprocess.run([xray, "run", "-test", "-config", str(tmp)], capture_output=True, text=True, timeout=15, check=False)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or "Xray rejected generated configuration")
                stage = "активация конфигурации"
                tmp.replace(VLESS_CONFIG)
                replaced = True
                restart_vless_service()
                target_host = reality.get("TARGET", "www.intel.com:443").rsplit(":", 1)[0]
                port = int(reality.get("PORT", "443"))
                xhttp_settings = config_data["inbounds"][0]["streamSettings"].get("xhttpSettings", {})
                xhttp_mode = str(xhttp_settings.get("mode", "auto"))
                query_values = {
                    "encryption": "none", "security": "reality", "sni": target_host, "fp": "chrome",
                    "alpn": "h2",
                    "pbk": reality.get("PUBLIC_KEY", ""), "sid": reality.get("SHORT_ID", ""),
                    "type": "xhttp", "host": target_host,
                    "path": reality.get("XHTTP_PATH", reality.get("PATH", "/")), "mode": xhttp_mode,
                }
                client_extra = {
                    "xPaddingBytes": "100-1000",
                    "xmux": {"maxConcurrency": "8-16", "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000"},
                    **xhttp_settings.get("extra", {}),
                }
                query_values["extra"] = json.dumps(client_extra, separators=(",", ":"))
                query = urllib.parse.urlencode(query_values)
                client_config = f"vless://{client_uuid}@{PUBLIC_IP}:{port}?{query}#{urllib.parse.quote(payload.name)}"
                stage = "сохранение подключения"
                items = read_clients()
                items.append({"id": client_id, "name": payload.name, "protocol": payload.protocol, "public_key": client_uuid, "port": port, "created_at": datetime.now(timezone.utc).isoformat()})
                write_clients(items)
                return {"id": client_id, "filename": f"{safe_name}-vless.txt", "config": client_config}
            except (OSError, json.JSONDecodeError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError, HTTPException) as exc:
                if replaced:
                    try:
                        restore_vless_config(original, client_id)
                    except Exception:
                        pass
                raise HTTPException(status_code=500, detail=f"Не удалось создать VLESS-подключение: {stage}") from exc
            except Exception as exc:
                # Do not turn an unexpected filesystem/systemd failure into a
                # blank HTTP 500. Keep the stage visible in the UI and retain
                # the traceback in the API journal for diagnosis.
                logger.exception("VLESS client creation failed at stage %s", stage)
                if replaced:
                    try:
                        restore_vless_config(original, client_id)
                    except Exception:
                        logger.exception("VLESS rollback failed for %s", client_id)
                raise HTTPException(status_code=500, detail=f"Не удалось создать VLESS-подключение: {stage}") from exc
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    command = "wg" if payload.protocol == "wg" else "awg"
    config_path = WG_CONFIG if payload.protocol == "wg" else AWG_CONFIG
    if not config_path.exists():
        raise HTTPException(status_code=409, detail=f"{payload.protocol} protocol is not installed")
    private_key = key(f"{command} genkey")
    public_key = key(f"printf '%s' '{private_key}' | {command} pubkey")
    psk = key(f"{command} genpsk")
    address = next_address(payload.protocol)
    interface = WG_INTERFACE if payload.protocol == "wg" else AWG_INTERFACE
    append_peer(config_path, client_id, public_key, psk, str(address))
    run_with_input(
        [command, "set", interface, "peer", public_key, "preshared-key", "/dev/stdin", "allowed-ips", f"{address}/32"],
        psk,
    )

    server_public = run(command, "show", interface, "public-key", check=True)
    extra = ""
    if payload.protocol == "awg":
        extra = "".join(f"{key} = {value}\n" for key, value in AWG_PROFILE.items())
    port = WG_PORT if payload.protocol == "wg" else AWG_PORT
    client_config = (
        f"[Interface]\nAddress = {address}/32\nDNS = {current_env_value('AWG_DNS', AWG_DNS) if payload.protocol == 'awg' else current_env_value('WG_DNS', WG_DNS)}\n"
        f"PrivateKey = {private_key}\nMTU = {AWG_MTU if payload.protocol == 'awg' else WG_MTU}\n{extra}\n[Peer]\n"
        f"PublicKey = {server_public}\nPresharedKey = {psk}\nAllowedIPs = 0.0.0.0/0\n"
        f"Endpoint = {PUBLIC_IP}:{port}\nPersistentKeepalive = {current_env_value('AWG_KEEPALIVE', str(AWG_KEEPALIVE)) if payload.protocol == 'awg' else current_env_value('WG_KEEPALIVE', str(WG_KEEPALIVE))}\n"
    )
    items = read_clients()
    items.append(
        {
            "id": client_id,
            "name": payload.name,
            "protocol": payload.protocol,
            "public_key": public_key,
            "address": f"{address}/32",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_clients(items)
    return {"id": client_id, "filename": f"{safe_name}-{payload.protocol}.conf", "config": client_config}


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: str, _: None = Depends(require_token)) -> dict:
    items = read_clients()
    item = next((entry for entry in items if entry["id"] == client_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Client not found")
    protocol = item["protocol"]
    if protocol == "shadowsocks":
        port = int(item.get("port", 0))
        run("systemctl", "disable", "--now", f"vps-control-shadowsocks@{client_id}.service", timeout=20)
        (SHADOWSOCKS_CONFIG_DIR / f"{client_id}.json").unlink(missing_ok=True)
        if port:
            try:
                shadowsocks_firewall("delete", port)
            except Exception as exc:
                raise HTTPException(status_code=500, detail="Unable to remove Shadowsocks firewall rule") from exc
        write_clients([entry for entry in items if entry["id"] != client_id])
        return {"deleted": client_id}
    if protocol == "vless-reality-xhttp":
        try:
            config_data = json.loads(VLESS_CONFIG.read_text(encoding="utf-8"))
            inbound_clients = config_data["inbounds"][0]["settings"]["clients"]
            config_data["inbounds"][0]["settings"]["clients"] = [client for client in inbound_clients if client.get("id") != item.get("public_key")]
            tmp = VLESS_CONFIG.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o640)
            os.chown(tmp, 0, 65534)
            tmp.replace(VLESS_CONFIG)
            run("systemctl", "restart", "vps-control-vless-reality-xhttp.service", timeout=20, check=True)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise HTTPException(status_code=409, detail="VLESS module configuration is invalid") from exc
        write_clients([entry for entry in items if entry["id"] != client_id])
        return {"deleted": client_id}
    command = "wg" if protocol == "wg" else "awg"
    interface = WG_INTERFACE if protocol == "wg" else AWG_INTERFACE
    config = WG_CONFIG if protocol == "wg" else AWG_CONFIG
    run(command, "set", interface, "peer", item["public_key"], "remove", check=True)
    text = config.read_text(encoding="utf-8")
    pattern = rf"\n?# vps-control:{re.escape(client_id)}:begin.*?# vps-control:{re.escape(client_id)}:end\n?"
    config.write_text(re.sub(pattern, "\n", text, flags=re.S), encoding="utf-8")
    write_clients([entry for entry in items if entry["id"] != client_id])
    return {"deleted": client_id}


def editable_protocol_settings(protocol: str, values: dict) -> list[dict]:
    if protocol in ("wg", "awg"):
        prefix = "WG" if protocol == "wg" else "AWG"
        return [{
            "key": "mtu", "label": "MTU туннеля", "type": "number",
            "value": int(values.get("mtu", 1280)), "min": 1280, "max": 1420,
            "help": "Применяется к серверному интерфейсу и новым клиентским конфигурациям.",
        }, {
            "key": "dns", "label": "DNS новых профилей", "type": "text",
            "value": str(values.get("dns", current_env_value(f"{prefix}_DNS", "1.1.1.1, 1.0.0.1"))),
            "help": "Список DNS через запятую. Применяется к новым конфигурациям.",
        }, {
            "key": "keepalive", "label": "Keepalive новых профилей, с", "type": "number",
            "value": int(values.get("keepalive", current_env_value(f"{prefix}_KEEPALIVE", "25"))), "min": 0, "max": 300,
            "help": "0 отключает keepalive; 25 секунд подходит для NAT и мобильных сетей.",
        }]
    if protocol == "shadowsocks":
        return [
            {"key": "timeout", "label": "Таймаут соединения, с", "type": "number", "value": int(values.get("timeout", 300)), "min": 30, "max": 3600},
            {"key": "udp_mtu", "label": "MTU UDP", "type": "number", "value": int(values.get("mtu", 1200)), "min": 576, "max": 1500},
            {"key": "mode", "label": "Транспорт", "type": "select", "value": str(values.get("mode", "tcp_and_udp")), "options": [
                {"value": "tcp_and_udp", "label": "TCP + UDP"}, {"value": "tcp_only", "label": "Только TCP"},
            ]},
            {"key": "no_delay", "label": "TCP no-delay", "type": "boolean", "value": bool(values.get("no_delay", True))},
            {"key": "dns", "label": "Рекомендуемый DNS клиента", "type": "text", "value": current_env_value("SHADOWSOCKS_DNS", "1.1.1.1, 1.0.0.1"), "help": "Сохраняется как политика администратора. SS-сервер не может принудительно изменить DNS устройства; настройте его в клиентском приложении."},
        ]
    return [{
        "key": "sni", "label": "SNI маскировки", "type": "text",
        "value": str(values.get("sni", "www.intel.com")),
        "help": "Публичный HTTPS-домен без https:// и порта. Изменение требует заново импортировать существующие VRX-профили.",
    }, {
        "key": "xhttp_mode", "label": "Режим XHTTP", "type": "select",
        "value": str(values.get("mode", "auto")),
        "options": [
            {"value": "auto", "label": "Автоматически (рекомендуется)"},
            {"value": "stream-one", "label": "Один поток"},
            {"value": "stream-up", "label": "Раздельный upload"},
            {"value": "packet-up", "label": "Пакетный upload"},
        ],
        "help": "Клиенты с режимом auto согласуют выбранный серверный режим автоматически.",
    }, {
        "key": "loglevel", "label": "Уровень журнала", "type": "select", "value": str(values.get("loglevel", "warning")),
        "options": [{"value": value, "label": label} for value, label in (("debug", "Debug"), ("info", "Info"), ("warning", "Warning"), ("error", "Error"), ("none", "Отключён"))],
    }, {
        "key": "xpadding", "label": "XHTTP padding, байт", "type": "text", "value": str(values.get("xPaddingBytes", "100-1000")),
        "help": "Одно число или диапазон, например 100-1000.",
    }, {
        "key": "xmux_concurrency", "label": "Параллелизм XHTTP", "type": "number", "value": int(values.get("xmuxConcurrency", 12)), "min": 1, "max": 64,
        "help": "Количество одновременных запросов на HTTP-соединение. 8–16 устраняет секундные очереди; применяется к новым VRX-профилям.",
    }, {
        "key": "dns", "label": "DNS VRX", "type": "text", "value": current_env_value("VRX_DNS", "1.1.1.1, 1.0.0.1"),
        "help": "Применяется к серверному резолверу Xray. DNS самого устройства задаётся в клиентском приложении.",
    }]


def persist_tunnel_mtu(protocol: Literal["wg", "awg"], mtu: int) -> None:
    config = WG_CONFIG if protocol == "wg" else AWG_CONFIG
    interface = WG_INTERFACE if protocol == "wg" else AWG_INTERFACE
    text = config.read_text(encoding="utf-8")
    interface_end = text.find("\n[Peer]")
    head = text if interface_end < 0 else text[:interface_end]
    tail = "" if interface_end < 0 else text[interface_end:]
    if re.search(r"(?im)^MTU\s*=", head):
        head = re.sub(r"(?im)^MTU\s*=.*$", f"MTU = {mtu}", head)
    else:
        head = head.rstrip() + f"\nMTU = {mtu}\n"
    temporary = config.with_suffix(config.suffix + ".tmp")
    temporary.write_text(head + tail, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(config)
    run("ip", "link", "set", "dev", interface, "mtu", str(mtu), check=True)
    env_key = "WG_MTU" if protocol == "wg" else "AWG_MTU"
    env_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    if re.search(rf"(?m)^{env_key}=", env_text):
        env_text = re.sub(rf"(?m)^{env_key}=.*$", f'{env_key}="{mtu}"', env_text)
    else:
        env_text = env_text.rstrip() + f'\n{env_key}="{mtu}"\n'
    ENV_FILE.write_text(env_text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)


def persist_env_values(values: dict[str, str | int | bool]) -> None:
    env_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    for key, value in values.items():
        encoded = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        line = f'{key}="{encoded}"'
        if re.search(rf"(?m)^{re.escape(key)}=", env_text):
            env_text = re.sub(rf"(?m)^{re.escape(key)}=.*$", lambda _: line, env_text)
        else:
            env_text = env_text.rstrip() + "\n" + line + "\n"
    # ProtectSystem=strict exposes the exact /etc/vps-control.env file as
    # writable, not arbitrary sibling paths in /etc. Writing a sibling temp
    # file therefore fails with EROFS inside the API service sandbox.
    ENV_FILE.write_text(env_text, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)


def validate_reality_sni(host: str) -> None:
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="SNI не разрешается через DNS") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(status_code=422, detail="SNI должен указывать только на публичные IP-адреса")
    try:
        result = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:443", "-servername", host, "-brief"],
            input="", capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=422, detail="Не удалось проверить TLS выбранного SNI") from exc
    if result.returncode != 0 or "Protocol version:" not in (result.stdout + result.stderr):
        raise HTTPException(status_code=422, detail="Адрес SNI не завершает корректное TLS-соединение на порту 443")


def persist_vrx_target(host: str) -> None:
    text = VLESS_ENV.read_text(encoding="utf-8")
    target = f"TARGET={host}:443"
    if re.search(r"(?m)^TARGET=", text):
        text = re.sub(r"(?m)^TARGET=.*$", target, text)
    else:
        text = text.rstrip() + "\n" + target + "\n"
    temporary = VLESS_ENV.with_suffix(".env.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(VLESS_ENV)


@app.patch("/api/protocols/{protocol}/settings")
def update_protocol_settings(
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"],
    payload: ProtocolSettingsUpdate,
    _: None = Depends(require_token),
) -> dict:
    supplied = payload.model_dump(exclude_none=True)
    allowed = {
        "wg": {"mtu", "dns", "keepalive"}, "awg": {"mtu", "dns", "keepalive"},
        "shadowsocks": {"timeout", "udp_mtu", "mode", "no_delay", "dns"},
        "vless-reality-xhttp": {"xhttp_mode", "loglevel", "xpadding", "xmux_concurrency", "dns", "sni"},
    }[protocol]
    if not supplied or not set(supplied).issubset(allowed):
        raise HTTPException(status_code=422, detail="Настройки не соответствуют выбранному протоколу")
    if "dns" in supplied:
        resolver_values = [value.strip() for value in supplied["dns"].split(",") if value.strip()]
        if not resolver_values:
            raise HTTPException(status_code=422, detail="Укажите хотя бы один DNS-резолвер")
        for resolver in resolver_values:
            if protocol == "vless-reality-xhttp" and re.fullmatch(r"https://[^\s]+", resolver):
                continue
            try:
                ipaddress.ip_address(resolver)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Некорректный DNS для {protocol}: {resolver}") from exc

    if protocol in ("wg", "awg"):
        config = WG_CONFIG if protocol == "wg" else AWG_CONFIG
        if not config.exists():
            raise HTTPException(status_code=409, detail="Конфигурация туннеля не установлена")
        if "mtu" in supplied:
            persist_tunnel_mtu(protocol, int(supplied["mtu"]))
        prefix = "WG" if protocol == "wg" else "AWG"
        persist_env_values({
            **({f"{prefix}_DNS": supplied["dns"]} if "dns" in supplied else {}),
            **({f"{prefix}_KEEPALIVE": supplied["keepalive"]} if "keepalive" in supplied else {}),
        })
    elif protocol == "shadowsocks":
        if "dns" in supplied:
            persist_env_values({"SHADOWSOCKS_DNS": supplied["dns"]})
        paths = sorted(SHADOWSOCKS_CONFIG_DIR.glob("*.json"))
        if not paths:
            raise HTTPException(status_code=409, detail="Нет настроенных каналов Shadowsocks")
        originals = {path: path.read_bytes() for path in paths}
        try:
            for path in paths:
                config = json.loads(originals[path])
                if "timeout" in supplied:
                    config["timeout"] = supplied["timeout"]
                if "udp_mtu" in supplied:
                    config["mtu"] = supplied["udp_mtu"]
                if "mode" in supplied:
                    config["mode"] = supplied["mode"]
                if "no_delay" in supplied:
                    config["no_delay"] = supplied["no_delay"]
                temporary = path.with_suffix(".tmp.json")
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(temporary, 0o600)
                temporary.replace(path)
            for item in read_clients():
                if item.get("protocol") == protocol:
                    run("systemctl", "restart", f'vps-control-shadowsocks@{item.get("id", "")}.service', timeout=20, check=True)
        except Exception as exc:
            for path, original in originals.items():
                path.write_bytes(original)
                os.chmod(path, 0o600)
            raise HTTPException(status_code=500, detail="Не удалось применить настройки Shadowsocks") from exc
    else:
        if not VLESS_CONFIG.exists():
            raise HTTPException(status_code=409, detail="VRX не установлен")
        original = VLESS_CONFIG.read_bytes()
        env_original = VLESS_ENV.read_bytes() if VLESS_ENV.exists() else None
        temporary = VLESS_CONFIG.with_suffix(".settings.tmp.json")
        try:
            config = json.loads(original)
            if "sni" in supplied:
                validate_reality_sni(supplied["sni"])
                reality_settings = config["inbounds"][0]["streamSettings"]["realitySettings"]
                reality_settings["target"] = f'{supplied["sni"]}:443'
                reality_settings["serverNames"] = [supplied["sni"]]
            if "xhttp_mode" in supplied:
                config["inbounds"][0]["streamSettings"]["xhttpSettings"]["mode"] = supplied["xhttp_mode"]
            if "xpadding" in supplied:
                config["inbounds"][0]["streamSettings"]["xhttpSettings"].setdefault("extra", {})["xPaddingBytes"] = supplied["xpadding"]
            if "xmux_concurrency" in supplied:
                extra = config["inbounds"][0]["streamSettings"]["xhttpSettings"].setdefault("extra", {})
                extra["xmux"] = {
                    "maxConcurrency": str(supplied["xmux_concurrency"]),
                    "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000",
                }
            if "loglevel" in supplied:
                config.setdefault("log", {})["loglevel"] = supplied["loglevel"]
            if "dns" in supplied:
                dns_addresses = [value.strip() for value in supplied["dns"].split(",") if value.strip()]
                config["dns"] = {"servers": dns_addresses, "queryStrategy": "UseIP"}
                config.setdefault("routing", {})["domainStrategy"] = "IPIfNonMatch"
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o640)
            os.chown(temporary, 0, 65534)
            result = subprocess.run([XRAY_BIN, "run", "-test", "-config", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "Xray rejected configuration")
            temporary.replace(VLESS_CONFIG)
            if "sni" in supplied:
                persist_vrx_target(supplied["sni"])
            if "dns" in supplied:
                persist_env_values({"VRX_DNS": supplied["dns"]})
            restart_vless_service()
        except Exception as exc:
            VLESS_CONFIG.write_bytes(original)
            os.chmod(VLESS_CONFIG, 0o640)
            os.chown(VLESS_CONFIG, 0, 65534)
            if env_original is not None:
                VLESS_ENV.write_bytes(env_original)
                os.chmod(VLESS_ENV, 0o600)
            try:
                restart_vless_service()
            except Exception:
                pass
            if isinstance(exc, HTTPException):
                raise exc
            raise HTTPException(status_code=500, detail="Не удалось применить настройки VRX") from exc
        finally:
            temporary.unlink(missing_ok=True)
    return protocol_status(protocol)


@app.get("/api/protocols/{protocol}/status")
def protocol_status(protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"], _: None = Depends(require_token)) -> dict:
    if protocol in ("shadowsocks", "vless-reality-xhttp"):
        unit = "vps-control-shadowsocks.target" if protocol == "shadowsocks" else "vps-control-vless-reality-xhttp.service"
        protocol_clients = [item for item in read_clients() if item.get("protocol") == protocol]
        target = ""
        settings: dict[str, str | int | bool] = {}
        stats = []
        if protocol == "shadowsocks":
            config_values: dict = {}
            for config_path in sorted(SHADOWSOCKS_CONFIG_DIR.glob("*.json")):
                try:
                    config_values = json.loads(config_path.read_text(encoding="utf-8"))
                    break
                except (OSError, json.JSONDecodeError):
                    continue
            settings = {
                "Шифр": str(config_values.get("method", "chacha20-ietf-poly1305")),
                "Режим": str(config_values.get("mode", "tcp_and_udp")),
                "MTU UDP": int(config_values.get("mtu", 1200) or 1200),
                "Таймаут": f"{int(config_values.get('timeout', 300) or 300)} с",
                "TCP no-delay": bool(config_values.get("no_delay", True)),
                "DNS новых профилей": current_env_value("SHADOWSOCKS_DNS", "не настроен"),
            }
            editable_settings = editable_protocol_settings(protocol, config_values)
            for item in protocol_clients:
                rx, tx = service_bytes(f'vps-control-shadowsocks@{item.get("id", "")}.service')
                age, _, _ = stream_sample(f'ss:{item.get("id", "")}', rx, tx)
                connections = shadowsocks_connections(int(item.get("port", 0)))
                stats.append((rx, tx, age if connections else None, connections))
            active_clients = sum(
                1 for _, _, age, connections in stats
                if connections and age is not None and age < STREAM_ACTIVITY_WINDOW_S
            )
            listen_port = SHADOWSOCKS_PORT_START
        else:
            activity = recent_xray_activity()
            stats = [xray_user_stats(f'{item.get("id", "")}@312.net', activity.get(f'{item.get("id", "")}@312.net')) for item in protocol_clients]
            active_clients = sum(1 for _, _, age, _, _ in stats if age is not None and age < STREAM_ACTIVITY_WINDOW_S)
            try:
                reality = dict(line.split("=", 1) for line in VLESS_ENV.read_text(encoding="utf-8").splitlines() if "=" in line)
                listen_port = int(reality.get("PORT", "443"))
                target = reality.get("TARGET", "")
                settings = {
                    "REALITY target": target,
                    "XHTTP path": reality.get("XHTTP_PATH", reality.get("PATH", "/")),
                    "SNI": target.rsplit(":", 1)[0] if ":" in target else target,
                    "DNS": current_env_value("VRX_DNS", "не настроен"),
                }
                config_data = json.loads(VLESS_CONFIG.read_text(encoding="utf-8"))
                xhttp_values = dict(config_data["inbounds"][0]["streamSettings"].get("xhttpSettings", {}))
                xhttp_values["sni"] = target.rsplit(":", 1)[0] if ":" in target else target
                xhttp_values["loglevel"] = config_data.get("log", {}).get("loglevel", "warning")
                xhttp_values["xPaddingBytes"] = xhttp_values.get("extra", {}).get("xPaddingBytes", "100-1000")
                max_concurrency = xhttp_values.get("extra", {}).get("xmux", {}).get("maxConcurrency", "12")
                xhttp_values["xmuxConcurrency"] = int(str(max_concurrency).split("-", 1)[0])
                editable_settings = editable_protocol_settings(protocol, xhttp_values)
            except (OSError, ValueError, json.JSONDecodeError, KeyError, IndexError):
                listen_port = 443
                editable_settings = editable_protocol_settings(protocol, {})
        service_active = run("systemctl", "is-active", unit) == "active"
        return {
            "protocol": protocol, "interface": "systemd", "active": service_active,
            "service_active": service_active, "service_enabled": run("systemctl", "is-enabled", unit) == "enabled",
            "active_since": run("systemctl", "show", unit, "--property=ActiveEnterTimestamp", "--value"),
            "address": PUBLIC_IP, "listen_port": listen_port, "mtu": 0,
            "peers": len(protocol_clients), "online_peers": active_clients, "endpoints": active_clients,
            "last_handshake_age_s": min((row[2] for row in stats if row[2] is not None), default=None),
            "peer_rx_bytes": sum(row[0] for row in stats),
            "peer_tx_bytes": sum(row[1] for row in stats),
            "interface_rx_bytes": sum(row[0] for row in stats), "interface_tx_bytes": sum(row[1] for row in stats), "rx_errors": 0, "tx_errors": 0,
            "rx_dropped": 0, "tx_dropped": 0,
            "unit": unit,
            "transport": "TCP + UDP" if protocol == "shadowsocks" else "XHTTP over TCP",
            "security": "ChaCha20-IETF-Poly1305" if protocol == "shadowsocks" else "REALITY",
            "target": target,
            "settings": settings,
            "editable_settings": editable_settings,
        }
    command = "wg" if protocol == "wg" else "awg"
    interface = WG_INTERFACE if protocol == "wg" else AWG_INTERFACE
    unit = f"{'wg-quick' if protocol == 'wg' else 'awg-quick'}@{interface}.service"
    dump = run(command, "show", interface, "dump")
    rows = dump.splitlines()
    now = int(time.time())
    handshakes: list[int] = []
    peer_rx = peer_tx = 0
    endpoints = 0
    for row in rows[1:]:
        columns = row.split("\t")
        if len(columns) < 8:
            continue
        if columns[2]:
            endpoints += 1
        handshake = int(columns[4] or 0)
        if handshake:
            handshakes.append(now - handshake)
        peer_rx += int(columns[5] or 0)
        peer_tx += int(columns[6] or 0)

    stats_dir = Path("/sys/class/net") / interface / "statistics"
    def stat(name: str) -> int:
        try:
            return int((stats_dir / name).read_text().strip())
        except (OSError, ValueError):
            return 0

    address = ""
    mtu = 0
    try:
        link_data = json.loads(run("ip", "-j", "address", "show", "dev", interface) or "[]")
        if link_data:
            mtu = int(link_data[0].get("mtu", 0))
            ipv4 = next((item for item in link_data[0].get("addr_info", []) if item.get("family") == "inet"), None)
            if ipv4:
                address = f"{ipv4.get('local')}/{ipv4.get('prefixlen')}"
    except (json.JSONDecodeError, ValueError):
        pass

    listen_port = int(rows[0].split("\t")[2]) if rows and len(rows[0].split("\t")) >= 3 else 0
    history = protocol_history(protocol)
    return {
        "protocol": protocol,
        "interface": interface,
        "active": bool(dump),
        "service_active": run("systemctl", "is-active", unit) == "active",
        "service_enabled": run("systemctl", "is-enabled", unit) == "enabled",
        "active_since": run("systemctl", "show", unit, "--property=ActiveEnterTimestamp", "--value"),
        "address": address,
        "listen_port": listen_port,
        "mtu": mtu,
        "settings": {"MTU": mtu},
        "editable_settings": editable_protocol_settings(protocol, {"mtu": mtu}),
        "peers": len(rows) - 1 if rows else 0,
        "online_peers": sum(1 for age in handshakes if age < 180),
        "endpoints": endpoints,
        "last_handshake_age_s": min(handshakes) if handshakes else None,
        "peer_rx_bytes": peer_rx,
        "peer_tx_bytes": peer_tx,
        "interface_rx_bytes": stat("rx_bytes"),
        "interface_tx_bytes": stat("tx_bytes"),
        "rx_errors": stat("rx_errors"),
        "tx_errors": stat("tx_errors"),
        "rx_dropped": stat("rx_dropped"),
        "tx_dropped": stat("tx_dropped"),
        "resources": cached_resource_availability(protocol),
        "history": history,
        "diagnostics": cached_network_diagnostics(protocol),
      }


@app.post("/api/protocols/{protocol}/resources/check")
def check_protocol_resources(
    protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"],
    _: None = Depends(require_token),
) -> dict:
    return check_resource_availability(protocol)


@app.post("/api/protocols/{protocol}/diagnostics/check")
def check_network_diagnostics(protocol: Literal["wg", "awg"], _: None = Depends(require_token)) -> dict:
    return network_diagnostics(protocol, protocol_history(protocol), force=True)


@app.post("/api/protocols/{protocol}/restart")
def restart_protocol(protocol: Literal["wg", "awg", "shadowsocks", "vless-reality-xhttp"], _: None = Depends(require_token)) -> dict:
    if protocol == "shadowsocks":
        unit = "vps-control-shadowsocks.target"
        if run("systemctl", "show", unit, "--property=LoadState", "--value") != "loaded":
            raise HTTPException(status_code=409, detail="Shadowsocks protocol is not installed")
        for item in read_clients():
            if item.get("protocol") == protocol:
                run("systemctl", "restart", f'vps-control-shadowsocks@{item.get("id", "")}.service', timeout=20, check=True)
    else:
        unit = (
            f"wg-quick@{WG_INTERFACE}.service" if protocol == "wg"
            else f"awg-quick@{AWG_INTERFACE}.service" if protocol == "awg"
            else "vps-control-vless-reality-xhttp.service"
        )
        if run("systemctl", "show", unit, "--property=LoadState", "--value") != "loaded":
            raise HTTPException(status_code=409, detail=f"{protocol} protocol is not installed")
        run("systemctl", "restart", unit, timeout=20, check=True)
    return {"protocol": protocol, "active": run("systemctl", "is-active", unit) == "active"}
