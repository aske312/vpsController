from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import ipaddress
import json
import csv
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import platform
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics_history import MetricsHistory, MetricsMonitor
from system_metrics import CpuSampler, collect_resources


@asynccontextmanager
async def lifespan(_: FastAPI):
    global metrics_monitor
    if platform.system() == "Linux":
        metrics_monitor = MetricsMonitor(metrics_history_store, collect_system_resources)
        metrics_monitor.start()
    try:
        yield
    finally:
        if metrics_monitor is not None:
            metrics_monitor.stop()
            metrics_monitor = None


app = FastAPI(title="312.net Infrastructure API", version="0.1.0", lifespan=lifespan)
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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
AWG_INTERFACE = os.getenv("AWG_INTERFACE", "awg0")
AWG_PORT = int(os.getenv("AWG_PORT", "51822"))
AWG_SUBNET = ipaddress.ip_network(os.getenv("AWG_SUBNET", "10.73.0.0/24"))
AWG_CONFIG = Path(os.getenv("AWG_CONFIG", f"/etc/amnezia/amneziawg/{AWG_INTERFACE}.conf"))
AWG_MTU = int(os.getenv("AWG_MTU", "1280"))
AWG_PROFILE = {
    "Jc": os.getenv("AWG_JC", "6"), "Jmin": os.getenv("AWG_JMIN", "8"), "Jmax": os.getenv("AWG_JMAX", "80"),
    "S1": os.getenv("AWG_S1", "64"), "S2": os.getenv("AWG_S2", "112"),
    "H1": os.getenv("AWG_H1", "150000000"), "H2": os.getenv("AWG_H2", "600000000"),
    "H3": os.getenv("AWG_H3", "1000000000"), "H4": os.getenv("AWG_H4", "1400000000"),
}
DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/vps-control"))
metrics_history_store = MetricsHistory(DATA_DIR / "metrics" / "history.sqlite3")
metrics_monitor: MetricsMonitor | None = None
ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/vps-control.env"))
CLIENTS_FILE = DATA_DIR / "clients.json"
ACTION_FILE = DATA_DIR / "application-action.json"
AUTOMATION_FILE = DATA_DIR / "automation.json"
PROTOCOL_VERSIONS_FILE = DATA_DIR / "protocol-versions.json"
SERVICE_MODE_FILE = DATA_DIR / "service-mode.json"
UPDATES_FILE = DATA_DIR / "security-updates.json"
APP_VERSION_FILE = DATA_DIR / "application-version.json"
REGIONAL_PROBES_FILE = DATA_DIR / "regional-probes.json"
LOGGING_CONFIG_FILE = Path("/etc/vps-control-logging.conf")
INSTALL_DIR = Path(os.getenv("INSTALL_DIR", "/opt/vps-control"))
CONTROL_COMMAND = os.getenv("CONTROL_COMMAND", "/usr/local/sbin/vps-control")
PROTOCOL_IMAGES_DIR = INSTALL_DIR / "protocol-images"
HYSTERIA2_DIR = Path("/etc/vps-control/hysteria2")
HYSTERIA2_SETTINGS = HYSTERIA2_DIR / "settings.json"
HYSTERIA2_USERS = HYSTERIA2_DIR / "users.json"
TUIC_DIR = Path("/etc/vps-control/tuic")
TUIC_SETTINGS = TUIC_DIR / "settings.json"
TUIC_CONFIG = TUIC_DIR / "config.json"
XRAY_DIR = Path("/etc/vps-control/xray")
XRAY_SETTINGS = XRAY_DIR / "settings.json"
XRAY_CONFIG = XRAY_DIR / "config.json"
MONITOR_DIR = DATA_DIR / "monitor"
updates_refresh_lock = threading.Lock()
app_version_refresh_lock = threading.Lock()
resource_check_lock = threading.Lock()
action_start_lock = threading.Lock()
network_diagnostic_lock = threading.Lock()
connection_probe_lock = threading.Lock()
resource_check_cache: dict[str, dict] = {}
network_diagnostic_cache: dict[str, dict] = {}
connection_probe_cache: dict[str, dict] = {}
client_quality_cache: dict[str, dict] = {}
client_mutation_lock = threading.Lock()
protocol_version_lock = threading.Lock()
regional_probe_lock = threading.Lock()
protocol_version_cache: dict[str, dict] = {}
DIRECT_PROTOCOLS = ("hysteria2", "tuic", "xray")
DIAGNOSTIC_CLIENT_PREFIX = "__vps_control_probe__"
MODULE_ORDER = {module_id: index for index, module_id in enumerate(("awg", "tuic", "hysteria2", "xray", "relay-agent"))}
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


def write_action_file(action: dict) -> None:
    """Atomically publish an action without exposing partial JSON to readers."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = ACTION_FILE.with_name(f".{ACTION_FILE.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(action, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(ACTION_FILE)


def start_application_task(
    unit_prefix: str,
    action_name: str,
    command: list[str],
    message: str,
    error_detail: str,
    runtime_max_seconds: int | None = None,
) -> dict:
    """Reserve the shared action slot before systemd can start a fast task."""
    with action_start_lock:
        if ACTION_FILE.exists():
            try:
                previous_unit = json.loads(ACTION_FILE.read_text(encoding="utf-8")).get("unit", "")
                if previous_unit and run("systemctl", "is-active", previous_unit) in ("active", "activating"):
                    raise HTTPException(status_code=409, detail="Another application action is already running")
            except (json.JSONDecodeError, OSError):
                pass

        unit = f"{unit_prefix}-{time.time_ns()}"
        action = {
            "unit": f"{unit}.service",
            "action": action_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "state": "activating",
            "progress": 3,
            "message": message,
        }
        write_action_file(action)
        systemd_command = ["systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec"]
        if runtime_max_seconds:
            systemd_command.append(f"--property=RuntimeMaxSec={runtime_max_seconds}")
        try:
            result = subprocess.run(
                [*systemd_command, *command],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as cause:
            action.update(state="failed", result="failed", message=error_detail)
            write_action_file(action)
            status_code = 504 if isinstance(cause, subprocess.TimeoutExpired) else 500
            raise HTTPException(status_code=status_code, detail=error_detail) from cause
        if result.returncode:
            detail = result.stderr.strip() or error_detail
            action.update(state="failed", result="failed", message=detail)
            write_action_file(action)
            raise HTTPException(status_code=500, detail=detail)
        return action


def run_with_input(args: list[str], value: str) -> str:
    try:
        result = subprocess.run(args, input=value, capture_output=True, text=True, timeout=8, check=False)
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail=f"{args[0]} is not installed")
    if result.returncode:
        raise HTTPException(status_code=500, detail=result.stderr.strip() or f"{args[0]} failed")
    return result.stdout.strip()


def cached_resource_availability(protocol: str) -> dict:
    cached = resource_check_cache.get(protocol)
    return {key: value for key, value in (cached or {}).items() if not key.startswith("_")}


def check_resource_availability(protocol: str) -> dict:
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


def direct_client_rows() -> list[dict]:
    rows = []
    for item in read_clients():
        if item.get("protocol") not in DIRECT_PROTOCOLS or item.get("diagnostic"):
            continue
        rows.append({**item, "address": item.get("endpoint") or PUBLIC_IP,
                     "endpoint": item.get("endpoint") or PUBLIC_IP, "rx_bytes": 0, "tx_bytes": 0,
                     "handshake_age_s": None, "quality": "offline",
                     "quality_reason": "Учётная запись готова; активность определяется службой протокола"})
    return rows


def diagnostic_client_id(protocol: str) -> str:
    return f"{DIAGNOSTIC_CLIENT_PREFIX}-{protocol}"


def is_diagnostic_identity(protocol: str, identity: dict | str) -> bool:
    expected = diagnostic_client_id(protocol)
    if isinstance(identity, str):
        return identity == expected
    return identity.get("name") == expected or identity.get("email") == f"{expected}@312.net"


def record_diagnostic_client(protocol: str, public_key: str) -> None:
    client_id = diagnostic_client_id(protocol)
    items = read_clients()
    if any(item.get("id") == client_id for item in items):
        return
    items.append({
        "id": client_id,
        "name": "Служебная проверка протокола",
        "protocol": protocol,
        "public_key": public_key,
        "endpoint": "127.0.0.1",
        "diagnostic": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    write_clients(items)


def certificate_server_name(path: Path) -> str:
    extensions = run("openssl", "x509", "-in", str(path), "-noout", "-ext", "subjectAltName", check=True)
    match = re.search(r"DNS:([A-Za-z0-9.-]+)", extensions)
    if not match:
        raise HTTPException(status_code=409, detail="Server certificate has no DNS identity")
    return match.group(1)


def service_bytes(unit: str) -> tuple[int, int]:
    def value(name: str) -> int:
        try:
            return int(run("systemctl", "show", unit, "--property=" + name, "--value") or 0)
        except ValueError:
            return 0
    return value("IPIngressBytes"), value("IPEgressBytes")


def interface_dump(include_quality: bool = True) -> list[dict]:
    protocol = "awg"
    command = "awg"
    interface = AWG_INTERFACE
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
                "update_state": meta.get("update_state"),
                "update_message": meta.get("update_message"),
        })
    online_peers = [peer for peer in peers if peer["handshake_age_s"] is not None and peer["handshake_age_s"] < 180]
    if include_quality and online_peers:
        with ThreadPoolExecutor(max_workers=min(6, len(online_peers))) as pool:
            qualities = dict(pool.map(lambda peer: (peer["id"], client_connection_quality(peer)), online_peers))
        for peer in online_peers:
            peer.update(qualities[peer["id"]])
    for peer in peers:
        if include_quality and peer not in online_peers:
            peer.update({"quality": "offline", "latency_ms": None, "packet_loss_percent": None, "quality_reason": "Нет активного handshake"})
    return peers


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
        # A fresh AmneziaWG handshake proves the tunnel is active. Some clients
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
        "quality_reason": reason, "_cached_at": time.time(),
    }
    client_quality_cache[cache_key] = result
    return {key: value for key, value in result.items() if key != "_cached_at"}


def protocol_history(protocol: str, period_hours: int = 24) -> dict:
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


def network_diagnostics(protocol: str, history: dict, force: bool = False) -> dict:
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
        interface = AWG_INTERFACE
        port = AWG_PORT
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
        unit = f"awg-quick@{interface}.service"
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
        tunnel_budget = max(0, (uplink_mtu or 1500) - 80)
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
            finding("warning", "jitter", "Высокий jitter", f"Сейчас {live_jitter or 0:.1f} мс, за 24 часа {historical_jitter or 0:.1f} мс.", "Проверить загрузку канала, очереди и регион размещения VPS.")
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


def cached_network_diagnostics(protocol: str) -> dict:
    cached = network_diagnostic_cache.get(protocol)
    return {
        key: value for key, value in (cached or {
            "checked_at": None, "status": "pending", "score": None, "checks": [], "findings": [],
        }).items() if key != "_cached_at"
    }


def protocol_listener(protocol: str) -> tuple[str, int, str, bool]:
    unit, port, transport = {
        "hysteria2": ("vps-control-hysteria2.service", 8443, "udp"),
        "tuic": ("vps-control-tuic.service", 8444, "udp"),
        "xray": ("vps-control-xray.service", 8445, "tcp"),
    }[protocol]
    settings_path = {
        "hysteria2": HYSTERIA2_SETTINGS,
        "tuic": TUIC_SETTINGS,
        "xray": XRAY_SETTINGS,
    }[protocol]
    try:
        port = int(json.loads(settings_path.read_text(encoding="utf-8")).get("port", port))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    listeners = run("ss", "-H", "-ln" + ("u" if transport == "udp" else "t"))
    listening = any(re.search(rf"(?:^|[:.]){port}(?:\s|$)", line) for line in listeners.splitlines())
    return unit, port, transport, listening


def protocol_runtime_profile(protocol: str) -> dict:
    if protocol == "awg":
        return {
            "kind": "encrypted-tunnel",
            "summary": "Сетевой L3-туннель AmneziaWG с обфускацией WireGuard-трафика.",
            "facts": [
                {"label": "Транспорт", "value": "AmneziaWG / UDP"},
                {"label": "Проверка клиента", "value": "Handshake + RX/TX"},
                {"label": "MTU", "value": str(AWG_MTU)},
                {"label": "Обфускация", "value": f"Jc {AWG_PROFILE['Jc']} · Jmin/Jmax {AWG_PROFILE['Jmin']}/{AWG_PROFILE['Jmax']}"},
            ],
        }

    unit, port, transport, listening = protocol_listener(protocol)
    settings_path = {
        "hysteria2": HYSTERIA2_SETTINGS,
        "tuic": TUIC_SETTINGS,
        "xray": XRAY_SETTINGS,
    }[protocol]
    config_path = {"tuic": TUIC_CONFIG, "xray": XRAY_CONFIG}.get(protocol)
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}

    accounts = 0
    diagnostic_ready = False
    if protocol == "hysteria2":
        try:
            users = json.loads(HYSTERIA2_USERS.read_text(encoding="utf-8"))
            if isinstance(users, dict):
                diagnostic_ready = any(is_diagnostic_identity(protocol, identity) for identity in users)
                accounts = sum(not is_diagnostic_identity(protocol, identity) for identity in users)
        except (OSError, json.JSONDecodeError):
            pass
        identity = str(settings.get("domain", "")).strip()
        if not identity:
            try:
                identity = certificate_server_name(HYSTERIA2_DIR / "server.crt")
            except HTTPException:
                identity = "не определено"
        facts = [
            {"label": "Транспорт", "value": "Hysteria2 / QUIC / UDP"},
            {"label": "TLS identity", "value": identity},
            {"label": "Аутентификация", "value": "HTTP auth"},
            {"label": "Учётные записи", "value": str(accounts)},
            {"label": "Диагностический доступ", "value": "готов" if diagnostic_ready else "будет создан при проверке"},
        ]
        summary = "QUIC-прокси с TLS и отдельной HTTP-аутентификацией клиентов."
    else:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8")) if config_path else {}
        except (OSError, json.JSONDecodeError):
            config = {}
        if protocol == "tuic":
            inbound = next((row for row in config.get("inbounds", []) if row.get("type") == "tuic"), {})
            users = inbound.get("users", [])
            diagnostic_ready = any(is_diagnostic_identity(protocol, identity) for identity in users)
            accounts = sum(not is_diagnostic_identity(protocol, identity) for identity in users)
            tls = inbound.get("tls", {})
            facts = [
                {"label": "Транспорт", "value": "TUIC v5 / QUIC / UDP"},
                {"label": "TLS identity", "value": str(tls.get("server_name", "endpoint.internal"))},
                {"label": "Congestion control", "value": str(settings.get("congestion_control", "bbr"))},
                {"label": "Heartbeat", "value": str(settings.get("heartbeat", "10s"))},
                {"label": "Учётные записи", "value": str(accounts)},
                {"label": "Диагностический доступ", "value": "готов" if diagnostic_ready else "будет создан при проверке"},
            ]
            summary = "QUIC-прокси TUIC v5 с TLS и индивидуальной UUID/password-аутентификацией."
        else:
            inbound = next((row for row in config.get("inbounds", []) if row.get("protocol") == "vless"), {})
            users = inbound.get("settings", {}).get("clients", [])
            diagnostic_ready = any(is_diagnostic_identity(protocol, identity) for identity in users)
            accounts = sum(not is_diagnostic_identity(protocol, identity) for identity in users)
            facts = [
                {"label": "Протокол", "value": "VLESS"},
                {"label": "Транспорт", "value": "XHTTP / TCP"},
                {"label": "Защита", "value": "REALITY"},
                {"label": "Server name", "value": str(settings.get("server_name", "не определено"))},
                {"label": "Reality target", "value": str(settings.get("target", "не определено"))},
                {"label": "Учётные записи", "value": str(accounts)},
                {"label": "Диагностический доступ", "value": "готов" if diagnostic_ready else "будет создан при проверке"},
            ]
            summary = "VLESS поверх XHTTP/TCP с транспортной защитой REALITY."
    return {
        "kind": "proxy",
        "summary": summary,
        "accounts": accounts,
        "diagnostic_ready": diagnostic_ready,
        "listener": {"unit": unit, "port": port, "transport": transport, "listening": listening},
        "facts": facts,
    }


def observed_tunnel_connection(protocol: Literal["awg"]) -> dict:
    peers = interface_dump(include_quality=False)
    fresh = [peer for peer in peers if peer.get("handshake_age_s") is not None and peer["handshake_age_s"] < 180]
    exchanged = [peer for peer in fresh if peer.get("rx_bytes", 0) > 0 and peer.get("tx_bytes", 0) > 0]
    checked_at = datetime.now(timezone.utc).isoformat()
    if exchanged:
        newest = min(exchanged, key=lambda peer: peer["handshake_age_s"])
        return {
            "checked_at": checked_at,
            "state": "confirmed",
            "method": "observed-client-traffic",
            "title": "Подключение подтверждено реальным клиентом",
            "detail": f"Свежий handshake {newest['handshake_age_s']} сек назад; через туннель передавались данные в обе стороны.",
            "latency_ms": None,
            "bytes_received": newest.get("rx_bytes", 0),
            "bytes_sent": newest.get("tx_bytes", 0),
            "identity": "registered-client",
            "scope": "Подтверждает handshake и трафик зарегистрированного клиента; не создаёт тестовый peer и не использует ICMP.",
        }
    if fresh:
        newest = min(fresh, key=lambda peer: peer["handshake_age_s"])
        detail = f"Handshake свежий ({newest['handshake_age_s']} сек), но двусторонняя передача полезных данных не подтверждена."
    elif peers:
        detail = "Есть зарегистрированные peers, но свежего handshake нет. Подключите клиент и повторите проверку."
    else:
        detail = "Нет зарегистрированных клиентов, поэтому проверить реальный путь подключения невозможно."
    return {
        "checked_at": checked_at,
        "state": "unverified",
        "method": "observed-client-traffic",
        "title": "Подключение не проверено",
        "detail": detail,
        "latency_ms": None,
        "bytes_received": 0,
        "bytes_sent": 0,
        "identity": "registered-client",
        "scope": f"{protocol.upper()} подтверждается только свежим handshake и реальным двусторонним трафиком клиента.",
    }


def cached_connection_probe(protocol: str) -> dict:
    if protocol == "awg":
        return observed_tunnel_connection(protocol)
    return connection_probe_cache.get(protocol, {
        "checked_at": None,
        "state": "unverified",
        "method": "local-protocol-roundtrip",
        "title": "Передача данных ещё не проверялась",
        "detail": "Запустите проверку, чтобы выполнить настоящий handshake и запрос-ответ через протокол.",
        "latency_ms": None,
        "bytes_received": 0,
        "bytes_sent": 0,
        "identity": "managed-diagnostic",
        "scope": "Локальный loopback probe подтверждает протокол и передачу данных, но не внешний firewall и сеть устройства.",
    })


def read_regional_probe(protocol: str) -> dict | None:
    try:
        reports = json.loads(REGIONAL_PROBES_FILE.read_text(encoding="utf-8"))
        report = reports.get(protocol)
        checked_at = datetime.fromisoformat(str(report.get("checked_at", "")))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - checked_at > timedelta(hours=24):
            return None
        if report.get("state") not in ("confirmed", "failed") or report.get("region") != "RU":
            return None
        return report
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        return None


def regional_reachability(protocol: str, connection: dict) -> dict:
    report = read_regional_probe(protocol)
    if report:
        confirmed = report["state"] == "confirmed"
        return {
            **report,
            "method": "external-regional-probe",
            "title": "Доступность из РФ подтверждена" if confirmed else "Протокол недоступен из РФ",
            "detail": (
                "Внешний probe-агент в российской сети выполнил handshake и получил корректный ответ через протокол."
                if confirmed else
                "Внешний probe-агент в российской сети не смог получить корректный ответ через протокол."
            ),
        }
    if connection.get("method") == "observed-client-traffic" and connection.get("state") == "confirmed":
        detail = "Есть реальный handshake и двусторонний трафик, но приложение не определяет страну сети клиента. Результат нельзя честно приписать маршруту из РФ."
    else:
        detail = "Серверная проверка не проходит через российскую сеть. Для точного результата нужен внешний probe-агент или реальный клиент в РФ."
    return {
        "checked_at": None,
        "state": "unverified",
        "region": "RU",
        "method": "external-regional-probe",
        "title": "Доступность из РФ не подтверждена",
        "detail": detail,
    }


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def ensure_direct_probe_identity(protocol: str) -> bool:
    """Create one reserved, non-user identity when a direct protocol needs a probe."""
    client_id = diagnostic_client_id(protocol)
    with client_mutation_lock:
        if protocol == "hysteria2":
            original = HYSTERIA2_USERS.read_bytes() if HYSTERIA2_USERS.exists() else None
            users = json.loads(HYSTERIA2_USERS.read_text(encoding="utf-8")) if HYSTERIA2_USERS.exists() else {}
            if not isinstance(users, dict):
                raise ValueError("Hysteria2 users file is invalid")
            created = client_id not in users
            temporary = HYSTERIA2_USERS.with_suffix(".probe.tmp")
            try:
                if created:
                    users[client_id] = secrets.token_urlsafe(32)
                    temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
                    os.chmod(temporary, 0o600)
                    temporary.replace(HYSTERIA2_USERS)
                record_diagnostic_client(protocol, client_id)
                return created
            except Exception:
                if created:
                    if original is None:
                        HYSTERIA2_USERS.unlink(missing_ok=True)
                    else:
                        HYSTERIA2_USERS.write_bytes(original)
                        os.chmod(HYSTERIA2_USERS, 0o600)
                raise
            finally:
                temporary.unlink(missing_ok=True)

        if protocol == "tuic":
            binary = Path("/usr/local/lib/vps-control-tuic/sing-box")
            unit = "vps-control-tuic.service"
            config_path = TUIC_CONFIG
            original = config_path.read_bytes()
            config = json.loads(original)
            inbound = next(row for row in config.get("inbounds", []) if row.get("type") == "tuic")
            existing = next((user for user in inbound.get("users", []) if is_diagnostic_identity(protocol, user)), None)
            if existing:
                record_diagnostic_client(protocol, str(existing.get("uuid", client_id)))
                return False
            user = {"name": client_id, "password": secrets.token_urlsafe(32), "uuid": str(uuid.uuid4())}
            inbound.setdefault("users", []).append(user)
            temporary = config_path.with_suffix(".probe.tmp.json")
            try:
                temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(temporary, 0o600)
                validation = subprocess.run(
                    [str(binary), "check", "-c", str(temporary)],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if validation.returncode:
                    raise RuntimeError(validation.stderr.strip() or "sing-box rejected diagnostic identity")
                temporary.replace(config_path)
                run("systemctl", "restart", unit, timeout=20, check=True)
                record_diagnostic_client(protocol, user["uuid"])
                return True
            except Exception:
                config_path.write_bytes(original)
                os.chmod(config_path, 0o600)
                run("systemctl", "restart", unit, timeout=20)
                raise
            finally:
                temporary.unlink(missing_ok=True)

        binary = Path("/usr/local/lib/vps-control-xray/xray")
        unit = "vps-control-xray.service"
        original = XRAY_CONFIG.read_bytes()
        config = json.loads(original)
        inbound = next(row for row in config.get("inbounds", []) if row.get("protocol") == "vless")
        users = inbound.setdefault("settings", {}).setdefault("clients", [])
        existing = next((user for user in users if is_diagnostic_identity(protocol, user)), None)
        if existing:
            record_diagnostic_client(protocol, str(existing.get("id", client_id)))
            return False
        user_uuid = str(uuid.uuid4())
        users.append({"id": user_uuid, "email": f"{client_id}@312.net"})
        temporary = XRAY_CONFIG.with_suffix(".probe.tmp.json")
        try:
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temporary, 0o600)
            validation = subprocess.run(
                [str(binary), "run", "-test", "-config", str(temporary)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if validation.returncode:
                raise RuntimeError(validation.stderr.strip() or "Xray rejected diagnostic identity")
            temporary.replace(XRAY_CONFIG)
            run("systemctl", "restart", unit, timeout=20, check=True)
            record_diagnostic_client(protocol, user_uuid)
            return True
        except Exception:
            XRAY_CONFIG.write_bytes(original)
            os.chmod(XRAY_CONFIG, 0o600)
            run("systemctl", "restart", unit, timeout=20)
            raise
        finally:
            temporary.unlink(missing_ok=True)


def direct_probe_client(protocol: str, proxy_port: int, directory: Path) -> list[str] | None:
    if protocol == "hysteria2":
        try:
            settings = json.loads(HYSTERIA2_SETTINGS.read_text(encoding="utf-8"))
            users = json.loads(HYSTERIA2_USERS.read_text(encoding="utf-8"))
            client_id, password = next(
                ((key, value) for key, value in users.items() if is_diagnostic_identity(protocol, key)),
                next(iter(users.items())),
            )
            identity = str(settings.get("domain", "")).strip() or certificate_server_name(HYSTERIA2_DIR / "server.crt")
            fingerprint = run("openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(HYSTERIA2_DIR / "server.crt"), check=True).partition("=")[2].strip()
        except (AttributeError, OSError, ValueError, StopIteration, json.JSONDecodeError, HTTPException):
            return None
        config = directory / "hysteria2.yaml"
        config.write_text(
            "\n".join([
                f"server: 127.0.0.1:{int(settings.get('port', 8443))}",
                f"auth: {client_id}:{password}",
                "tls:", f"  sni: {identity}", "  insecure: true", f"  pinSHA256: {fingerprint}",
                "socks5:", f"  listen: 127.0.0.1:{proxy_port}", "  disableUDP: false", "",
            ]),
            encoding="utf-8",
        )
        os.chmod(config, 0o600)
        return ["/usr/local/lib/vps-control-hysteria2/hysteria", "client", "-c", str(config)]

    if protocol == "tuic":
        try:
            settings = json.loads(TUIC_SETTINGS.read_text(encoding="utf-8"))
            server = json.loads(TUIC_CONFIG.read_text(encoding="utf-8"))
            inbound = next(row for row in server.get("inbounds", []) if row.get("type") == "tuic")
            user = next(
                (row for row in inbound.get("users", []) if is_diagnostic_identity(protocol, row)),
                next(iter(inbound.get("users", []))),
            )
            certificate = (TUIC_DIR / "server.crt").read_text(encoding="utf-8")
            identity = certificate_server_name(TUIC_DIR / "server.crt")
        except (OSError, StopIteration, json.JSONDecodeError, HTTPException):
            return None
        outbound = {
            "type": "tuic", "tag": "probe-out", "server": "127.0.0.1",
            "server_port": int(settings.get("port", 8444)), "uuid": user.get("uuid"),
            "password": user.get("password"), "congestion_control": str(settings.get("congestion_control", "bbr")),
            "udp_relay_mode": "native", "zero_rtt_handshake": False,
            "heartbeat": str(settings.get("heartbeat", "10s")),
            "tls": {"enabled": True, "server_name": identity, "certificate": certificate},
        }
        payload = {
            "log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "probe-in", "listen": "127.0.0.1", "listen_port": proxy_port}],
            "outbounds": [outbound], "route": {"final": "probe-out"},
        }
        config = directory / "tuic.json"
        config.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(config, 0o600)
        return ["/usr/local/lib/vps-control-tuic/sing-box", "run", "-c", str(config)]

    try:
        settings = json.loads(XRAY_SETTINGS.read_text(encoding="utf-8"))
        server = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
        inbound = next(row for row in server.get("inbounds", []) if row.get("protocol") == "vless")
        users = inbound.get("settings", {}).get("clients", [])
        user = next((row for row in users if is_diagnostic_identity(protocol, row)), next(iter(users)))
    except (OSError, StopIteration, json.JSONDecodeError):
        return None
    path = str(settings.get("path", "/xhttp"))
    payload = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": proxy_port, "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [{
            "tag": "probe-out", "protocol": "vless",
            "settings": {"address": "127.0.0.1", "port": int(settings.get("port", 8445)), "id": user.get("id"), "encryption": "none"},
            "streamSettings": {
                "network": "xhttp", "security": "reality", "xhttpSettings": {"path": path},
                "realitySettings": {
                    "serverName": str(settings.get("server_name", "www.microsoft.com")),
                    "fingerprint": "chrome", "password": str(settings.get("password", "")),
                    "shortId": str(settings.get("short_id", "")), "spiderX": path,
                },
            },
        }],
    }
    config = directory / "xray.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(config, 0o600)
    return ["/usr/local/lib/vps-control-xray/xray", "run", "-config", str(config)]


def wait_for_proxy(process: subprocess.Popen, port: int, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def check_protocol_connection(protocol: str) -> dict:
    if protocol == "awg":
        return observed_tunnel_connection(protocol)
    if not connection_probe_lock.acquire(blocking=False):
        current = cached_connection_probe(protocol).copy()
        current.update(state="unverified", title="Проверка уже выполняется", detail="Дождитесь завершения текущего protocol probe.")
        return current
    checked_at = datetime.now(timezone.utc).isoformat()
    result = {
        "checked_at": checked_at,
        "state": "unverified",
        "method": "local-protocol-roundtrip",
        "title": "Проверка не выполнена",
        "detail": "Нет подходящей учётной записи или клиентского бинарника для безопасного probe.",
        "latency_ms": None,
        "bytes_received": 0,
        "bytes_sent": 0,
        "identity": "managed-diagnostic",
        "scope": "Локальный loopback probe подтверждает протокол и передачу данных, но не внешний firewall и сеть устройства.",
    }
    process = None
    try:
        unit, _, _, listening = protocol_listener(protocol)
        if run("systemctl", "is-active", unit) != "active" or not listening:
            result.update(state="failed", title="Протокол не принимает подключения", detail="Служба остановлена или listener не найден.")
            connection_probe_cache[protocol] = result
            return result
        try:
            ensure_direct_probe_identity(protocol)
        except (OSError, ValueError, StopIteration, json.JSONDecodeError, RuntimeError, subprocess.TimeoutExpired, HTTPException) as exc:
            result.update(
                state="failed",
                title="Диагностический доступ не подготовлен",
                detail=f"Не удалось безопасно создать служебную identity: {type(exc).__name__}.",
            )
            connection_probe_cache[protocol] = result
            return result
        proxy_port = free_loopback_port()
        with tempfile.TemporaryDirectory(prefix=f"vps-control-{protocol}-probe-") as temporary:
            command = direct_probe_client(protocol, proxy_port, Path(temporary))
            if not command or not Path(command[0]).exists():
                connection_probe_cache[protocol] = result
                return result
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not wait_for_proxy(process, proxy_port):
                result.update(state="failed", title="Протокольный handshake не выполнен", detail="Временный клиент не открыл локальный SOCKS после подключения к серверу.")
                connection_probe_cache[protocol] = result
                return result
            response_file = Path(temporary) / "health-response.json"
            started = time.monotonic()
            response = subprocess.run(
                [
                    "curl", "--silent", "--show-error", "--output", str(response_file),
                    "--write-out", "%{http_code} %{size_request} %{size_download}", "--socks5-hostname", f"127.0.0.1:{proxy_port}",
                    "--connect-timeout", "3", "--max-time", "8", "http://127.0.0.1:8000/api/health",
                ],
                capture_output=True, text=True, timeout=10, check=False,
            )
            latency_ms = round((time.monotonic() - started) * 1000)
            body = response_file.read_bytes() if response_file.exists() else b""
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            response_metrics = response.stdout.strip().split()
            status_code = response_metrics[0] if response_metrics else ""
            try:
                bytes_sent = round(float(response_metrics[1]))
                bytes_received = round(float(response_metrics[2]))
            except (IndexError, ValueError):
                bytes_sent, bytes_received = 0, len(body)
            health_contract_valid = payload.get("ok") is True or payload.get("status") == "ok"
            if response.returncode == 0 and status_code == "200" and health_contract_valid:
                result.update(
                    state="confirmed", title="Handshake и передача данных подтверждены",
                    detail="Временный клиент получил корректный ответ API через SOCKS и серверный outbound протокола.",
                    latency_ms=latency_ms, bytes_received=bytes_received, bytes_sent=bytes_sent,
                )
            else:
                result.update(
                    state="failed", title="Сквозной ответ через протокол не получен",
                    detail=f"Клиент запустился, но health request не вернул корректный ответ через протокол (HTTP {status_code or 'нет ответа'}, curl {response.returncode}).",
                    latency_ms=latency_ms, bytes_received=bytes_received, bytes_sent=bytes_sent,
                )
            connection_probe_cache[protocol] = result
            return result
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result.update(state="failed", title="Проверка передачи данных завершилась ошибкой", detail=type(exc).__name__)
        connection_probe_cache[protocol] = result
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        connection_probe_lock.release()


def direct_protocol_diagnostics(protocol: str) -> dict:
    unit, port, transport, listening = protocol_listener(protocol)
    active = run("systemctl", "is-active", unit) == "active"
    connection = cached_connection_probe(protocol)
    checks = [
        {"id": "service", "name": "Служба протокола", "state": "passed" if active else "failed", "ok": active, "value": "работает" if active else "остановлена"},
        {"id": "listener", "name": f"{transport.upper()} listener", "state": "passed" if listening else "failed", "ok": listening, "value": str(port) if listening else "не найден"},
        {"id": "data-plane", "name": "Handshake и данные", "state": "passed" if connection["state"] == "confirmed" else "failed" if connection["state"] == "failed" else "unknown", "ok": connection["state"] == "confirmed", "value": connection["title"]},
    ]
    findings = []
    if not active or not listening:
        findings.append({"severity": "critical", "code": "protocol_path", "title": "Протокол недоступен", "detail": "Служба или listener не подтверждены", "action": "Проверьте службу и журнал модуля"})
    elif connection["state"] == "failed":
        findings.append({"severity": "critical", "code": "data_plane_failed", "title": "Передача данных не подтверждена", "detail": connection["detail"], "action": "Проверьте клиентскую конфигурацию и журнал протокола"})
    elif connection["state"] != "confirmed":
        findings.append({"severity": "warning", "code": "data_plane_unverified", "title": "Реальное подключение ещё не проверено", "detail": connection["detail"], "action": "Запустите проверку передачи данных"})
    critical = any(item["severity"] == "critical" for item in findings)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "critical" if critical else "warning" if findings else "healthy", "score": 40 if critical else 75 if findings else 100, "checks": checks, "findings": findings, "network": {}}


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


cpu_sampler = CpuSampler()


def cpu_usage_percent() -> float | None:
    return cpu_sampler.sample()


def collect_system_resources() -> dict:
    return collect_resources({
        "cpu": lambda: (cpu_usage_percent(), os.cpu_count()),
        "load": lambda: os.getloadavg(),
        "memory": memory_info,
        "disk": disk_info,
        "network": network_info,
        "uptime": lambda: (float(Path("/proc/uptime").read_text().split()[0]),),
    })


def system_resources() -> dict:
    snapshot = metrics_monitor.snapshot() if metrics_monitor else None
    return snapshot if snapshot is not None else collect_system_resources()


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
        encoded = json.dumps(password, ensure_ascii=False)
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
    resources = system_resources()
    direct_protocols = {}
    for protocol in DIRECT_PROTOCOLS:
        settings_path = {"hysteria2": HYSTERIA2_SETTINGS, "tuic": TUIC_SETTINGS, "xray": XRAY_SETTINGS}[protocol]
        default_port = {"hysteria2": 8443, "tuic": 8444, "xray": 8445}[protocol]
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
        direct_protocols[protocol] = {
            "interface": "QUIC/UDP" if protocol != "xray" else "XHTTP/TCP",
            "port": int(settings.get("port", default_port)),
            "active": run("systemctl", "is-active", f"vps-control-{protocol}.service") == "active",
        }
    return {
        "server": {
            "name": SERVER_NAME,
            "public_ip": PUBLIC_IP,
            "city": SERVER_CITY,
            "country": SERVER_COUNTRY,
            "country_code": SERVER_COUNTRY_CODE,
            "uptime_s": resources.get("uptime_s"),
        },
        "resources": resources,
        "protocols": {
            "awg": {"interface": AWG_INTERFACE, "port": AWG_PORT, "active": bool(run("awg", "show", AWG_INTERFACE))},
            **direct_protocols,
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
        vpn_interfaces = interface_tokens.intersection({AWG_INTERFACE})
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
        and AWG_INTERFACE in panel_vpn_interfaces
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
    for protocol, interface, subnet in (("awg", AWG_INTERFACE, AWG_SUBNET),):
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
                    and Path(f"/sys/class/net/{AWG_INTERFACE}").exists()
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


def semantic_version(binary: Path, *arguments: str) -> str:
    if not binary.is_file():
        return ""
    output = run(str(binary), *arguments, timeout=5)
    match = re.search(r"\bv?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", output)
    return match.group(1) if match else ""


def apt_package_versions(package: str) -> tuple[str, str]:
    output = run("env", "LC_ALL=C", "apt-cache", "policy", package, timeout=10)
    installed = candidate = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Installed:"):
            installed = line.split(":", 1)[1].strip()
        elif line.startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
    return ("" if installed == "(none)" else installed, "" if candidate == "(none)" else candidate)


def protocol_installed_version(image_id: str, installed: bool) -> str:
    if not installed:
        return ""
    if image_id == "awg":
        output = run("modinfo", "-F", "version", "amneziawg", timeout=5) or run("awg", "--version", timeout=5)
        match = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)\b", output)
        return match.group(1) if match else apt_package_versions("amneziawg")[0]
    binaries = {
        "hysteria2": (Path("/usr/local/lib/vps-control-hysteria2/hysteria"), ("version",)),
        "tuic": (Path("/usr/local/lib/vps-control-tuic/sing-box"), ("version",)),
        "xray": (Path("/usr/local/lib/vps-control-xray/xray"), ("version",)),
    }
    binary = binaries.get(image_id)
    return semantic_version(binary[0], *binary[1]) if binary else ""


def latest_github_version(repository: str) -> str:
    output = run(
        "curl", "--silent", "--show-error", "--fail", "--location",
        "--connect-timeout", "4", "--max-time", "10",
        "-H", "Accept: application/vnd.github+json",
        f"https://api.github.com/repos/{repository}/releases/latest", timeout=12,
    )
    try:
        tag = str(json.loads(output).get("tag_name", ""))
    except (ValueError, json.JSONDecodeError):
        return ""
    match = re.search(r"(?:^|[/_-])v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$", tag)
    return match.group(1) if match else ""


def version_major(value: str) -> str:
    match = re.search(r"\d+", value.split(":")[-1])
    return match.group(0) if match else ""


def load_protocol_version_cache() -> None:
    if protocol_version_cache:
        return
    try:
        stored = json.loads(PROTOCOL_VERSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(stored, dict):
        protocol_version_cache.update({key: value for key, value in stored.items() if isinstance(value, dict)})


def save_protocol_version_cache() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROTOCOL_VERSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(protocol_version_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(PROTOCOL_VERSIONS_FILE)


def refresh_protocol_version(image_id: str) -> dict:
    repositories = {
        "hysteria2": "apernet/hysteria",
        "tuic": "SagerNet/sing-box",
        "xray": "XTLS/Xray-core",
    }
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        if image_id == "awg":
            installed_package, available = apt_package_versions("amneziawg")
        else:
            available = latest_github_version(repositories[image_id])
        if not available:
            raise ValueError("Не удалось определить последнюю версию")
        value = {"available_version": available, "version_checked_at": checked_at, "version_error": ""}
        if image_id == "awg":
            # The kernel module and the repository package use different
            # version schemes. Compare package-to-package and never infer a
            # breaking protocol change from those unrelated numbers.
            value.update({
                "update_available": bool(installed_package and available and installed_package != available),
                "update_breaking": False,
                "version_channel": "package",
            })
    except (KeyError, ValueError) as cause:
        value = {"available_version": "", "version_checked_at": checked_at, "version_error": str(cause)}
    protocol_version_cache[image_id] = value
    save_protocol_version_cache()
    return value


def protocol_image_manifests() -> dict[str, dict]:
    load_protocol_version_cache()
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
        installable = bool(manifest.get("installable", True))
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", image_id):
            continue
        if installable and (not re.fullmatch(r"[A-Za-z0-9._-]+", installer) or not (manifest_path.parent / installer).is_file()):
            continue
        if uninstaller and (
            not re.fullmatch(r"[A-Za-z0-9._-]+", uninstaller)
            or not (manifest_path.parent / uninstaller).is_file()
        ):
            continue
        interface_env = str(manifest.get("interface_env", ""))
        interface = os.getenv(interface_env, "") if interface_env else ""
        service_template = str(manifest.get("service", ""))
        service = service_template.replace("{interface}", interface) if service_template and (interface or "{interface}" not in service_template) else ""
        installed = bool(service and run("systemctl", "show", service, "--property=LoadState", "--value") == "loaded")
        installed_version = protocol_installed_version(image_id, installed)
        version_info = protocol_version_cache.get(image_id, {})
        available_version = str(version_info.get("available_version", ""))
        compared_update = bool(installed_version and available_version and installed_version != available_version)
        update_available = bool(version_info.get("update_available", compared_update))
        compared_breaking = bool(update_available and version_major(installed_version) != version_major(available_version))
        images[image_id] = {
            "id": image_id,
            "name": str(manifest.get("name", image_id)),
            "version": str(manifest.get("version", "")),
            "description": str(manifest.get("description", "")),
            "category": str(manifest.get("category", "network")),
            "category_name": str(manifest.get("category_name", "Сетевые модули")),
            "kind": str(manifest.get("kind", "tunnel")),
            "status": str(manifest.get("status", "available")),
            "installable": installable,
            "interface": interface,
            "service": service,
            # Installed and running are different states. A stopped tunnel must
            # remain manageable instead of being offered for installation again.
            "installed": installed,
            "removable": bool(uninstaller),
            "installed_version": installed_version,
            "available_version": available_version,
            "update_available": update_available,
            "update_breaking": bool(version_info.get("update_breaking", compared_breaking)),
            "version_channel": version_info.get("version_channel", "release"),
            "version_checked_at": version_info.get("version_checked_at"),
            "version_error": version_info.get("version_error", ""),
        }
    return images


@app.get("/api/protocol-images")
def protocol_images(_: None = Depends(require_token)) -> dict:
    items = list(protocol_image_manifests().values())
    items.sort(key=lambda item: (MODULE_ORDER.get(item["id"], 999), item["name"].casefold()))
    return {"items": items}


@app.post("/api/protocol-images/versions/check")
def check_protocol_versions(_: None = Depends(require_token)) -> dict:
    if not protocol_version_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Проверка версий уже выполняется")
    try:
        images = protocol_image_manifests()
        for image_id, image in images.items():
            if image.get("installable") and image_id in {"awg", "hysteria2", "tuic", "xray"}:
                refresh_protocol_version(image_id)
        items = list(protocol_image_manifests().values())
        items.sort(key=lambda item: (MODULE_ORDER.get(item["id"], 999), item["name"].casefold()))
        return {"items": items, "checked_at": datetime.now(timezone.utc).isoformat()}
    finally:
        protocol_version_lock.release()


@app.post("/api/protocol-images/{image_id}/version/check")
def check_protocol_version(image_id: str, _: None = Depends(require_token)) -> dict:
    image = protocol_image_manifests().get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if not image.get("installable") or image_id not in {"awg", "hysteria2", "tuic", "xray"}:
        raise HTTPException(status_code=409, detail="Protocol does not support version checks")
    if not protocol_version_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Проверка версии уже выполняется")
    try:
        refresh_protocol_version(image_id)
        item = protocol_image_manifests().get(image_id)
        if not item:
            raise HTTPException(status_code=404, detail="Protocol image not found")
        return {"item": item, "checked_at": datetime.now(timezone.utc).isoformat()}
    finally:
        protocol_version_lock.release()


@app.post("/api/protocol-images/{image_id}/install")
def install_protocol_image(image_id: str, _: None = Depends(require_token)) -> dict:
    image = protocol_image_manifests().get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if not image.get("installable"):
        raise HTTPException(status_code=409, detail="Module is not available for installation yet")
    return start_application_task(
        f"vps-control-protocol-{image_id}", f"protocol-install:{image_id}",
        [CONTROL_COMMAND, "protocol-install", image_id], "Запуск установки протокола",
        "Unable to install protocol image", runtime_max_seconds=1260,
    )


@app.post("/api/protocol-images/{image_id}/update")
def update_protocol_image(image_id: str, _: None = Depends(require_token)) -> dict:
    image = protocol_image_manifests().get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if not image.get("installed"):
        raise HTTPException(status_code=409, detail="Protocol is not installed")
    if not image.get("update_available"):
        raise HTTPException(status_code=409, detail="Новая версия не найдена. Сначала выполните проверку обновлений")
    return start_application_task(
        f"vps-control-protocol-update-{image_id}", f"protocol-update:{image_id}",
        [CONTROL_COMMAND, "protocol-update", image_id], "Запуск обновления протокола",
        "Unable to update protocol",
    )


@app.delete("/api/protocol-images/{image_id}")
def remove_protocol_image(image_id: str, _: None = Depends(require_token)) -> dict:
    image = protocol_image_manifests().get(image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Protocol image not found")
    if not image.get("removable"):
        raise HTTPException(status_code=409, detail="Protocol does not support removal")
    if os.getenv("ACCESS_MODE", "external") == "vpn" and image_id == "awg":
        raise HTTPException(
            status_code=409,
            detail="AmneziaWG cannot be removed while panel access is VPN-only",
        )
    return start_application_task(
        f"vps-control-protocol-remove-{image_id}", f"protocol-remove:{image_id}",
        [CONTROL_COMMAND, "protocol-remove", image_id], "Запуск удаления протокола",
        "Unable to remove protocol",
    )


def resolve_application_action(action: dict) -> dict:
    """Resolve a persisted action without mistaking a lost unit for success."""
    action = dict(action)
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
                # The manager writes a terminal state before a successful unit
                # is collected. If both are missing, success cannot be proven.
                resolved_state = "failed"
                result = result if result not in ("", "unknown") else "unknown"
                action["message"] = "Системная задача завершилась без подтверждённого результата"
        action["state"] = resolved_state
        action["result"] = result
    return action


@app.get("/api/application/status")
def application_status(_: None = Depends(require_token)) -> dict:
    action = {}
    if ACTION_FILE.exists():
        try:
            action = json.loads(ACTION_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            action = {}
    action = resolve_application_action(action)
    def component_details(service: str, unit: str, component_name: str, purpose: str, endpoint: str) -> dict:
        properties = {}
        for line in run(
            "systemctl", "show", unit,
            "--property=LoadState,ActiveState,SubState,UnitFileState,NRestarts,ActiveEnterTimestampMonotonic",
        ).splitlines():
            if "=" in line:
                key_name, value = line.split("=", 1)
                properties[key_name] = value
        active = properties.get("ActiveState") == "active"
        try:
            active_since_monotonic = int(properties.get("ActiveEnterTimestampMonotonic") or 0)
        except ValueError:
            active_since_monotonic = 0
        uptime_seconds = max(0, int(time.monotonic() - active_since_monotonic / 1_000_000)) if active and active_since_monotonic else 0
        return {
            "Service": service,
            "service_id": service,
            "unit": unit,
            "State": "running" if active else properties.get("ActiveState", "unknown"),
            "component_name": component_name,
            "purpose": purpose,
            "endpoint": endpoint,
            "healthy": active,
            "enabled": properties.get("UnitFileState") in ("enabled", "enabled-runtime", "static"),
            "restarts": int(properties.get("NRestarts") or 0),
            "uptime_seconds": uptime_seconds,
            "installed": properties.get("LoadState") == "loaded",
            "status_text": "systemd-служба запущена" if active else "systemd-служба остановлена или неисправна",
        }

    http_port = os.getenv("HTTP_PORT", "80")
    api_component = component_details("api", "vps-control-api.service", "API панели", "Принимает команды интерфейса", "127.0.0.1:8000")
    web_component = component_details("web", "vps-control-web.service", "Веб-интерфейс", "Отдаёт интерфейс управления сервером", "127.0.0.1:3000")
    gateway_component = component_details("gateway", "caddy.service", "Сетевой шлюз", "Публикует панель и направляет запросы к API", f"0.0.0.0:{http_port}")
    web_unit_loaded = web_component["installed"]
    caddy_unit_loaded = gateway_component["installed"]
    legacy_container_names = run("docker", "ps", "--format", "{{.Names}}") if not (web_unit_loaded and caddy_unit_loaded) else ""
    legacy_runtime = any(name.startswith(("vps-control-web-", "vps-control-gateway-")) for name in legacy_container_names.splitlines())
    containers = [web_component, gateway_component]
    return {
        "api": {
            "active": api_component["healthy"],
            "enabled": api_component["enabled"],
            "service_id": api_component["service_id"],
            "unit": api_component["unit"],
            "endpoint": api_component["endpoint"],
            "restarts": api_component["restarts"],
            "uptime_seconds": api_component["uptime_seconds"],
        },
        "containers": containers,
        "action": action,
        "service_mode": {
            "active": SERVICE_MODE_FILE.exists(),
            "rollback_available": (DATA_DIR / "test-app-backup").is_dir(),
        },
        "release": application_version_status(),
        "runtime": {
            "mode": "systemd" if web_unit_loaded and caddy_unit_loaded else "legacy-docker" if legacy_runtime else "incomplete",
            "migration_required": legacy_runtime,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


class ApplicationAction(BaseModel):
    action: Literal["restart", "update", "test-update", "test-rollback", "network-check", "integrity-check", "identity", "secure", "system-update", "kernel-update", "vpn-firewall", "optimize", "reboot", "poweroff"]


@app.post("/api/application/action")
def application_action(payload: ApplicationAction, _: None = Depends(require_token)) -> dict:
    if payload.action in ("test-update", "test-rollback") and not SERVICE_MODE_FILE.exists():
        raise HTTPException(status_code=409, detail="Test version requires active service mode")
    bundled_command = INSTALL_DIR / "scripts" / "vps-control.sh"
    command = (
        ["/bin/bash", str(bundled_command), payload.action]
        if bundled_command.exists()
        else [CONTROL_COMMAND, payload.action]
    )
    return start_application_task(
        "vps-control-action", payload.action, command,
        "Команда передана серверу", "Unable to start application action",
    )


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
        "awg": {"name": "AmneziaWG", "unit": f"awg-quick@{AWG_INTERFACE}.service", "controls": ["start", "stop", "restart"]},
        "hysteria2": {"name": "Hysteria2", "unit": "vps-control-hysteria2.service", "controls": ["start", "stop", "restart"]},
        "tuic": {"name": "TUIC v5", "unit": "vps-control-tuic.service", "controls": ["start", "stop", "restart"]},
        "xray": {"name": "Xray", "unit": "vps-control-xray.service", "controls": ["start", "stop", "restart"]},
        "monitor": {"name": "Мониторинг VPN", "unit": "vpn-monitor.timer", "controls": ["start", "stop", "restart"]},
        "fail2ban": {"name": "Fail2ban", "unit": "fail2ban.service", "controls": ["start", "stop", "restart"]},
        "updates": {
            "name": "Автообновления системы", "unit": "unattended-upgrades.service",
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


def installed_release_branch() -> str:
    metadata = INSTALL_DIR / ".prebuilt-release"
    try:
        values = dict(
            line.split("=", 1)
            for line in metadata.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    except OSError:
        return "light"
    return "test-light" if values.get("channel") == "test" else "light"


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
        branch = installed_release_branch()
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
    expected_branch = installed_release_branch()
    installed_commit = installed_build_commit()
    refreshing = (
        age > 600
        or cached.get("branch") != expected_branch
        or cached.get("current_commit") != installed_commit
    )
    if refreshing and not app_version_refresh_lock.locked():
        threading.Thread(target=refresh_application_version_cache, daemon=True).start()
    if cached:
        cache_matches_install = (
            cached.get("branch") == expected_branch
            and cached.get("current_commit") == installed_commit
        )
        return {
            **cached,
            "branch": expected_branch,
            "current_commit": installed_commit,
            "latest_commit": cached.get("latest_commit", "") if cache_matches_install else "",
            "outdated": cached.get("outdated") if cache_matches_install else None,
            "error": cached.get("error", "") if cache_matches_install else "",
            "refreshing": refreshing,
        }
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
        "protocol_scan": {"enabled": False, "cadence": "daily", "weekday": "Sun", "hour": 2, "minute": 30},
        "application_update": {"enabled": False, "cadence": "daily", "weekday": "Sun", "hour": 3, "minute": 30},
        "kernel_update": {"enabled": False, "cadence": "weekly", "weekday": "Sun", "hour": 4, "minute": 30},
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
        # Optional modules (AWG, monitoring, fail2ban, etc.) are
        # not shown until their systemd unit is actually installed.
        module_configured = {
            "awg": AWG_CONFIG.exists(),
            "monitor": AWG_CONFIG.exists(),
        }.get(service_id, True)
        if details["installed"] and module_configured:
            items.append(details)
    vpn_urls = []
    address = run("bash", "-lc", f"ip -o -4 addr show dev {AWG_INTERFACE} 2>/dev/null | awk 'NR==1 {{split($4,a,\"/\"); print a[1]}}'")
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
            "protocol_scan": timer_details("protocol-scan"),
            "application_update": timer_details("application-update"),
            "kernel_update": timer_details("kernel-update"),
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


@app.get("/api/metrics/history")
def get_metrics_history(period: Literal["live", "day", "week", "quarter"] = "live", _: None = Depends(require_token)) -> dict:
    try:
        return {**metrics_history_store.query(period), "error": metrics_monitor.error if metrics_monitor else ""}
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail="История метрик временно недоступна") from None


@app.get("/api/live-status")
def live_status(_: None = Depends(require_token)) -> dict:
    """Cheap sub-second telemetry without diagnostics, package checks or ICMP."""
    resources = system_resources()
    ufw_config = Path("/etc/ufw/ufw.conf")
    live_clients = interface_dump(include_quality=False) + direct_client_rows()

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
                if client.get("handshake_age_s") is not None and client["handshake_age_s"] < 180
            ]),
            "interface_rx_bytes": received,
            "interface_tx_bytes": transmitted,
        }

    def direct_protocol_live(protocol: str) -> dict:
        unit = f"vps-control-{protocol}.service"
        received, transmitted = service_bytes(unit)
        protocol_clients = [client for client in live_clients if client["protocol"] == protocol]
        return {
            "active": run("systemctl", "is-active", unit) == "active",
            "peers": len(protocol_clients),
            "online_peers": 0,
            "interface_rx_bytes": received,
            "interface_tx_bytes": transmitted,
        }

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "resources": resources,
        "protocols": {
            "awg": protocol_live("awg", AWG_INTERFACE),
            **{protocol: direct_protocol_live(protocol) for protocol in DIRECT_PROTOCOLS},
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
    if service_id == "awg" and payload.action == "stop" and os.getenv("ACCESS_MODE", "external") == "vpn":
        raise HTTPException(status_code=409, detail="AmneziaWG cannot be stopped while panel access is VPN-only")
    if service_id == "ssh" and payload.action == "stop":
        vpn_ready = Path(f"/sys/class/net/{AWG_INTERFACE}").exists() and run("systemctl", "is-active", f"awg-quick@{AWG_INTERFACE}.service") == "active"
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
        units = ["ssh.service"]
        if run("systemctl", "show", "ssh.socket", "--property=LoadState", "--value") == "loaded":
            units.insert(0, "ssh.socket")
        run("systemctl", payload.action, *units, timeout=30, check=True)
    else:
        run("systemctl", payload.action, definition["unit"], timeout=30, check=True)
    return service_details(service_id, definition)


class PanelAccessSettings(BaseModel):
    mode: Literal["external", "vpn"]


@app.put("/api/services/panel-access")
def update_panel_access(payload: PanelAccessSettings, _: None = Depends(require_token)) -> dict:
    if payload.mode == "vpn":
        available_interfaces = [AWG_INTERFACE] if Path(f"/sys/class/net/{AWG_INTERFACE}").exists() else []
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
    if not payload.active and installed_release_branch() == "test-light":
        raise HTTPException(status_code=409, detail="Return to light before disabling service mode")
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
    protocol_scan: AutomationSchedule
    application_update: AutomationSchedule
    kernel_update: AutomationSchedule


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
            "protocol_scan": timer_details("protocol-scan"),
            "application_update": timer_details("application-update"),
            "kernel_update": timer_details("kernel-update"),
        },
    }


@app.get("/api/clients")
def clients(_: None = Depends(require_token)) -> dict:
    return {"items": interface_dump() + direct_client_rows()}


def xray_server_names() -> list[str]:
    names: list[str] = []
    try:
        server = json.loads(XRAY_CONFIG.read_text(encoding="utf-8"))
        inbound = next(row for row in server.get("inbounds", []) if row.get("protocol") == "vless")
        configured = inbound.get("streamSettings", {}).get("realitySettings", {}).get("serverNames", [])
        if isinstance(configured, list):
            names.extend(str(name).strip() for name in configured if str(name).strip())
    except (OSError, StopIteration, json.JSONDecodeError):
        pass
    try:
        settings = json.loads(XRAY_SETTINGS.read_text(encoding="utf-8"))
        fallback = str(settings.get("server_name", "")).strip()
        if fallback:
            names.append(fallback)
    except (OSError, json.JSONDecodeError):
        pass
    return list(dict.fromkeys(names))


@app.get("/api/clients/options")
def client_options(_: None = Depends(require_token)) -> dict:
    server_names = xray_server_names()
    return {
        "awg": {key.lower(): int(value) for key, value in AWG_PROFILE.items()},
        "xray": {"server_names": server_names, "default_sni": server_names[0] if server_names else ""},
    }


class ClientSettings(BaseModel):
    dns: str = Field(default="1.1.1.1, 1.0.0.1", min_length=1, max_length=255, pattern=r"^[0-9A-Fa-f:., ]+$")
    mtu: int | None = Field(default=None, ge=576, le=1500)
    keepalive: int = Field(default=25, ge=0, le=300)
    route_mode: Literal["ipv4", "all", "custom"] = "ipv4"
    allowed_ips: str = Field(default="0.0.0.0/0", min_length=3, max_length=255, pattern=r"^[0-9A-Fa-f:.,/ ]+$")
    awg_jc: int | None = Field(default=None, ge=0, le=128)
    awg_jmin: int | None = Field(default=None, ge=0, le=1280)
    awg_jmax: int | None = Field(default=None, ge=0, le=1280)
    proxy_bind: Literal["loopback", "lan"] = "loopback"
    local_auth_enabled: bool = False
    local_username: str = Field(default="proxy", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    local_password: str = Field(default="", max_length=128)
    local_socks_port: int = Field(default=1080, ge=1024, le=65535)
    local_http_port: int = Field(default=10809, ge=1024, le=65535)
    http_proxy_enabled: bool = False
    disable_udp: bool = False
    fast_open: bool = False
    lazy: bool = False
    hysteria_congestion: Literal["bbr", "reno"] = "bbr"
    bbr_profile: Literal["standard", "conservative", "aggressive"] = "standard"
    up_mbps: int = Field(default=0, ge=0, le=10000)
    down_mbps: int = Field(default=0, ge=0, le=10000)
    disable_loss_compensation: bool = False
    congestion_control: Literal["bbr", "cubic", "new_reno"] = "bbr"
    heartbeat: Literal["5s", "10s", "15s", "30s"] = "10s"
    udp_relay_mode: Literal["native", "quic"] = "native"
    network: Literal["all", "tcp", "udp"] = "all"
    tcp_fast_open: bool = False
    set_system_proxy: bool = False
    udp_fragment: bool = False
    udp_timeout: Literal["1m", "3m", "5m", "10m"] = "5m"
    initial_packet_size: int = Field(default=0, ge=0, le=1500)
    disable_path_mtu_discovery: bool = False
    fingerprint: Literal["chrome", "firefox", "safari"] = "chrome"
    xray_sni: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.-]*$")
    mux_enabled: bool = False
    mux_concurrency: int = Field(default=8, ge=1, le=128)
    xudp_concurrency: int = Field(default=16, ge=1, le=1024)
    xudp_proxy_udp443: Literal["reject", "allow", "skip"] = "reject"
    sniffing: bool = True
    route_only: bool = False
    routing_domain_strategy: Literal["AsIs", "IPIfNonMatch", "IPOnDemand"] = "AsIs"
    log_level: Literal["none", "error", "warning", "info"] = "warning"
    xray_dns: str = Field(default="", max_length=255, pattern=r"^[0-9A-Fa-f:., ]*$")
    block_bittorrent: bool = False


class ClientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=48, pattern=r"^[\w .-]+$")
    protocol: Literal["awg", "hysteria2", "tuic", "xray"]
    settings: ClientSettings = Field(default_factory=ClientSettings)


def local_proxy_fields(payload: ClientCreate) -> list[dict]:
    host = "LAN" if payload.settings.proxy_bind == "lan" else "127.0.0.1"
    if payload.protocol == "tuic":
        fields = [{"label": "Локальный mixed-прокси", "value": f"{host}:{payload.settings.local_socks_port}"}]
    else:
        fields = [{"label": "Локальный SOCKS", "value": f"{host}:{payload.settings.local_socks_port}"}]
        if payload.protocol == "xray" or payload.settings.http_proxy_enabled:
            fields.append({"label": "Локальный HTTP", "value": f"{host}:{payload.settings.local_http_port}"})
    if payload.settings.local_auth_enabled:
        fields.extend([
            {"label": "Локальный логин", "value": payload.settings.local_username},
            {"label": "Локальный пароль", "value": payload.settings.local_password, "secret": True},
        ])
    return fields


def key(command: str) -> str:
    return run("bash", "-lc", command, check=True)


def next_address() -> ipaddress.IPv4Address:
    network = AWG_SUBNET
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


def connection_profile(
    *,
    protocol: str,
    name: str,
    endpoint: str,
    filename: str,
    config: str,
    fields: list[dict],
    apps: list[str],
    steps: list[str],
    uri: str = "",
    qr_content: str = "",
) -> dict:
    """Build the one-time client handoff returned after creating an identity."""
    mime_type = "application/json;charset=utf-8" if filename.endswith(".json") else "application/yaml;charset=utf-8" if filename.endswith((".yaml", ".yml")) else "text/plain;charset=utf-8"
    delivery = {
        "file": {"filename": filename, "content": config, "mime_type": mime_type},
        "link": {"uri": uri, "label": "Скопировать ссылку подключения"} if uri else None,
        "qr": {"content": qr_content, "label": "Сканировать в клиентском приложении"} if qr_content else None,
    }
    return {
        # Keep the original fields for compatibility with older panel builds.
        "filename": filename,
        "config": config,
        "profile": {
            "protocol": protocol,
            "name": name,
            "endpoint": endpoint,
            "fields": fields,
            "apps": apps,
            "steps": steps,
            "delivery": delivery,
            "one_time": True,
        },
    }


def uri_endpoint(host: str) -> str:
    try:
        return f"[{host}]" if ipaddress.ip_address(host).version == 6 else host
    except ValueError:
        return host


@app.post("/api/clients")
def create_client(payload: ClientCreate, _: None = Depends(require_token)) -> dict:
    if payload.protocol == "xray" and payload.settings.local_socks_port == payload.settings.local_http_port:
        raise HTTPException(status_code=422, detail="SOCKS and HTTP ports must be different")
    if payload.protocol == "awg" and payload.settings.awg_jmin is not None and payload.settings.awg_jmax is not None and payload.settings.awg_jmin > payload.settings.awg_jmax:
        raise HTTPException(status_code=422, detail="AmneziaWG Jmin must not exceed Jmax")
    if payload.protocol in DIRECT_PROTOCOLS and payload.settings.local_auth_enabled and len(payload.settings.local_password) < 8:
        raise HTTPException(status_code=422, detail="Local proxy password must contain at least 8 characters")
    if payload.protocol in DIRECT_PROTOCOLS and payload.settings.proxy_bind == "lan" and not payload.settings.local_auth_enabled:
        raise HTTPException(status_code=422, detail="LAN proxy access requires local authentication")
    if payload.protocol == "tuic" and payload.settings.initial_packet_size not in range(1200, 1501) and payload.settings.initial_packet_size != 0:
        raise HTTPException(status_code=422, detail="Initial QUIC packet size must be 0 or between 1200 and 1500")
    if payload.protocol == "xray" and payload.settings.xray_sni:
        allowed_server_names = xray_server_names()
        if payload.settings.xray_sni not in allowed_server_names:
            raise HTTPException(status_code=422, detail="Xray SNI is not allowed by the server REALITY configuration")
    if payload.settings.route_mode == "custom":
        try:
            for network in payload.settings.allowed_ips.split(","):
                ipaddress.ip_network(network.strip(), strict=False)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Allowed IPs contain an invalid network") from exc
    client_id = secrets.token_hex(8)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", payload.name).strip(".-") or "client"
    if payload.protocol == "hysteria2":
        if not HYSTERIA2_SETTINGS.exists() or run("systemctl", "is-enabled", "vps-control-hysteria2.service") != "enabled":
            raise HTTPException(status_code=409, detail="Hysteria2 protocol is not installed")
        with client_mutation_lock:
            try:
                settings = json.loads(HYSTERIA2_SETTINGS.read_text(encoding="utf-8"))
                users = json.loads(HYSTERIA2_USERS.read_text(encoding="utf-8")) if HYSTERIA2_USERS.exists() else {}
                password = secrets.token_urlsafe(32); users[client_id] = password
                temporary = HYSTERIA2_USERS.with_suffix(".tmp")
                temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"); os.chmod(temporary, 0o600); temporary.replace(HYSTERIA2_USERS)
                endpoint = str(settings.get("domain", "")).strip() or PUBLIC_IP
                identity = str(settings.get("domain", "")).strip() or certificate_server_name(HYSTERIA2_DIR / "server.crt")
                fingerprint = run("openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(HYSTERIA2_DIR / "server.crt")).partition("=")[2].strip()
                config_lines = [
                    f"server: {endpoint}:{int(settings.get('port', 8443))}", f"auth: {client_id}:{password}",
                    "tls:", f"  sni: {identity}", "  insecure: true", f"  pinSHA256: {fingerprint}",
                    f"fastOpen: {str(payload.settings.fast_open).lower()}", f"lazy: {str(payload.settings.lazy).lower()}",
                    "congestion:", f"  type: {payload.settings.hysteria_congestion}",
                ]
                if payload.settings.hysteria_congestion == "bbr":
                    config_lines.append(f"  bbrProfile: {payload.settings.bbr_profile}")
                if payload.settings.up_mbps or payload.settings.down_mbps:
                    config_lines.append("bandwidth:")
                    if payload.settings.up_mbps:
                        config_lines.append(f"  up: {payload.settings.up_mbps} mbps")
                    if payload.settings.down_mbps:
                        config_lines.append(f"  down: {payload.settings.down_mbps} mbps")
                listen_host = "0.0.0.0" if payload.settings.proxy_bind == "lan" else "127.0.0.1"
                if payload.settings.up_mbps or payload.settings.down_mbps:
                    config_lines.append(f"  disableLossCompensation: {str(payload.settings.disable_loss_compensation).lower()}")
                config_lines.extend(["socks5:", f"  listen: {listen_host}:{payload.settings.local_socks_port}"])
                if payload.settings.local_auth_enabled:
                    config_lines.extend([f"  username: {json.dumps(payload.settings.local_username)}", f"  password: {json.dumps(payload.settings.local_password)}"])
                config_lines.append(f"  disableUDP: {str(payload.settings.disable_udp).lower()}")
                if payload.settings.http_proxy_enabled:
                    config_lines.extend(["http:", f"  listen: {listen_host}:{payload.settings.local_http_port}"])
                    if payload.settings.local_auth_enabled:
                        config_lines.extend([f"  username: {json.dumps(payload.settings.local_username)}", f"  password: {json.dumps(payload.settings.local_password)}"])
                config = "\n".join([*config_lines, ""])
                items = read_clients(); items.append({"id": client_id, "name": payload.name, "protocol": payload.protocol, "public_key": client_id, "endpoint": endpoint, "created_at": datetime.now(timezone.utc).isoformat()}); write_clients(items)
                port = int(settings.get("port", 8443))
                query = urlencode({"sni": identity, "insecure": "1", "pinSHA256": fingerprint})
                uri = f"hysteria2://{quote(client_id, safe='')}:{quote(password, safe='')}@{uri_endpoint(endpoint)}:{port}/?{query}#{quote(payload.name, safe='')}"
                return {"id": client_id, **connection_profile(
                    protocol="hysteria2", name=payload.name, endpoint=f"{endpoint}:{port}", filename=f"{safe_name}-hysteria2.yaml", config=config,
                    fields=[{"label": "Пользователь", "value": client_id}, {"label": "Пароль", "value": password, "secret": True}, {"label": "TLS SNI", "value": identity}, *local_proxy_fields(payload)],
                    apps=["Hiddify", "NekoBox", "Hysteria 2"],
                    steps=["Откройте ссылку или отсканируйте QR в совместимом клиенте.", "Если импорт ссылки недоступен, загрузите YAML-файл.", "Включите созданный профиль и проверьте доступ в интернет."],
                    uri=uri, qr_content=uri,
                )}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=500, detail="Unable to create Hysteria2 connection") from exc
    if payload.protocol == "tuic":
        config_path = TUIC_CONFIG
        settings_path = TUIC_SETTINGS
        binary = "/usr/local/lib/vps-control-tuic/sing-box"
        unit = "vps-control-tuic.service"
        if not config_path.exists() or run("systemctl", "is-enabled", unit) != "enabled":
            raise HTTPException(status_code=409, detail=f"{payload.protocol} protocol is not installed")
        with client_mutation_lock:
            original = config_path.read_bytes(); temporary = config_path.with_suffix(".tmp.json")
            try:
                server = json.loads(original); inbound = next(row for row in server.get("inbounds", []) if row.get("type") == payload.protocol)
                password = secrets.token_urlsafe(32); user_uuid = str(uuid.uuid4())
                user = {"name": client_id, "password": password, "uuid": user_uuid}
                inbound.setdefault("users", []).append(user); temporary.write_text(json.dumps(server, ensure_ascii=False, indent=2), encoding="utf-8"); os.chmod(temporary, 0o600)
                result = subprocess.run([binary, "check", "-c", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
                if result.returncode: raise RuntimeError(result.stderr.strip() or "sing-box rejected configuration")
                temporary.replace(config_path); run("systemctl", "restart", unit, timeout=20, check=True)
                settings = json.loads(settings_path.read_text(encoding="utf-8")); endpoint = PUBLIC_IP; certificate = (config_path.parent / "server.crt").read_text(encoding="utf-8")
                outbound = {"type": payload.protocol, "tag": "connection-out", "server": endpoint, "server_port": int(settings.get("port", 8444)), "password": password,
                            "tls": {"enabled": True, "server_name": certificate_server_name(config_path.parent / "server.crt"), "certificate": certificate}}
                outbound.update({"uuid": user_uuid, "congestion_control": payload.settings.congestion_control, "udp_relay_mode": payload.settings.udp_relay_mode, "zero_rtt_handshake": False, "heartbeat": payload.settings.heartbeat})
                if payload.settings.network != "all":
                    outbound["network"] = payload.settings.network
                if payload.settings.initial_packet_size:
                    outbound["initial_packet_size"] = payload.settings.initial_packet_size
                outbound["disable_path_mtu_discovery"] = payload.settings.disable_path_mtu_discovery
                listen_host = "0.0.0.0" if payload.settings.proxy_bind == "lan" else "127.0.0.1"
                mixed_inbound = {"type": "mixed", "tag": "mixed-in", "listen": listen_host, "listen_port": payload.settings.local_socks_port, "tcp_fast_open": payload.settings.tcp_fast_open, "set_system_proxy": payload.settings.set_system_proxy, "udp_fragment": payload.settings.udp_fragment, "udp_timeout": payload.settings.udp_timeout}
                if payload.settings.local_auth_enabled:
                    mixed_inbound["users"] = [{"username": payload.settings.local_username, "password": payload.settings.local_password}]
                client = {"log": {"level": "warn"}, "inbounds": [mixed_inbound], "outbounds": [outbound], "route": {"final": "connection-out"}}
                items = read_clients(); items.append({"id": client_id, "name": payload.name, "protocol": payload.protocol, "public_key": user_uuid, "endpoint": endpoint, "created_at": datetime.now(timezone.utc).isoformat()}); write_clients(items)
                port = int(settings.get("port", 8444))
                server_name = certificate_server_name(config_path.parent / "server.crt")
                return {"id": client_id, **connection_profile(
                    protocol="tuic", name=payload.name, endpoint=f"{endpoint}:{port}", filename=f"{safe_name}-tuic.json", config=json.dumps(client, ensure_ascii=False, indent=2),
                    fields=[{"label": "UUID", "value": user_uuid, "secret": True}, {"label": "Пароль", "value": password, "secret": True}, {"label": "TLS SNI", "value": server_name}, *local_proxy_fields(payload)],
                    apps=["sing-box", "NekoBox"],
                    steps=["Скачайте персональный JSON-файл.", "Импортируйте файл в sing-box или совместимый клиент.", "Запустите профиль и используйте локальный mixed-прокси клиента."],
                )}
            except Exception as exc:
                config_path.write_bytes(original); os.chmod(config_path, 0o600); run("systemctl", "restart", unit, timeout=20)
                raise HTTPException(status_code=500, detail=f"Unable to create {payload.protocol} connection") from exc
            finally:
                temporary.unlink(missing_ok=True)
    if payload.protocol == "xray":
        binary = "/usr/local/lib/vps-control-xray/xray"
        unit = "vps-control-xray.service"
        if not XRAY_CONFIG.exists() or not Path(binary).exists() or run("systemctl", "is-enabled", unit) != "enabled":
            raise HTTPException(status_code=409, detail="Xray protocol is not installed")
        with client_mutation_lock:
            original = XRAY_CONFIG.read_bytes()
            temporary = XRAY_CONFIG.with_suffix(".tmp.json")
            try:
                server = json.loads(original)
                inbound = next(row for row in server.get("inbounds", []) if row.get("protocol") == "vless")
                user_uuid = str(uuid.uuid4())
                inbound.setdefault("settings", {}).setdefault("clients", []).append({"id": user_uuid, "email": f"{client_id}@312.net"})
                temporary.write_text(json.dumps(server, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(temporary, 0o600)
                result = subprocess.run([binary, "run", "-test", "-config", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or "Xray rejected configuration")
                temporary.replace(XRAY_CONFIG)
                run("systemctl", "restart", unit, timeout=20, check=True)
                settings = json.loads(XRAY_SETTINGS.read_text(encoding="utf-8"))
                endpoint = PUBLIC_IP
                path = str(settings.get("path", "/xhttp"))
                server_names = xray_server_names()
                server_name = payload.settings.xray_sni or (server_names[0] if server_names else str(settings.get("server_name", "www.microsoft.com")))
                listen_host = "0.0.0.0" if payload.settings.proxy_bind == "lan" else "127.0.0.1"
                socks_settings = {"udp": not payload.settings.disable_udp, "auth": "password" if payload.settings.local_auth_enabled else "noauth"}
                http_settings: dict = {}
                if payload.settings.local_auth_enabled:
                    local_user = {"user": payload.settings.local_username, "pass": payload.settings.local_password}
                    socks_settings["users"] = [local_user]
                    http_settings["users"] = [local_user]
                outbounds = [{
                    "tag": "xray-out", "protocol": "vless",
                    "settings": {"address": endpoint, "port": int(settings.get("port", 8445)), "id": user_uuid, "encryption": "none"},
                    "streamSettings": {
                        "network": "xhttp", "security": "reality", "xhttpSettings": {"path": path},
                        "realitySettings": {
                            "serverName": server_name,
                            "fingerprint": payload.settings.fingerprint, "password": str(settings.get("password", "")),
                            "shortId": str(settings.get("short_id", "")), "spiderX": path,
                        },
                    },
                    "mux": {"enabled": payload.settings.mux_enabled, "concurrency": payload.settings.mux_concurrency, "xudpConcurrency": payload.settings.xudp_concurrency, "xudpProxyUDP443": payload.settings.xudp_proxy_udp443},
                }]
                routing_rules = []
                if payload.settings.block_bittorrent:
                    outbounds.append({"tag": "blocked", "protocol": "blackhole"})
                    routing_rules.append({"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"})
                client = {
                    "log": {"loglevel": payload.settings.log_level},
                    "inbounds": [
                        {"listen": listen_host, "port": payload.settings.local_socks_port, "protocol": "socks", "settings": socks_settings, "sniffing": {"enabled": payload.settings.sniffing, "destOverride": ["http", "tls", "quic"], "routeOnly": payload.settings.route_only}},
                        {"listen": listen_host, "port": payload.settings.local_http_port, "protocol": "http", "settings": http_settings, "sniffing": {"enabled": payload.settings.sniffing, "destOverride": ["http", "tls"], "routeOnly": payload.settings.route_only}},
                    ],
                    "outbounds": outbounds,
                    "routing": {"domainStrategy": payload.settings.routing_domain_strategy, "rules": routing_rules},
                }
                if payload.settings.xray_dns:
                    client["dns"] = {"servers": [server.strip() for server in payload.settings.xray_dns.split(",") if server.strip()]}
                items = read_clients()
                items.append({"id": client_id, "name": payload.name, "protocol": "xray", "public_key": user_uuid, "endpoint": endpoint, "created_at": datetime.now(timezone.utc).isoformat()})
                write_clients(items)
                port = int(settings.get("port", 8445))
                query = urlencode({"type": "xhttp", "security": "reality", "pbk": str(settings.get("password", "")), "fp": payload.settings.fingerprint, "sni": server_name, "sid": str(settings.get("short_id", "")), "path": path})
                uri = f"vless://{quote(user_uuid, safe='')}@{uri_endpoint(endpoint)}:{port}?{query}#{quote(payload.name, safe='')}"
                return {"id": client_id, **connection_profile(
                    protocol="xray", name=payload.name, endpoint=f"{endpoint}:{port}", filename=f"{safe_name}-xray.json", config=json.dumps(client, ensure_ascii=False, indent=2),
                    fields=[{"label": "VLESS UUID", "value": user_uuid, "secret": True}, {"label": "Транспорт", "value": "XHTTP + REALITY"}, {"label": "Server name", "value": server_name}, *local_proxy_fields(payload)],
                    apps=["Hiddify", "v2rayN", "NekoBox"],
                    steps=["Отсканируйте QR или откройте VLESS-ссылку в клиенте.", "При ручном импорте используйте персональный JSON-файл.", "Сохраните профиль и включите системный VPN-режим клиента."],
                    uri=uri, qr_content=uri,
                )}
            except Exception as exc:
                XRAY_CONFIG.write_bytes(original)
                os.chmod(XRAY_CONFIG, 0o600)
                run("systemctl", "restart", unit, timeout=20)
                raise HTTPException(status_code=500, detail="Unable to create Xray connection") from exc
            finally:
                temporary.unlink(missing_ok=True)
    command = "awg"
    config_path = AWG_CONFIG
    if not config_path.exists():
        raise HTTPException(status_code=409, detail=f"{payload.protocol} protocol is not installed")
    private_key = key(f"{command} genkey")
    public_key = key(f"printf '%s' '{private_key}' | {command} pubkey")
    psk = key(f"{command} genpsk")
    address = next_address()
    interface = AWG_INTERFACE
    append_peer(config_path, client_id, public_key, psk, str(address))
    run_with_input(
        [command, "set", interface, "peer", public_key, "preshared-key", "/dev/stdin", "allowed-ips", f"{address}/32"],
        psk,
    )

    server_public = run(command, "show", interface, "public-key", check=True)
    awg_profile = dict(AWG_PROFILE)
    if payload.settings.awg_jc is not None:
        awg_profile["Jc"] = str(payload.settings.awg_jc)
    if payload.settings.awg_jmin is not None:
        awg_profile["Jmin"] = str(payload.settings.awg_jmin)
    if payload.settings.awg_jmax is not None:
        awg_profile["Jmax"] = str(payload.settings.awg_jmax)
    extra = "".join(f"{key} = {value}\n" for key, value in awg_profile.items())
    port = AWG_PORT
    allowed_ips = payload.settings.allowed_ips if payload.settings.route_mode == "custom" else "0.0.0.0/0, ::/0" if payload.settings.route_mode == "all" else "0.0.0.0/0"
    client_mtu = payload.settings.mtu if payload.settings.mtu is not None else AWG_MTU
    client_config = (
        f"[Interface]\nAddress = {address}/32\nDNS = {payload.settings.dns}\n"
        f"PrivateKey = {private_key}\nMTU = {client_mtu}\n{extra}\n[Peer]\n"
        f"PublicKey = {server_public}\nPresharedKey = {psk}\nAllowedIPs = {allowed_ips}\n"
        f"Endpoint = {PUBLIC_IP}:{port}\nPersistentKeepalive = {payload.settings.keepalive}\n"
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
    protocol_name = "AmneziaWG"
    return {"id": client_id, **connection_profile(
        protocol=payload.protocol, name=payload.name, endpoint=f"{PUBLIC_IP}:{port}", filename=f"{safe_name}-{payload.protocol}.conf", config=client_config,
        fields=[{"label": "Адрес в туннеле", "value": f"{address}/32"}, {"label": "Сервер", "value": f"{PUBLIC_IP}:{port}"}, {"label": "Профиль", "value": protocol_name}],
        apps=[protocol_name],
        steps=[f"Скачайте файл или отсканируйте QR в приложении {protocol_name}.", "Сохраните импортированный профиль с именем устройства.", "Включите туннель и проверьте доступ в интернет."],
        qr_content=client_config,
    )}


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: str, _: None = Depends(require_token)) -> dict:
    items = read_clients()
    item = next((entry for entry in items if entry["id"] == client_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Client not found")
    protocol = item["protocol"]
    if protocol == "hysteria2":
        if HYSTERIA2_USERS.exists():
            with client_mutation_lock:
                users = json.loads(HYSTERIA2_USERS.read_text(encoding="utf-8")); users.pop(client_id, None)
                temporary = HYSTERIA2_USERS.with_suffix(".tmp"); temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8"); os.chmod(temporary, 0o600); temporary.replace(HYSTERIA2_USERS)
        write_clients([entry for entry in items if entry["id"] != client_id]); return {"deleted": client_id}
    if protocol == "tuic":
        config_path = TUIC_CONFIG; binary = "/usr/local/lib/vps-control-tuic/sing-box"; unit = "vps-control-tuic.service"
        if config_path.exists() and Path(binary).exists():
            with client_mutation_lock:
                config_data = json.loads(config_path.read_text(encoding="utf-8")); inbound = next(row for row in config_data.get("inbounds", []) if row.get("type") == protocol)
                inbound["users"] = [user for user in inbound.get("users", []) if user.get("name") != client_id]
                temporary = config_path.with_suffix(".tmp.json"); temporary.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8"); os.chmod(temporary, 0o600)
                result = subprocess.run([binary, "check", "-c", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
                if result.returncode: temporary.unlink(missing_ok=True); raise HTTPException(status_code=500, detail=f"Unable to remove {protocol} connection")
                temporary.replace(config_path); run("systemctl", "restart", unit, timeout=20, check=True)
        write_clients([entry for entry in items if entry["id"] != client_id]); return {"deleted": client_id}
    if protocol == "xray":
        binary = "/usr/local/lib/vps-control-xray/xray"
        if XRAY_CONFIG.exists() and Path(binary).exists():
            with client_mutation_lock:
                original = XRAY_CONFIG.read_bytes()
                config_data = json.loads(original)
                inbound = next(row for row in config_data.get("inbounds", []) if row.get("protocol") == "vless")
                users = inbound.setdefault("settings", {}).setdefault("clients", [])
                inbound["settings"]["clients"] = [user for user in users if user.get("email") != f"{client_id}@312.net"]
                temporary = XRAY_CONFIG.with_suffix(".tmp.json")
                temporary.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.chmod(temporary, 0o600)
                result = subprocess.run([binary, "run", "-test", "-config", str(temporary)], capture_output=True, text=True, timeout=15, check=False)
                if result.returncode:
                    temporary.unlink(missing_ok=True)
                    raise HTTPException(status_code=500, detail="Unable to remove Xray connection")
                temporary.replace(XRAY_CONFIG)
                run("systemctl", "restart", "vps-control-xray.service", timeout=20, check=True)
        write_clients([entry for entry in items if entry["id"] != client_id])
        return {"deleted": client_id}
    command = "awg"
    interface = AWG_INTERFACE
    config = AWG_CONFIG
    run(command, "set", interface, "peer", item["public_key"], "remove", check=True)
    text = config.read_text(encoding="utf-8")
    pattern = rf"\n?# vps-control:{re.escape(client_id)}:begin.*?# vps-control:{re.escape(client_id)}:end\n?"
    config.write_text(re.sub(pattern, "\n", text, flags=re.S), encoding="utf-8")
    write_clients([entry for entry in items if entry["id"] != client_id])
    return {"deleted": client_id}


@app.get("/api/protocols/{protocol}/status")
def protocol_status(protocol: Literal["awg", "hysteria2", "tuic", "xray"], _: None = Depends(require_token)) -> dict:
    if protocol in DIRECT_PROTOCOLS:
        unit = f"vps-control-{protocol}.service"
        settings_path = {"hysteria2": HYSTERIA2_SETTINGS, "tuic": TUIC_SETTINGS, "xray": XRAY_SETTINGS}[protocol]
        default_port = {"hysteria2": 8443, "tuic": 8444, "xray": 8445}[protocol]
        try: settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): settings = {}
        rx, tx = service_bytes(unit); active = run("systemctl", "is-active", unit) == "active"; protocol_clients = [item for item in read_clients() if item.get("protocol") == protocol and not item.get("diagnostic")]
        connection = cached_connection_probe(protocol)
        return {"protocol": protocol, "interface": "QUIC/UDP" if protocol != "xray" else "XHTTP/TCP", "active": active, "service_active": active,
                "service_enabled": run("systemctl", "is-enabled", unit) == "enabled", "active_since": run("systemctl", "show", unit, "--property=ActiveEnterTimestamp", "--value"),
                "address": PUBLIC_IP, "listen_port": int(settings.get("port", default_port)), "mtu": 0, "peers": len(protocol_clients), "online_peers": 0, "endpoints": 0,
                "last_handshake_age_s": None, "peer_rx_bytes": rx, "peer_tx_bytes": tx, "interface_rx_bytes": rx, "interface_tx_bytes": tx,
                "rx_errors": 0, "tx_errors": 0, "rx_dropped": 0, "tx_dropped": 0, "transport": "VLESS / XHTTP / REALITY" if protocol == "xray" else "QUIC / UDP",
                "resources": cached_resource_availability(protocol), "history": protocol_history(protocol), "diagnostics": direct_protocol_diagnostics(protocol),
                "profile": protocol_runtime_profile(protocol), "connection_test": connection, "regional_reachability": regional_reachability(protocol, connection)}
    command = "awg"
    interface = AWG_INTERFACE
    unit = f"awg-quick@{interface}.service"
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
    connection = cached_connection_probe(protocol)
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
        "profile": protocol_runtime_profile(protocol),
        "connection_test": connection,
        "regional_reachability": regional_reachability(protocol, connection),
      }


@app.post("/api/protocols/{protocol}/resources/check")
def check_protocol_resources(protocol: Literal["awg", "hysteria2", "tuic", "xray"], _: None = Depends(require_token)) -> dict:
    return check_resource_availability(protocol)


@app.post("/api/protocols/{protocol}/diagnostics/check")
def check_network_diagnostics(protocol: Literal["awg", "hysteria2", "tuic", "xray"], _: None = Depends(require_token)) -> dict:
    return network_diagnostics(protocol, protocol_history(protocol), force=True) if protocol == "awg" else direct_protocol_diagnostics(protocol)


@app.post("/api/protocols/{protocol}/connection/check")
def check_protocol_data_plane(protocol: Literal["awg", "hysteria2", "tuic", "xray"], _: None = Depends(require_token)) -> dict:
    return check_protocol_connection(protocol)


class RegionalProbeReport(BaseModel):
    region: Literal["RU"] = "RU"
    state: Literal["confirmed", "failed"]
    latency_ms: int | None = Field(default=None, ge=0, le=120_000)
    bytes_sent: int = Field(default=0, ge=0, le=1_000_000_000)
    bytes_received: int = Field(default=0, ge=0, le=1_000_000_000)


@app.post("/api/protocols/{protocol}/regional-report")
def report_protocol_reachability(
    protocol: Literal["awg", "hysteria2", "tuic", "xray"],
    payload: RegionalProbeReport,
    _: None = Depends(require_token),
) -> dict:
    if payload.state == "confirmed" and (payload.bytes_sent <= 0 or payload.bytes_received <= 0):
        raise HTTPException(status_code=422, detail="Confirmed report requires transferred request and response bytes")
    report = {
        **payload.model_dump(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    with regional_probe_lock:
        try:
            reports = json.loads(REGIONAL_PROBES_FILE.read_text(encoding="utf-8"))
            if not isinstance(reports, dict):
                reports = {}
        except (OSError, json.JSONDecodeError):
            reports = {}
        reports[protocol] = report
        REGIONAL_PROBES_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = REGIONAL_PROBES_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(REGIONAL_PROBES_FILE)
    return regional_reachability(protocol, {})


@app.post("/api/protocols/{protocol}/restart")
def restart_protocol(protocol: Literal["awg", "hysteria2", "tuic", "xray"], _: None = Depends(require_token)) -> dict:
    unit = f"awg-quick@{AWG_INTERFACE}.service" if protocol == "awg" else f"vps-control-{protocol}.service"
    run("systemctl", "restart", unit, timeout=20, check=True)
    return {"protocol": protocol, "active": run("systemctl", "is-active", unit) == "active"}
