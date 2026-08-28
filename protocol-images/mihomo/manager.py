from __future__ import annotations

import base64
import functools
import fcntl
import hmac
import ipaddress
import json
import logging
import os
import secrets
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

APP_ROOT = Path("/opt/vps-control")
MODULE_ROOT = APP_ROOT / "protocol-images" / "mihomo"
SUBMODULE_ROOT = MODULE_ROOT / "modules"
DATA_ROOT = Path("/var/lib/vps-control/mihomo")
CONFIG_ROOT = Path("/etc/vps-control/mihomo")
PROFILE_FILE = DATA_ROOT / "profiles.json"
STATE_FILE = DATA_ROOT / "state.json"
SETTINGS_ROOT = DATA_ROOT / "settings"
RUNTIME_CORE_BIN = DATA_ROOT / "bin" / "mihomo"
BUNDLED_CORE_BIN = APP_ROOT / "api" / "bin" / "mihomo"
CORE_BIN = RUNTIME_CORE_BIN if RUNTIME_CORE_BIN.is_file() else BUNDLED_CORE_BIN
# Mihomo defaults to $HOME/.config/mihomo for its home/cache directory.
# The manager service runs with ProtectHome=true, so /root is masked and
# that mkdir fails. Point it at a writable directory explicitly instead.
CORE_HOME = DATA_ROOT / "core-home"
ACTION_FILE = DATA_ROOT / "action.json"
REALITY_XRAY_BIN = Path("/usr/local/lib/vps-control-mihomo-reality/xray")
REALITY_API_SERVER = "127.0.0.1:10086"
VLESS_CDN_SNIPPET = Path("/etc/caddy/vps-control.d/vless-cdn.caddy")
VLESS_CDN_ROUTE_ROOT = CONFIG_ROOT / "reality" / "caddy-routes"
DIRECT_VLESS_ENV = Path("/etc/vps-control/vless-reality-xhttp/reality.env")
XRAY_GITHUB_REPO = "XTLS/Xray-core"
MIHOMO_GITHUB_REPO = "MetaCubeX/mihomo"
github_release_lock = threading.Lock()
profile_mutation_lock = threading.Lock()
github_release_cache: dict[str, dict[str, Any]] = {}


def serialized_profile_mutation(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with profile_mutation_lock:
            return function(*args, **kwargs)
    return wrapped

TRANSPORTS = {
    "transport-wg",
    "transport-awg",
    "transport-shadowsocks",
    "transport-reality",
}
# DNS and routing are mandatory policy layers, not toggleable modules. Their
# settings are created with the first transport and edited on dedicated pages.
# Profiles may override individual routing values without replacing defaults.
ROUTING_MODULE_ID = "routing-policy"
DNS_MODULE_ID = "dns-private"
KNOWN_MODULES = TRANSPORTS

# Mirrors the DNS_PROVIDERS list in api/main.py (kept independent: separate
# process, no shared import). Only providers usable directly as a Mihomo
# nameserver/fallback entry (DoH URL or plain IP for UDP) are included.
DNS_PROVIDERS = (
    {"id": "cloudflare", "name": "Cloudflare — без фильтрации", "server": "https://cloudflare-dns.com/dns-query"},
    {"id": "google", "name": "Google Public DNS — без фильтрации", "server": "https://dns.google/dns-query"},
    {"id": "quad9", "name": "Quad9 Secure — блокировка вредоносных доменов", "server": "https://dns.quad9.net/dns-query"},
    {"id": "adguard", "name": "AdGuard DNS — блокировка рекламы и трекеров", "server": "https://dns.adguard-dns.com/dns-query"},
    {"id": "opendns", "name": "OpenDNS — базовая защита", "server": "208.67.222.222"},
    {"id": "cleanbrowsing", "name": "CleanBrowsing Security — вредоносные сайты", "server": "185.228.168.9"},
    {"id": "yandex-basic", "name": "Яндекс DNS — базовый, без фильтрации", "server": "https://common.dot.dns.yandex.net/dns-query"},
    {"id": "yandex-safe", "name": "Яндекс DNS — безопасный", "server": "77.88.8.88"},
    {"id": "yandex-family", "name": "Яндекс DNS — семейный", "server": "77.88.8.7"},
    {"id": "skydns", "name": "SkyDNS — российская фильтрация", "server": "193.58.251.251"},
    {"id": "nsdi", "name": "НСДИ", "server": "195.208.4.1"},
    {"id": "safedns", "name": "SafeDNS — безопасность и категории", "server": "195.46.39.39"},
)

SERVICE_BY_MODULE = {
    "transport-wg": "wg-quick@mh-wg0.service",
    "transport-awg": "awg-quick@mh-awg0.service",
    "transport-shadowsocks": "vps-control-mihomo-ss.target",
    "transport-reality": "vps-control-mihomo-reality.service",
}

app = FastAPI(
    title="GATE.312 Mihomo Manager",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
logger = logging.getLogger("vps-control.mihomo")
PUBLIC_COMMAND_ERROR = "Команда завершилась с ошибкой. Технические сведения сохранены в журнале."
TECHNICAL_ERROR_MARKERS = ("traceback", "systemctl", "journalctl", "apt-get", "dpkg", "stderr", "stdout", "command failed", "exit status", "failed to start")


def public_http_error(status_code: int, detail: object) -> str:
    message = str(detail or "").strip()
    technical = "\n" in message or any(marker in message.lower() for marker in TECHNICAL_ERROR_MARKERS)
    return PUBLIC_COMMAND_ERROR if status_code >= 500 or technical else (message or "Команда не выполнена.")


@app.exception_handler(HTTPException)
async def public_http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    detail = public_http_error(exc.status_code, exc.detail)
    if detail == PUBLIC_COMMAND_ERROR:
        logger.error("Suppressed technical Mihomo error (%s): %s", exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


class ModuleSettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class PresetSettingsPatch(BaseModel):
    presets: list[dict[str, Any]] = Field(default_factory=list, min_length=1, max_length=5)


class ProfileConnectionInput(BaseModel):
    id: str | None = Field(default=None, max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    component: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=80)
    device_id: str = Field(default="device-1", max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    settings: dict[str, Any] = Field(default_factory=dict)


class ProfileDeviceInput(BaseModel):
    id: str = Field(max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channels: list[str] = Field(default_factory=list)
    connections: list[ProfileConnectionInput] | None = None
    devices: list[ProfileDeviceInput] = Field(default_factory=lambda: [ProfileDeviceInput(id="device-1", name="Основное устройство")], min_length=1)
    routing: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    channels: list[str] | None = None
    connections: list[ProfileConnectionInput] | None = None
    devices: list[ProfileDeviceInput] | None = Field(default=None, min_length=1)
    routing: dict[str, Any] | None = None


def run(*args: str, check: bool = False, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"Command failed: {' '.join(args)}"
        raise RuntimeError(message)
    return result


def systemctl_active(unit: str) -> bool:
    return run("systemctl", "is-active", "--quiet", unit).returncode == 0


def service_stably_active(unit: str, checks: int = 4, interval: float = 0.5) -> bool:
    """Reject transient systemd 'active' states from a process that immediately crashes."""
    for _ in range(checks):
        if not systemctl_active(unit):
            return False
        time.sleep(interval)
    return systemctl_active(unit)


def load_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError, TypeError):
        return fallback


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_action(action: str, message: str, state: str = "running", progress: int = 10) -> None:
    # FastAPI dispatches these sync endpoints to a threadpool, so a client can
    # poll get_action() from a separate request while a long install/profile
    # operation is still running on another worker thread.
    if state == "failed":
        logger.error("Mihomo action %s failed: %s", action, message)
        message = PUBLIC_COMMAND_ERROR
    atomic_json(ACTION_FILE, {
        "action": action,
        "message": message,
        "state": state,
        "progress": progress,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def get_action_payload() -> dict[str, Any]:
    return load_json(ACTION_FILE, {"action": "", "message": "", "state": "idle", "progress": 100})


def state() -> dict[str, Any]:
    value = load_json(STATE_FILE, {})
    if not isinstance(value, dict):
        value = {}
    modules = value.get("modules")
    if not isinstance(modules, dict):
        modules = {}
    for module_id in KNOWN_MODULES:
        modules.setdefault(module_id, False)
    value["modules"] = modules
    if any(bool(modules.get(module_id)) for module_id in TRANSPORTS):
        ensure_policy_settings()
    return value


def save_state(value: dict[str, Any]) -> None:
    atomic_json(STATE_FILE, value)


def normalize_profile(item: dict[str, Any]) -> dict[str, Any]:
    """Expose legacy profiles through the connection-instance model."""
    result = dict(item)
    connections = result.get("connections")
    if not isinstance(connections, list):
        credentials = result.get("credentials", {}) if isinstance(result.get("credentials"), dict) else {}
        connections = []
        for index, component in enumerate(result.get("channels", [])):
            if component not in TRANSPORTS:
                continue
            connections.append({
                "id": f"legacy-{index + 1}",
                "component": component,
                "name": manifest(component).get("name", component),
                "settings": {},
                "credential": credentials.get(component, {}),
            })
    result["connections"] = [entry for entry in connections if isinstance(entry, dict)]
    devices = result.get("devices")
    if not isinstance(devices, list) or not devices:
        devices = [{"id": "device-1", "name": "Основное устройство"}]
    result["devices"] = devices
    subscriptions = result.get("subscriptions")
    result["subscriptions"] = subscriptions if isinstance(subscriptions, dict) else {}
    default_device = str(devices[0].get("id", "device-1"))
    for connection in result["connections"]:
        connection.setdefault("device_id", default_device)
    sync_legacy_profile_fields(result)
    return result


def sync_legacy_profile_fields(item: dict[str, Any]) -> None:
    """Keep older stored profile formats operational during migration."""
    channels: list[str] = []
    credentials: dict[str, Any] = {}
    for connection in item.get("connections", []):
        component = str(connection.get("component", ""))
        if component not in TRANSPORTS:
            continue
        if component not in channels:
            channels.append(component)
            credentials[component] = connection.get("credential", {})
    item["channels"] = channels
    item["credentials"] = credentials


def profiles() -> list[dict[str, Any]]:
    value = load_json(PROFILE_FILE, [])
    return [normalize_profile(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def save_profiles(value: list[dict[str, Any]]) -> None:
    atomic_json(PROFILE_FILE, value)


def profile_response(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "subscriptions"}


def manifest(module_id: str) -> dict[str, Any]:
    if module_id not in KNOWN_MODULES:
        raise HTTPException(status_code=404, detail="Mihomo sub-module not found")
    path = SUBMODULE_ROOT / module_id / "manifest.json"
    value = load_json(path, {})
    if not isinstance(value, dict) or value.get("id") != module_id:
        raise HTTPException(status_code=500, detail=f"Invalid manifest for {module_id}")
    return value


def default_settings(module_id: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in manifest(module_id).get("settings", []):
        if isinstance(item, dict) and item.get("key"):
            values[str(item["key"])] = item.get("default")
    return values


def module_settings(module_id: str) -> dict[str, Any]:
    path = SETTINGS_ROOT / f"{module_id}.json"
    stored = load_json(path, {})
    if not isinstance(stored, dict):
        stored = {}
    return {**default_settings(module_id), **stored}


def option_value(option: Any) -> Any:
    # "select" options are normally plain strings, but fields like DNS
    # providers need a human label distinct from the stored value (a DoH
    # URL), so an option may also be {"value": ..., "label": ...}.
    return option.get("value", option) if isinstance(option, dict) else option


def validate_settings(module_id: str, values: dict[str, Any]) -> dict[str, Any]:
    definition = {
        str(item["key"]): item
        for item in manifest(module_id).get("settings", [])
        if isinstance(item, dict) and item.get("key")
    }
    current = module_settings(module_id)
    for key, raw in values.items():
        if key not in definition:
            raise HTTPException(status_code=422, detail=f"Unknown setting: {key}")
        item = definition[key]
        kind = item.get("type", "text")
        if kind == "number":
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} must be numeric")
            minimum = item.get("min")
            maximum = item.get("max")
            if minimum is not None and value < int(minimum):
                raise HTTPException(status_code=422, detail=f"{key} is below minimum")
            if maximum is not None and value > int(maximum):
                raise HTTPException(status_code=422, detail=f"{key} is above maximum")
            current[key] = value
        elif kind == "select":
            options = [option_value(option) for option in item.get("options", [])]
            if raw not in options:
                raise HTTPException(status_code=422, detail=f"Unsupported value for {key}")
            current[key] = raw
        else:
            value = str(raw).strip()
            if not value:
                raise HTTPException(status_code=422, detail=f"{key} cannot be empty")
            current[key] = value

    if module_id in ("transport-wg", "transport-awg"):
        try:
            network = ipaddress.ip_network(str(current["subnet"]), strict=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid subnet: {exc}") from exc
        if network.version != 4 or network.prefixlen > 28:
            raise HTTPException(status_code=422, detail="Use an IPv4 subnet /28 or larger")
    elif module_id == "transport-reality":
        if int(current.get("port_start", 0)) == int(current.get("cdn_port_start", 0)):
            raise HTTPException(status_code=422, detail="Direct and CDN port ranges must start at different ports")
        dns_servers = [value.strip() for value in str(current.get("dns", "")).split(",") if value.strip()]
        if not dns_servers:
            raise HTTPException(status_code=422, detail="At least one Xray DNS server is required")
    return current


def routing_schema() -> list[dict[str, Any]]:
    path = SUBMODULE_ROOT / ROUTING_MODULE_ID / "manifest.json"
    value = load_json(path, {})
    settings = value.get("settings", []) if isinstance(value, dict) else []
    return settings if isinstance(settings, list) else []


def routing_defaults() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in routing_schema():
        if isinstance(item, dict) and item.get("key"):
            values[str(item["key"])] = item.get("default")
    return values


def validate_routing(values: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    definition = {
        str(item["key"]): item
        for item in routing_schema()
        if isinstance(item, dict) and item.get("key")
    }
    result = dict(current) if current is not None else routing_defaults()
    for key, raw in values.items():
        if key not in definition:
            raise HTTPException(status_code=422, detail=f"Unknown routing setting: {key}")
        item = definition[key]
        kind = item.get("type", "text")
        if kind == "number":
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{key} must be numeric")
            minimum = item.get("min")
            maximum = item.get("max")
            if minimum is not None and value < int(minimum):
                raise HTTPException(status_code=422, detail=f"{key} is below minimum")
            if maximum is not None and value > int(maximum):
                raise HTTPException(status_code=422, detail=f"{key} is above maximum")
            result[key] = value
        elif kind == "select":
            options = [option_value(option) for option in item.get("options", [])]
            if raw not in options:
                raise HTTPException(status_code=422, detail=f"Unsupported value for {key}")
            result[key] = raw
        else:
            # Unlike module settings, routing text fields (e.g. "rules") are
            # allowed to be empty: a profile without extra rules is valid.
            result[key] = str(raw)
    return result


DNS_SETTINGS_FILE = SETTINGS_ROOT / f"{DNS_MODULE_ID}.json"
ROUTING_SETTINGS_FILE = SETTINGS_ROOT / f"{ROUTING_MODULE_ID}.json"
PRESET_SETTINGS_FILE = SETTINGS_ROOT / "profile-presets.json"


def default_profile_presets() -> list[dict[str, Any]]:
    return [
        {"id": "reliable", "name": "Надёжный", "description": "Direct VLESS, CDN VLESS, AWG и SS", "strategy": "fallback", "components": [{"id": "transport-reality", "label": "VLESS · Direct"}, {"id": "transport-reality", "cdn": True, "label": "VLESS · CDN"}, {"id": "transport-awg"}, {"id": "transport-shadowsocks"}]},
        {"id": "direct-fallback", "name": "Direct + резерв", "description": "Основной и резервный транспорт", "strategy": "fallback", "components": [{"id": "transport-reality"}, {"id": "transport-awg"}]},
        {"id": "cdn-first", "name": "CDN-first", "description": "VLESS через CDN с резервом", "strategy": "fallback", "components": [{"id": "transport-reality", "cdn": True}, {"id": "transport-awg"}]},
        {"id": "low-latency", "name": "Минимальная задержка", "description": "AWG с резервным Shadowsocks", "strategy": "url-test", "components": [{"id": "transport-awg"}, {"id": "transport-shadowsocks"}]},
        {"id": "all", "name": "Все транспортные каналы", "description": "Все доступные компоненты", "strategy": "select", "components": [{"id": "transport-reality"}, {"id": "transport-awg"}, {"id": "transport-wg"}, {"id": "transport-shadowsocks"}]},
    ]


def validate_profile_presets(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, value in enumerate(values[:5]):
        preset_id = str(value.get("id") or f"preset-{index + 1}")
        if not re.fullmatch(r"[a-z0-9-]{1,32}", preset_id) or preset_id in used_ids:
            raise HTTPException(status_code=422, detail="Preset id is invalid or duplicated")
        used_ids.add(preset_id)
        strategy = str(value.get("strategy", "fallback"))
        if strategy not in {"fallback", "url-test", "select"}:
            raise HTTPException(status_code=422, detail=f"Unsupported strategy for preset {preset_id}")
        components: list[dict[str, Any]] = []
        used_singletons: set[str] = set()
        for component in value.get("components", []):
            component_id = str(component.get("id", ""))
            if component_id not in TRANSPORTS:
                raise HTTPException(status_code=422, detail=f"Unknown component in preset {preset_id}")
            if component_id != "transport-reality" and component_id in used_singletons:
                continue
            used_singletons.add(component_id)
            components.append({"id": component_id, "cdn": bool(component.get("cdn", False)), "label": str(component.get("label", ""))[:80]})
        if not components:
            raise HTTPException(status_code=422, detail=f"Preset {preset_id} has no connections")
        result.append({"id": preset_id, "name": str(value.get("name") or f"Preset {index + 1}")[:80], "description": str(value.get("description", ""))[:160], "strategy": strategy, "components": components})
    return result


def profile_presets() -> list[dict[str, Any]]:
    stored = load_json(PRESET_SETTINGS_FILE, None)
    return validate_profile_presets(stored) if isinstance(stored, list) and stored else default_profile_presets()


def dns_provider_options() -> list[dict[str, str]]:
    return [{"value": item["server"], "label": item["name"]} for item in DNS_PROVIDERS]


def dns_schema() -> list[dict[str, Any]]:
    path = SUBMODULE_ROOT / DNS_MODULE_ID / "manifest.json"
    value = load_json(path, {})
    settings = value.get("settings", []) if isinstance(value, dict) else []
    settings = settings if isinstance(settings, list) else []
    options = dns_provider_options()
    result: list[dict[str, Any]] = []
    for item in settings:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        if item.get("key") in ("nameserver", "fallback"):
            item["options"] = options
        result.append(item)
    return result


def dns_defaults() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in dns_schema():
        if isinstance(item, dict) and item.get("key"):
            values[str(item["key"])] = item.get("default")
    return values


def dns_settings() -> dict[str, Any]:
    stored = load_json(DNS_SETTINGS_FILE, {})
    stored = stored if isinstance(stored, dict) else {}
    return {**dns_defaults(), **stored}


def routing_settings() -> dict[str, Any]:
    stored = load_json(ROUTING_SETTINGS_FILE, {})
    stored = stored if isinstance(stored, dict) else {}
    return {**routing_defaults(), **stored}


def ensure_policy_settings() -> None:
    SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    if not DNS_SETTINGS_FILE.exists():
        atomic_json(DNS_SETTINGS_FILE, dns_defaults())
    if not ROUTING_SETTINGS_FILE.exists():
        atomic_json(ROUTING_SETTINGS_FILE, routing_defaults())
    if not PRESET_SETTINGS_FILE.exists():
        atomic_json(PRESET_SETTINGS_FILE, default_profile_presets())


def validate_dns(values: dict[str, Any]) -> dict[str, Any]:
    definition = {
        str(item["key"]): item
        for item in dns_schema()
        if isinstance(item, dict) and item.get("key")
    }
    result = dns_settings()
    for key, raw in values.items():
        if key not in definition:
            raise HTTPException(status_code=422, detail=f"Unknown DNS setting: {key}")
        item = definition[key]
        if item.get("type", "text") == "select":
            options = [option_value(option) for option in item.get("options", [])]
            if raw not in options:
                raise HTTPException(status_code=422, detail=f"Unsupported value for {key}")
            result[key] = raw
        else:
            value = str(raw).strip()
            if not value:
                raise HTTPException(status_code=422, detail=f"{key} cannot be empty")
            result[key] = value
    return result


def auth_required(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="Invalid authorization")
    expected_user = os.getenv("ADMIN_USER", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "")
    if not expected_password or not (
        hmac.compare_digest(username, expected_user)
        and hmac.compare_digest(password, expected_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/api/mihomo/action", dependencies=[Depends(auth_required)])
def get_action() -> dict[str, Any]:
    return get_action_payload()


@app.get("/api/mihomo/routing/schema", dependencies=[Depends(auth_required)])
def get_routing_schema() -> dict[str, Any]:
    return {"schema": routing_schema(), "values": routing_settings(), "presets": profile_presets()}


@app.patch("/api/mihomo/routing/presets", dependencies=[Depends(auth_required)])
def patch_profile_presets(patch: PresetSettingsPatch) -> dict[str, Any]:
    next_presets = validate_profile_presets(patch.presets)
    atomic_json(PRESET_SETTINGS_FILE, next_presets)
    return {"presets": next_presets}


@app.patch("/api/mihomo/routing/settings", dependencies=[Depends(auth_required)])
def patch_routing_settings(patch: ModuleSettingsPatch) -> dict[str, Any]:
    next_values = validate_routing(patch.values, current=routing_settings())
    SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_json(ROUTING_SETTINGS_FILE, next_values)
    return {"schema": routing_schema(), "values": next_values}


@app.get("/api/mihomo/dns/settings", dependencies=[Depends(auth_required)])
def get_dns_settings() -> dict[str, Any]:
    return {"schema": dns_schema(), "values": dns_settings()}


@app.patch("/api/mihomo/dns/settings", dependencies=[Depends(auth_required)])
def patch_dns_settings(patch: ModuleSettingsPatch) -> dict[str, Any]:
    next_values = validate_dns(patch.values)
    SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_json(DNS_SETTINGS_FILE, next_values)
    return {"schema": dns_schema(), "values": next_values}


def public_endpoint() -> str:
    for key in ("PUBLIC_ENDPOINT", "PUBLIC_IPV4", "PUBLIC_IP"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return "SERVER_IP"


def module_is_installed(module_id: str) -> bool:
    enabled = bool(state()["modules"].get(module_id))
    service = SERVICE_BY_MODULE.get(module_id)
    if service:
        return enabled and systemctl_active(service)
    return enabled


def apt_package_versions(package: str) -> tuple[str, str]:
    """Return (installed, candidate) versions for an apt package. Mirrors
    api/main.py's helper of the same name (separate process, no shared
    import)."""
    # LC_ALL=C: a non-English locale (this server runs ru_RU) makes
    # apt-cache policy print "Установлен:"/"Кандидат:" instead of
    # "Installed:"/"Candidate:", which the parsing below would silently
    # never match, leaving both versions permanently blank.
    output = run("env", "LC_ALL=C", "apt-cache", "policy", package).stdout
    installed = candidate = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Installed:"):
            installed = line.split(":", 1)[1].strip()
        elif line.startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
    return "" if installed == "(none)" else installed, "" if candidate == "(none)" else candidate


def awg_packages_current() -> bool:
    for package in ("amneziawg", "amneziawg-tools", "amneziawg-dkms"):
        installed, candidate = apt_package_versions(package)
        if not installed or not candidate or installed != candidate:
            return False
    return True


def version_major(value: str) -> str:
    match = re.match(r"\d+", value.split(":")[-1])
    return match.group(0) if match else ""


def xray_installed_version(binary: Path) -> str:
    if not binary.is_file():
        return ""
    output = run(str(binary), "version").stdout
    match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", output)
    return match.group(1) if match else ""


def awg_installed_version() -> str:
    module = run("modinfo", "-F", "version", "amneziawg", check=False).stdout
    match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", module)
    if match:
        return match.group(1)
    if not shutil.which("awg"):
        return ""
    output = run("awg", "--version").stdout
    match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", output)
    return match.group(1) if match else ""


def github_latest_tag(repo: str) -> str:
    cached = github_release_cache.get(repo)
    if cached and time.time() - cached["_cached_at"] < 3600:
        return cached["tag"]
    if not github_release_lock.acquire(blocking=False):
        return cached["tag"] if cached else ""
    try:
        output = run(
            "curl", "--silent", "--show-error", "--connect-timeout", "3", "--max-time", "6",
            f"https://api.github.com/repos/{repo}/releases/latest",
        ).stdout
        tag = ""
        try:
            tag = str(json.loads(output).get("tag_name", "")).lstrip("v")
        except (ValueError, json.JSONDecodeError):
            pass
        if tag:
            github_release_cache[repo] = {"tag": tag, "_cached_at": time.time()}
            return tag
        latest_url = run(
            "curl", "--silent", "--show-error", "--location", "--connect-timeout", "3", "--max-time", "8",
            "--output", "/dev/null", "--write-out", "%{url_effective}",
            f"https://github.com/{repo}/releases/latest",
        ).stdout
        fallback_tag = latest_url.rstrip("/").rsplit("/", 1)[-1].lstrip("v")
        if re.fullmatch(r"[0-9][0-9A-Za-z._-]*", fallback_tag):
            github_release_cache[repo] = {"tag": fallback_tag, "_cached_at": time.time()}
            return fallback_tag
        return cached["tag"] if cached else ""
    finally:
        github_release_lock.release()


def module_version_info(module_id: str, info: dict[str, Any], installed: bool) -> dict[str, Any]:
    package = str(info.get("package", ""))
    update_available_override: bool | None = None
    if module_id == "transport-awg":
        installed_package, candidate_package = apt_package_versions(package)
        installed_version = awg_installed_version() if installed else ""
        available_version = ""
        update_available_override = bool(installed and not awg_packages_current())
    elif package:
        installed_version, available_version = apt_package_versions(package)
        if not installed:
            installed_version = ""
    elif module_id == "transport-reality":
        installed_version = xray_installed_version(REALITY_XRAY_BIN) if installed else ""
        available_version = github_latest_tag(XRAY_GITHUB_REPO)
    else:
        installed_version = available_version = ""
    update_available = update_available_override if update_available_override is not None else bool(installed_version and available_version and installed_version != available_version)
    update_breaking = update_available and version_major(installed_version) != version_major(available_version)
    return {
        "installed_version": installed_version,
        "available_version": available_version,
        "update_available": update_available,
        "update_breaking": update_breaking,
    }


def module_payload(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    settings = module_settings(module_id)
    installed = module_is_installed(module_id)
    service = SERVICE_BY_MODULE.get(module_id)
    return {
        **info,
        "installable": info.get("installable", True) is True,
        "installed": installed,
        "active": systemctl_active(service) if service else installed,
        "service": service or "",
        "settings_values": settings,
        **module_version_info(module_id, info, installed),
    }


def preflight_module(info: dict[str, Any]) -> None:
    os_release: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip().strip('"')
    except OSError as exc:
        raise RuntimeError("Unable to identify the server operating system") from exc

    supported = info.get("supported_os", [])
    if os_release.get("ID") not in supported:
        raise RuntimeError(f"Module does not support OS {os_release.get('ID', 'unknown')}")

    minimum_free_mb = int(info.get("minimum_free_mb", 128))
    free_mb = shutil.disk_usage("/opt").free // (1024 * 1024)
    if free_mb < minimum_free_mb:
        raise RuntimeError(f"Not enough free space: {free_mb} MB available, {minimum_free_mb} MB required")

    audit = run("dpkg", "--audit")
    if audit.stdout.strip() or audit.returncode:
        raise RuntimeError("dpkg has an unfinished operation; repair the package manager before installing modules")
    apt_check = run("apt-get", "-o", "DPkg::Lock::Timeout=300", "check")
    if apt_check.returncode:
        raise RuntimeError(apt_check.stderr.strip() or "APT package check failed")

    for package in info.get("preflight_packages", []):
        if not isinstance(package, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", package):
            raise RuntimeError("Module manifest contains an invalid package requirement")
        installed = run("dpkg-query", "-W", "-f=${db:Status-Status}", package).stdout.strip() == "installed"
        available = bool(run("apt-cache", "show", package).stdout.strip())
        if not installed and not available:
            raise RuntimeError(f"Required package {package} is unavailable in configured APT repositories")


def call_module_script(module_id: str, action: str, extra_env: dict[str, str] | None = None) -> None:
    script = SUBMODULE_ROOT / module_id / f"{action}.sh"
    if not script.is_file():
        raise RuntimeError(f"Missing {script.name} for {module_id}")
    env = os.environ.copy()
    env["MIHOMO_SETTINGS_FILE"] = str(SETTINGS_ROOT / f"{module_id}.json")
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{module_id}: {action} failed")


def core_status() -> dict[str, Any]:
    module_state = state()
    installed_modules = [module_id for module_id in KNOWN_MODULES if module_is_installed(module_id)]
    profile_items = profiles()
    credentials_count = sum(len(profile.get("connections", [])) for profile in profile_items)
    profiles_in_use = sum(
        1
        for profile in profile_items
        if profile.get("connections")
    )
    channels_in_use = sorted({
        str(channel)
        for profile in profile_items
        for connection in profile.get("connections", [])
        for channel in [str(connection.get("component", ""))]
        if channel in TRANSPORTS
    })
    core_version = ""
    core_build = ""
    if CORE_BIN.is_file():
        result = run(str(CORE_BIN), "-v")
        core_build = (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 else ""
        match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", core_build)
        core_version = match.group(1) if match else core_build
    core_available_version = github_latest_tag(MIHOMO_GITHUB_REPO)
    return {
        "id": "mihomo",
        "name": "Mihomo",
        "active": systemctl_active("vps-control-mihomo-manager.service"),
        "installed": True,
        "version": core_version,
        "core_version": core_version,
        "core_build": core_build,
        "core_available_version": core_available_version,
        "core_update_via_release": False,
        "modules_installed": len(installed_modules),
        "modules_total": len(KNOWN_MODULES),
        "profiles": len(profile_items),
        "profiles_in_use": profiles_in_use,
        "credentials": credentials_count,
        "channels_in_use": channels_in_use,
        "channels_installed": len([item for item in TRANSPORTS if module_is_installed(item)]),
        "endpoint": public_endpoint(),
        "updated_at": module_state.get("updated_at", ""),
    }


@app.get("/api/protocols/mihomo/status", dependencies=[Depends(auth_required)])
def stable_protocol_status() -> dict[str, Any]:
    status = core_status()
    return {
        "protocol": "mihomo",
        "active": status["active"],
        "interface": "",
        "port": 0,
        "editable_settings": [],
        "diagnostics": {
            "state": "healthy" if status["active"] else "unavailable",
            "summary": "Mihomo Manager работает" if status["active"] else "Mihomo Manager остановлен",
        },
        "resources": {},
    }


@app.get("/api/mihomo/status", dependencies=[Depends(auth_required)])
def get_status() -> dict[str, Any]:
    return core_status()


@app.get("/api/mihomo/modules", dependencies=[Depends(auth_required)])
def get_modules() -> dict[str, Any]:
    order = [
        "transport-awg",
        "transport-wg",
        "transport-reality",
        "transport-shadowsocks",
    ]
    return {"items": [module_payload(module_id) for module_id in order]}


@app.get("/api/mihomo/modules/{module_id}/settings", dependencies=[Depends(auth_required)])
def get_module_settings(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    return {
        "id": module_id,
        "schema": info.get("settings", []),
        "values": module_settings(module_id),
        "installed": module_is_installed(module_id),
    }


@app.patch("/api/mihomo/modules/{module_id}/settings", dependencies=[Depends(auth_required)])
def patch_module_settings(module_id: str, patch: ModuleSettingsPatch) -> dict[str, Any]:
    previous_values = module_settings(module_id)
    next_values = validate_settings(module_id, patch.values)
    SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_json(SETTINGS_ROOT / f"{module_id}.json", next_values)
    if module_is_installed(module_id) and module_id in TRANSPORTS:
        write_action(f"module-settings:{module_id}", f"Применение настроек {manifest(module_id)['name']}…")
        try:
            call_module_script(module_id, "install")
        except RuntimeError as exc:
            # Keep persisted settings and the generated runtime configuration
            # in sync. Re-applying the previous validated settings also rolls
            # back a candidate that passed static validation but failed its
            # runtime probe or service restart.
            atomic_json(SETTINGS_ROOT / f"{module_id}.json", previous_values)
            try:
                call_module_script(module_id, "install")
            except RuntimeError as rollback_exc:
                exc = RuntimeError(f"{exc}; rollback failed: {rollback_exc}")
            write_action(f"module-settings:{module_id}", str(exc), state="failed", progress=100)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        write_action(f"module-settings:{module_id}", f"Настройки {manifest(module_id)['name']} применены", state="done", progress=100)
    return get_module_settings(module_id)


@app.post("/api/mihomo/modules/{module_id}/install", dependencies=[Depends(auth_required)])
def install_module(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    if info.get("installable", True) is not True:
        raise HTTPException(status_code=409, detail="Module is not available for installation")
    if module_is_installed(module_id):
        return module_payload(module_id)
    try:
        preflight_module(info)
    except RuntimeError as exc:
        write_action(f"module-install:{module_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_action(f"module-install:{module_id}", f"Установка {info['name']}…", progress=10)
    # Create defaults before the installer so the first install is deterministic.
    settings_path = SETTINGS_ROOT / f"{module_id}.json"
    if not settings_path.exists():
        atomic_json(settings_path, default_settings(module_id))
    try:
        call_module_script(module_id, "install")
        write_action(f"module-install:{module_id}", f"Проверка службы {info['name']}…", progress=70)
        service = SERVICE_BY_MODULE.get(module_id)
        if service and not service_stably_active(service):
            status = run("systemctl", "status", service, "--no-pager", "-l")
            detail = (status.stdout or status.stderr).strip()
            raise RuntimeError(
                f"{manifest(module_id)['name']} не подтвердил рабочее состояние systemd"
                + (f"\n{detail}" if detail else "")
            )
        ensure_policy_settings()
    except RuntimeError as exc:
        write_action(f"module-install:{module_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    value = state()
    value["modules"][module_id] = True
    value["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(value)
    write_action(f"module-install:{module_id}", f"{info['name']} установлен", state="done", progress=100)
    return module_payload(module_id)


@app.delete("/api/mihomo/modules/{module_id}", dependencies=[Depends(auth_required)])
def remove_module(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    in_use = [
        profile["name"]
        for profile in profiles()
        if any(connection.get("component") == module_id for connection in profile.get("connections", []))
    ]
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Модуль используется профилями: {', '.join(in_use)}",
        )
    write_action(f"module-remove:{module_id}", f"Удаление {info['name']}…", progress=10)
    try:
        call_module_script(module_id, "uninstall")
    except RuntimeError as exc:
        write_action(f"module-remove:{module_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    value = state()
    value["modules"][module_id] = False
    value["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(value)
    write_action(f"module-remove:{module_id}", f"{info['name']} удалён", state="done", progress=100)
    return module_payload(module_id)


@app.post("/api/mihomo/modules/{module_id}/update", dependencies=[Depends(auth_required)])
def update_module(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    if not module_is_installed(module_id):
        raise HTTPException(status_code=409, detail="Module is not installed")
    if not module_payload(module_id).get("update_available"):
        raise HTTPException(status_code=409, detail="No update available")
    write_action(f"module-update:{module_id}", f"Обновление {info['name']}…", progress=10)
    package = str(info.get("package", ""))
    try:
        if module_id == "transport-awg":
            run("apt-get", "-o", "DPkg::Lock::Timeout=300", "update", check=True)
            run("apt-get", "-o", "DPkg::Lock::Timeout=300", "install", "--only-upgrade", "--allow-change-held-packages", "-y", "amneziawg", "amneziawg-tools", "amneziawg-dkms", check=True)
            if not awg_packages_current():
                raise RuntimeError("AmneziaWG packages did not reach repository candidates")
        elif package:
            # Package upgrade only, same as the direct protocol's update:
            # the running tunnel/service is left untouched so live
            # connections aren't dropped by an unattended restart.
            run("apt-get", "-o", "DPkg::Lock::Timeout=300", "update", check=True)
            run("apt-get", "-o", "DPkg::Lock::Timeout=300", "install", "--only-upgrade", "-y", package, check=True)
        else:
            # Self-fetching module (transport-reality's bundled Xray): its
            # installer already downloads and verifies the latest official
            # release. XRAY_UPDATE_ONLY makes it stop right after swapping
            # the binary in, skipping the TLS probe/config/systemd-restart
            # steps so the running service and its live profile connections
            # aren't touched by this update.
            call_module_script(module_id, "install", extra_env={"XRAY_UPDATE_ONLY": "1"})
    except RuntimeError as exc:
        write_action(f"module-update:{module_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    write_action(f"module-update:{module_id}", f"{info['name']} обновлён", state="done", progress=100)
    return module_payload(module_id)


def next_tunnel_address(module_id: str) -> tuple[str, str]:
    settings = module_settings(module_id)
    network = ipaddress.ip_network(str(settings["subnet"]))
    used = set()
    for profile in profiles():
        for connection in profile.get("connections", []):
            credential = connection.get("credential", {})
            if connection.get("component") == module_id and credential.get("ip"):
                used.add(str(credential["ip"]).split("/", 1)[0])
    hosts = list(network.hosts())
    if len(hosts) < 3:
        raise RuntimeError("Mihomo subnet is too small")
    server = hosts[0]
    for host in hosts[1:]:
        if str(host) not in used:
            return str(host), f"{server}/{network.prefixlen}"
    raise RuntimeError(f"No free addresses in {network}")


def keypair(tool: str) -> tuple[str, str]:
    private = run(tool, "genkey", check=True).stdout.strip()
    public = run(tool, "pubkey", check=True, input_text=private + "\n").stdout.strip()
    return private, public


def interface_private_key(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("PrivateKey"):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"PrivateKey missing in {path}")


def add_wg_credential(profile_id: str, module_id: str, connection_id: str = "default") -> dict[str, Any]:
    if module_id == "transport-wg":
        tool, interface = "wg", "mh-wg0"
        config = Path("/etc/wireguard/mh-wg0.conf")
    else:
        tool, interface = "awg", "mh-awg0"
        config = Path("/etc/amnezia/amneziawg/mh-awg0.conf")
    if not config.is_file():
        raise RuntimeError(f"{module_id} configuration is missing")
    client_ip, _ = next_tunnel_address(module_id)
    private, public = keypair(tool)
    server_public = run(tool, "pubkey", check=True, input_text=interface_private_key(config) + "\n").stdout.strip()
    prefix = ipaddress.ip_network(str(module_settings(module_id)["subnet"])).prefixlen
    with config.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n# mihomo-profile:{profile_id}:{connection_id}\n"
            f"[Peer]\nPublicKey = {public}\nAllowedIPs = {client_ip}/32\n"
        )
    run(tool, "set", interface, "peer", public, "allowed-ips", f"{client_ip}/32", check=True)
    payload = {
        "private_key": private,
        "public_key": public,
        "server_public_key": server_public,
        "ip": f"{client_ip}/{prefix}",
        "port": int(module_settings(module_id)["port"]),
        "mtu": int(module_settings(module_id)["mtu"]),
        "marker": f"{profile_id}:{connection_id}",
    }
    if module_id == "transport-awg":
        settings = module_settings(module_id)
        payload["amnezia"] = {key: int(settings[key]) for key in ("jc", "jmin", "jmax", "s1", "s2", "h1", "h2", "h3", "h4")}
    return payload


def remove_wg_credential(profile_id: str, module_id: str, credential: dict[str, Any]) -> None:
    if module_id == "transport-wg":
        tool, interface, config = "wg", "mh-wg0", Path("/etc/wireguard/mh-wg0.conf")
    else:
        tool, interface, config = "awg", "mh-awg0", Path("/etc/amnezia/amneziawg/mh-awg0.conf")
    public = str(credential.get("public_key", ""))
    if public:
        run(tool, "set", interface, "peer", public, "remove")
    if config.is_file():
        lines = config.read_text(encoding="utf-8").splitlines()
        marker = f"# mihomo-profile:{credential.get('marker') or profile_id}"
        output: list[str] = []
        skipping = False
        for line in lines:
            if line.strip() == marker:
                skipping = True
                continue
            if skipping and line.startswith("# mihomo-profile:"):
                skipping = False
            if not skipping:
                output.append(line)
        config.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        os.chmod(config, 0o600)


def used_ss_ports() -> set[int]:
    result: set[int] = set()
    for profile in profiles():
        for connection in profile.get("connections", []):
            if connection.get("component") == "transport-shadowsocks":
                try:
                    result.add(int(connection.get("credential", {}).get("port", 0)))
                except (TypeError, ValueError):
                    pass
    return result


def add_ss_credential(profile_id: str, connection_id: str = "default") -> dict[str, Any]:
    settings = module_settings("transport-shadowsocks")
    start = int(settings["port_start"])
    used = used_ss_ports()
    port = next((candidate for candidate in range(start, min(start + 2000, 65536)) if candidate not in used), None)
    if port is None:
        raise RuntimeError("No free Mihomo Shadowsocks ports")
    password = secrets.token_urlsafe(24)
    config_dir = CONFIG_ROOT / "shadowsocks"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "server": ["0.0.0.0", "::0"],
        "server_port": port,
        "password": password,
        "method": settings["method"],
        "timeout": 300,
        "mode": "tcp_and_udp",
        "mtu": 1200,
        "no_delay": True,
    }
    instance_id = f"{profile_id}-{connection_id}"
    atomic_json(config_dir / f"{instance_id}.json", config)
    run("systemctl", "enable", "--now", f"vps-control-mihomo-ss@{instance_id}.service", check=True)
    if shutil.which("ufw") and run("ufw", "status").stdout.startswith("Status: active"):
        run("ufw", "allow", f"{port}/tcp")
        run("ufw", "allow", f"{port}/udp")
    return {"port": port, "password": password, "method": settings["method"], "instance_id": instance_id}


def remove_ss_credential(profile_id: str, credential: dict[str, Any]) -> None:
    instance_id = str(credential.get("instance_id") or profile_id)
    run("systemctl", "disable", "--now", f"vps-control-mihomo-ss@{instance_id}.service")
    path = CONFIG_ROOT / "shadowsocks" / f"{instance_id}.json"
    path.unlink(missing_ok=True)
    port = credential.get("port")
    if port and shutil.which("ufw"):
        run("ufw", "delete", "allow", f"{port}/tcp")
        run("ufw", "delete", "allow", f"{port}/udp")


def reality_env() -> dict[str, str]:
    result: dict[str, str] = {}
    path = CONFIG_ROOT / "reality" / "reality.env"
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def reality_inbound(config: dict[str, Any]) -> dict[str, Any]:
    # The config also carries a loopback "api" inbound for the Stats API
    # (see transport-reality/install.sh); find the client inbound by tag
    # instead of assuming it is inbounds[0].
    for inbound in config.get("inbounds", []):
        if isinstance(inbound, dict) and inbound.get("tag") == "mihomo-reality":
            return inbound
    raise KeyError("mihomo-reality inbound missing")


def reality_connection_settings() -> dict[str, Any]:
    """Read the effective VLESS client parameters from the live Xray config.

    Profiles keep only identity-bound credentials. Transport settings can be
    changed later, so exported Mihomo configs must not rely on a stale copy
    captured when the profile was created.
    """
    config = load_json(CONFIG_ROOT / "reality" / "config.json", {})
    env = reality_env()
    try:
        stream = reality_inbound(config)["streamSettings"]
    except (TypeError, KeyError, IndexError):
        stream = {}
    transport = str(stream.get("network", env.get("TRANSPORT", "xhttp")))
    result: dict[str, Any] = {
        "port": int(env.get("PORT", module_settings("transport-reality")["port"])),
        "public_key": env.get("PUBLIC_KEY", ""),
        "short_id": env.get("SHORT_ID", ""),
        "servername": env.get("TARGET", "www.intel.com:443").rsplit(":", 1)[0],
        "transport": transport,
    }
    if transport == "xhttp":
        xhttp = stream.get("xhttpSettings", {})
        result.update({"path": xhttp.get("path", env.get("TRANSPORT_PATH", env.get("XHTTP_PATH", "/"))), "xhttp_mode": xhttp.get("mode", "auto")})
    elif transport == "grpc":
        grpc = stream.get("grpcSettings", {})
        result["path"] = "/" + str(grpc.get("serviceName", env.get("TRANSPORT_PATH", "/vless")).lstrip("/"))
    else:
        result["path"] = "/"
    return result


def connection_defaults(component: str) -> dict[str, Any]:
    return {
        str(field["key"]): field.get("default")
        for field in manifest(component).get("connection_settings", [])
        if isinstance(field, dict) and field.get("key")
    }


def validate_connection(component: str, values: dict[str, Any]) -> dict[str, Any]:
    if component not in TRANSPORTS:
        raise HTTPException(status_code=422, detail=f"{component} is not a Mihomo component")
    if not module_is_installed(component):
        raise HTTPException(status_code=409, detail=f"Сначала установите компонент {manifest(component)['name']}")
    result = {**connection_defaults(component), **values}
    if component != "transport-reality":
        return result
    try:
        port = int(result.get("port", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="VLESS port must be numeric") from exc
    if not 0 <= port <= 65535 or 0 < port < 1024:
        raise HTTPException(status_code=422, detail="VLESS port must be 0 or between 1024 and 65535")
    target = str(result.get("target", ""))
    target_match = re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", target)
    if not target_match or not 1 <= int(target_match.group(2)) <= 65535:
        raise HTTPException(status_code=422, detail="REALITY target must be hostname:port")
    transport = str(result.get("transport", "xhttp"))
    if transport not in {"xhttp", "raw", "grpc"}:
        raise HTTPException(status_code=422, detail="Unsupported VLESS transport")
    path = str(result.get("transport_path", "/vless"))
    if not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*", path):
        raise HTTPException(status_code=422, detail="Transport path must start with /")
    mode = str(result.get("xhttp_mode", "auto"))
    if mode not in {"auto", "stream-one", "stream-up", "packet-up"}:
        raise HTTPException(status_code=422, detail="Unsupported XHTTP mode")
    padding = str(result.get("xpadding", "100-1000"))
    padding_match = re.fullmatch(r"(\d+)(?:-(\d+))?", padding)
    if not padding_match or (padding_match.group(2) and int(padding_match.group(1)) > int(padding_match.group(2))):
        raise HTTPException(status_code=422, detail="Invalid XHTTP padding")
    concurrency = int(result.get("xmux_concurrency", 12))
    if not 1 <= concurrency <= 64:
        raise HTTPException(status_code=422, detail="XHTTP concurrency must be between 1 and 64")
    cdn_enabled = bool(result.get("cdn_enabled", False))
    cdn_domain = str(result.get("cdn_domain", "")).strip().lower()
    cdn_transport = str(result.get("cdn_transport", "websocket"))
    if cdn_transport not in {"websocket", "xhttp", "grpc"}:
        raise HTTPException(status_code=422, detail="Unsupported CDN transport")
    cdn_xhttp_mode = str(result.get("cdn_xhttp_mode", "auto"))
    if cdn_xhttp_mode not in {"auto", "stream-one", "stream-up", "packet-up"}:
        raise HTTPException(status_code=422, detail="Unsupported CDN XHTTP mode")
    if cdn_enabled and not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", cdn_domain):
        raise HTTPException(status_code=422, detail="For CDN specify a valid hostname")
    result.update({"port": port, "target": target, "transport": transport, "transport_path": path, "xhttp_mode": mode, "xpadding": padding, "xmux_concurrency": concurrency, "cdn_enabled": cdn_enabled, "cdn_domain": cdn_domain, "cdn_transport": cdn_transport, "cdn_xhttp_mode": cdn_xhttp_mode})
    return result


def used_vless_ports(config: dict[str, Any]) -> set[int]:
    return {int(item.get("port", 0)) for item in config.get("inbounds", []) if isinstance(item, dict) and int(item.get("port", 0) or 0) > 0}


def next_vless_port(config: dict[str, Any], start: int) -> int:
    used = used_vless_ports(config)
    for port in range(start, min(start + 4000, 65536)):
        if port not in used:
            return port
    raise RuntimeError("No free VLESS ports in the configured range")


def vless_stream(settings: dict[str, Any], private_key: str, short_id: str) -> dict[str, Any]:
    transport = str(settings["transport"])
    path = str(settings["transport_path"])
    stream: dict[str, Any] = {"network": transport, "security": "reality"}
    if transport == "xhttp":
        stream["xhttpSettings"] = {"path": path, "mode": settings["xhttp_mode"], "extra": {"xPaddingBytes": settings["xpadding"], "xmux": {"maxConcurrency": str(settings["xmux_concurrency"]), "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000"}}}
    elif transport == "grpc":
        stream["grpcSettings"] = {"serviceName": path.lstrip("/") or "vless", "multiMode": False}
    else:
        stream["rawSettings"] = {"header": {"type": "none"}}
    host = str(settings["target"]).rsplit(":", 1)[0]
    stream["realitySettings"] = {"show": False, "target": settings["target"], "xver": 0, "serverNames": [host], "privateKey": private_key, "shortIds": [short_id]}
    return stream


def write_mihomo_vless_cdn(connection_id: str, enabled: bool, domain: str, path: str, port: int, transport: str = "websocket") -> None:
    VLESS_CDN_ROUTE_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor = VLESS_CDN_ROUTE_ROOT / f"{connection_id}.json"
    if enabled:
        atomic_json(descriptor, {"domain": domain, "path": path, "port": int(port), "transport": transport}, mode=0o600)
    else:
        descriptor.unlink(missing_ok=True)
    rebuild_vless_cdn_snippet()


def rebuild_vless_cdn_snippet() -> None:
    routes: list[dict[str, Any]] = []
    direct = {}
    try:
        direct = dict(line.split("=", 1) for line in DIRECT_VLESS_ENV.read_text(encoding="utf-8").splitlines() if "=" in line)
    except OSError:
        pass
    if direct.get("CDN_ENABLED") == "yes" and direct.get("CDN_DOMAIN") and direct.get("WS_PATH"):
        routes.append({"domain": direct["CDN_DOMAIN"], "path": direct.get("CDN_PATH", direct["WS_PATH"]), "port": int(direct.get("CDN_PORT", "10087")), "transport": direct.get("CDN_TRANSPORT", "websocket")})
    for descriptor in VLESS_CDN_ROUTE_ROOT.glob("*.json") if VLESS_CDN_ROUTE_ROOT.exists() else []:
        value = load_json(descriptor, {})
        if value.get("domain") and value.get("path") and value.get("port"):
            routes.append(value)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        grouped.setdefault(str(route["domain"]), []).append(route)
    VLESS_CDN_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
    lock_path = VLESS_CDN_SNIPPET.with_suffix(".lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not grouped:
            VLESS_CDN_SNIPPET.unlink(missing_ok=True)
        else:
            lines: list[str] = []
            for domain, items in grouped.items():
                lines.append(f"{domain} {{")
                for item in items:
                    matcher = f"{item['path']}*" if item.get("transport") in {"xhttp", "grpc"} else str(item["path"])
                    upstream = f"h2c://127.0.0.1:{int(item['port'])}" if item.get("transport") == "grpc" else f"127.0.0.1:{int(item['port'])}"
                    lines.extend([f"    handle {matcher} {{", f"        reverse_proxy {upstream}", "    }"])
                lines.extend(["    respond 404", "}"])
            temporary = VLESS_CDN_SNIPPET.with_suffix(".tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o644)
            os.replace(temporary, VLESS_CDN_SNIPPET)
        for legacy in VLESS_CDN_SNIPPET.parent.glob("mihomo-vless-*.caddy"):
            legacy.unlink(missing_ok=True)


def apply_reality_config(config_path: Path, config: dict[str, Any]) -> None:
    # Xray determines the config loader from the final extension. Keep .json
    # last; names such as config.json.candidate are rejected by newer Xray.
    candidate = config_path.with_name(f"{config_path.stem}.candidate.json")
    atomic_json(candidate, config, mode=0o640)
    shutil.chown(candidate, user="root", group="nogroup")
    result = run(str(REALITY_XRAY_BIN), "run", "-test", "-config", str(candidate))
    if result.returncode:
        candidate.unlink(missing_ok=True)
        raise RuntimeError((result.stderr or result.stdout).strip() or "Xray rejected VLESS configuration")
    os.replace(candidate, config_path)
    # Profile mutations can legitimately restart Xray several times in a
    # short transaction. Clear systemd's start-rate counter before applying
    # the next validated configuration.
    run("systemctl", "reset-failed", "vps-control-mihomo-reality.service")
    run("systemctl", "restart", "vps-control-mihomo-reality.service", check=True)


def add_reality_credential(profile_id: str, connection_id: str, connection_settings: dict[str, Any]) -> dict[str, Any]:
    config_path = CONFIG_ROOT / "reality" / "config.json"
    config = load_json(config_path, {})
    if not isinstance(config.get("inbounds"), list):
        raise RuntimeError("Mihomo Reality server configuration is invalid")
    settings = validate_connection("transport-reality", connection_settings)
    core = module_settings("transport-reality")
    direct_port = int(settings["port"]) or next_vless_port(config, int(core["port_start"]))
    if direct_port in used_vless_ports(config):
        raise RuntimeError(f"VLESS port {direct_port} is already used")
    port_allocation_config = {**config, "inbounds": [*config.get("inbounds", []), {"port": direct_port}]}
    cdn_port = next_vless_port(port_allocation_config, int(core["cdn_port_start"])) if settings["cdn_enabled"] else 0
    env = reality_env()
    private_key, public_key = env.get("PRIVATE_KEY", ""), env.get("PUBLIC_KEY", "")
    if not private_key or not public_key:
        raise RuntimeError("Mihomo VLESS key material is missing")
    user_id = str(uuid.uuid4())
    email = f"mihomo-{profile_id}-{connection_id}"
    direct_tag = f"mihomo-vless-{connection_id}"
    config["inbounds"].append({"tag": direct_tag, "listen": "::", "port": direct_port, "protocol": "vless", "settings": {"clients": [{"id": user_id, "email": email, "flow": ""}], "decryption": "none"}, "streamSettings": vless_stream(settings, private_key, env.get("SHORT_ID", "")), "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}})
    cdn_path = "/" + secrets.token_hex(16)
    if settings["cdn_enabled"]:
        transport = settings["cdn_transport"]
        cdn_settings: dict[str, Any] = {"network": transport, "security": "none"}
        if transport == "xhttp":
            cdn_settings["xhttpSettings"] = {"path": cdn_path, "mode": settings["cdn_xhttp_mode"]}
        elif transport == "grpc":
            cdn_settings["grpcSettings"] = {"serviceName": cdn_path.lstrip("/"), "multiMode": False}
        else:
            cdn_settings["network"] = "websocket"
            cdn_settings["wsSettings"] = {"path": cdn_path}
        config["inbounds"].append({"tag": f"mihomo-vless-cdn-{connection_id}", "listen": "127.0.0.1", "port": cdn_port, "protocol": "vless", "settings": {"clients": [{"id": user_id, "email": email, "flow": ""}], "decryption": "none"}, "streamSettings": cdn_settings})
    original_config = config_path.read_bytes()
    try:
        write_mihomo_vless_cdn(connection_id, settings["cdn_enabled"], settings["cdn_domain"], cdn_path, cdn_port, settings["cdn_transport"])
        apply_reality_config(config_path, config)
        if settings["cdn_enabled"]:
            run("caddy", "validate", "--config", "/etc/caddy/Caddyfile", check=True)
            run("systemctl", "reload", "caddy.service", check=True)
    except Exception:
        write_mihomo_vless_cdn(connection_id, False, "", "", 0)
        config_path.write_bytes(original_config)
        os.chmod(config_path, 0o640)
        shutil.chown(config_path, user="root", group="nogroup")
        run("systemctl", "restart", "vps-control-mihomo-reality.service")
        run("systemctl", "reload", "caddy.service")
        raise
    return {"uuid": user_id, "port": direct_port, "public_key": public_key, "short_id": env.get("SHORT_ID", ""), "servername": settings["target"].rsplit(":", 1)[0], "transport": settings["transport"], "path": settings["transport_path"], "xhttp_mode": settings["xhttp_mode"], "direct_tag": direct_tag, "cdn_enabled": settings["cdn_enabled"], "cdn_domain": settings["cdn_domain"], "cdn_port": cdn_port, "cdn_path": cdn_path, "cdn_transport": settings["cdn_transport"], "cdn_xhttp_mode": settings["cdn_xhttp_mode"]}


def remove_reality_credential(profile_id: str, credential: dict[str, Any]) -> None:
    config_path = CONFIG_ROOT / "reality" / "config.json"
    config = load_json(config_path, {})
    try:
        user_id = credential.get("uuid")
        direct_tag = credential.get("direct_tag")
        config["inbounds"] = [item for item in config.get("inbounds", []) if not (direct_tag and item.get("tag") in {direct_tag, str(direct_tag).replace("mihomo-vless-", "mihomo-vless-cdn-")}) and not (not direct_tag and any(client.get("id") == user_id for client in item.get("settings", {}).get("clients", [])))]
        connection_id = str(direct_tag or "").removeprefix("mihomo-vless-")
        if connection_id:
            write_mihomo_vless_cdn(connection_id, False, "", "", 0)
        apply_reality_config(config_path, config)
        if credential.get("cdn_enabled"):
            run("systemctl", "reload", "caddy.service")
    except (TypeError, KeyError, IndexError):
        pass


def wg_like_dump(module_id: str) -> dict[str, dict[str, Any]]:
    tool = "wg" if module_id == "transport-wg" else "awg"
    interface = "mh-wg0" if module_id == "transport-wg" else "mh-awg0"
    output = run(tool, "show", interface, "dump").stdout
    now = int(time.time())
    peers: dict[str, dict[str, Any]] = {}
    for row in output.splitlines()[1:]:
        columns = row.split("\t")
        if len(columns) < 8:
            continue
        key, _, endpoint, _, handshake, rx, tx, _ = columns[:8]
        handshake_at = int(handshake or 0)
        peers[key] = {
            "endpoint": endpoint if endpoint and endpoint != "(none)" else None,
            "rx_bytes": int(rx or 0),
            "tx_bytes": int(tx or 0),
            "handshake_age_s": (now - handshake_at) if handshake_at else None,
        }
    return peers


def shadowsocks_profile_stats(profile_id: str, port: int, instance_id: str | None = None) -> dict[str, Any]:
    unit = f"vps-control-mihomo-ss@{instance_id or profile_id}.service"

    def counter(name: str) -> int:
        try:
            return int(run("systemctl", "show", unit, f"--property={name}", "--value").stdout.strip() or 0)
        except ValueError:
            return 0

    connections = run("ss", "-Htn", "state", "established", f"( sport = :{port} )").stdout
    active_connections = len([line for line in connections.splitlines() if line.strip()])
    return {
        "active": systemctl_active(unit),
        "rx_bytes": counter("IPIngressBytes"),
        "tx_bytes": counter("IPEgressBytes"),
        "active_connections": active_connections,
    }


def reality_profile_stats(profile_id: str, connection_id: str | None = None) -> dict[str, Any]:
    active = systemctl_active("vps-control-mihomo-reality.service")
    if not REALITY_XRAY_BIN.is_file() or not active:
        return {"active": active, "rx_bytes": 0, "tx_bytes": 0}
    email = f"mihomo-{profile_id}" + (f"-{connection_id}" if connection_id else "")
    output = run(
        str(REALITY_XRAY_BIN), "api", "statsquery",
        f"-server={REALITY_API_SERVER}", "-pattern", f"user>>>{email}>>>traffic>>>",
    ).stdout
    uplink = downlink = 0
    try:
        payload = json.loads(output)
        for entry in payload.get("stat", []):
            name = str(entry.get("name", ""))
            value = int(entry.get("value", 0))
            if name.endswith(">>>uplink"):
                uplink = value
            elif name.endswith(">>>downlink"):
                downlink = value
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    # "uplink"/"downlink" are named from the client's perspective, matching
    # the rx/tx convention used by the wg/awg and Shadowsocks stats above.
    return {"active": True, "rx_bytes": downlink, "tx_bytes": uplink}


@app.get("/api/mihomo/profiles/{profile_id}/stats", dependencies=[Depends(auth_required)])
def profile_stats(profile_id: str) -> dict[str, Any]:
    item = next((entry for entry in profiles() if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    connections: dict[str, Any] = {}
    wg_dump: dict[str, dict[str, Any]] | None = None
    awg_dump: dict[str, dict[str, Any]] | None = None
    empty_peer = {"endpoint": None, "rx_bytes": 0, "tx_bytes": 0, "handshake_age_s": None}
    for connection in normalize_profile(item).get("connections", []):
        connection_id = str(connection.get("id", ""))
        module_id = str(connection.get("component", ""))
        credential = connection.get("credential", {})
        if module_id == "transport-wg":
            wg_dump = wg_like_dump("transport-wg") if wg_dump is None else wg_dump
            connections[connection_id] = wg_dump.get(str(credential.get("public_key", "")), empty_peer)
        elif module_id == "transport-awg":
            awg_dump = wg_like_dump("transport-awg") if awg_dump is None else awg_dump
            connections[connection_id] = awg_dump.get(str(credential.get("public_key", "")), empty_peer)
        elif module_id == "transport-shadowsocks":
            connections[connection_id] = shadowsocks_profile_stats(profile_id, int(credential.get("port", 0)), credential.get("instance_id"))
        elif module_id == "transport-reality":
            connections[connection_id] = reality_profile_stats(profile_id, connection_id)
    values = list(connections.values())
    rx_bytes = sum(int(value.get("rx_bytes", 0) or 0) for value in values)
    tx_bytes = sum(int(value.get("tx_bytes", 0) or 0) for value in values)
    active = sum(1 for value in values if value.get("active") or value.get("endpoint") or int(value.get("active_connections", 0) or 0) > 0)
    handshake_ages = [int(value["handshake_age_s"]) for value in values if value.get("handshake_age_s") is not None]
    device_summaries: dict[str, dict[str, Any]] = {}
    for device in item.get("devices", [{"id": "device-1"}]):
        device_id = str(device.get("id", "device-1"))
        ids = [str(connection.get("id")) for connection in item.get("connections", []) if connection.get("device_id", "device-1") == device_id]
        rows = [connections[value] for value in ids if value in connections]
        ages = [int(row["handshake_age_s"]) for row in rows if row.get("handshake_age_s") is not None]
        device_summaries[device_id] = {"configured": len(rows), "active": sum(1 for row in rows if row.get("active") or row.get("endpoint") or int(row.get("active_connections", 0) or 0) > 0), "rx_bytes": sum(int(row.get("rx_bytes", 0) or 0) for row in rows), "tx_bytes": sum(int(row.get("tx_bytes", 0) or 0) for row in rows), "last_handshake_age_s": min(ages) if ages else None}
    return {"id": profile_id, "connections": connections, "channels": connections, "devices": device_summaries, "summary": {"configured": len(values), "active": active, "rx_bytes": rx_bytes, "tx_bytes": tx_bytes, "last_handshake_age_s": min(handshake_ages) if handshake_ages else None}}


def provision(profile_id: str, module_id: str, connection_id: str = "default", settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if module_id in ("transport-wg", "transport-awg"):
        return add_wg_credential(profile_id, module_id, connection_id)
    if module_id == "transport-shadowsocks":
        return add_ss_credential(profile_id, connection_id)
    if module_id == "transport-reality":
        return add_reality_credential(profile_id, connection_id, settings or {})
    raise RuntimeError(f"{module_id} is not a transport")


def deprovision(profile_id: str, module_id: str, credential: dict[str, Any]) -> None:
    if module_id in ("transport-wg", "transport-awg"):
        remove_wg_credential(profile_id, module_id, credential)
    elif module_id == "transport-shadowsocks":
        remove_ss_credential(profile_id, credential)
    elif module_id == "transport-reality":
        remove_reality_credential(profile_id, credential)


def validate_channels(channels: list[str]) -> list[str]:
    result: list[str] = []
    for module_id in channels:
        if module_id not in TRANSPORTS:
            raise HTTPException(status_code=422, detail=f"{module_id} is not a Mihomo transport")
        if not module_is_installed(module_id):
            raise HTTPException(status_code=409, detail=f"Сначала установите {manifest(module_id)['name']} внутри Mihomo")
        if module_id not in result:
            result.append(module_id)
    return result


def validate_connection_inputs(values: list[ProfileConnectionInput]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    used_singletons: set[tuple[str, str]] = set()
    for index, value in enumerate(values):
        component = value.component
        validate_channels([component])
        singleton_key = (value.device_id, component)
        if component != "transport-reality" and singleton_key in used_singletons:
            raise HTTPException(status_code=422, detail=f"Only VLESS can currently be added more than once: {manifest(component)['name']}")
        used_singletons.add(singleton_key)
        connection_id = value.id or f"connection-{index + 1}-{uuid.uuid4().hex[:6]}"
        if connection_id in used_ids:
            raise HTTPException(status_code=422, detail=f"Duplicate connection id: {connection_id}")
        used_ids.add(connection_id)
        settings = validate_connection(component, value.settings)
        result.append({"id": connection_id, "component": component, "name": value.name.strip() or manifest(component).get("name", component), "device_id": value.device_id, "settings": settings})
    return result


def legacy_connection_inputs(channels: list[str]) -> list[dict[str, Any]]:
    return [{"id": f"connection-{index + 1}-{uuid.uuid4().hex[:6]}", "component": component, "name": manifest(component).get("name", component), "settings": connection_defaults(component)} for index, component in enumerate(validate_channels(channels))]


def provision_connections(profile_id: str, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    try:
        for definition in definitions:
            credential = provision(profile_id, definition["component"], definition["id"], definition.get("settings", {}))
            completed.append({**definition, "credential": credential})
    except Exception:
        for connection in reversed(completed):
            try:
                deprovision(profile_id, connection["component"], connection.get("credential", {}))
            except Exception:
                pass
        raise
    return completed


def create_profile_credentials(profile_id: str, channels: list[str]) -> dict[str, Any]:
    credentials: dict[str, Any] = {}
    completed: list[str] = []
    try:
        for module_id in channels:
            credentials[module_id] = provision(profile_id, module_id)
            completed.append(module_id)
    except Exception:
        for module_id in reversed(completed):
            try:
                deprovision(profile_id, module_id, credentials[module_id])
            except Exception:
                pass
        raise
    return credentials


@app.get("/api/mihomo/profiles", dependencies=[Depends(auth_required)])
def list_profiles() -> dict[str, Any]:
    return {"items": [profile_response(item) for item in profiles()]}


@app.post("/api/mihomo/profiles", dependencies=[Depends(auth_required)])
@serialized_profile_mutation
def create_profile(payload: ProfileCreate) -> dict[str, Any]:
    definitions = validate_connection_inputs(payload.connections) if payload.connections is not None else legacy_connection_inputs(payload.channels)
    devices = [device.model_dump() for device in payload.devices]
    device_ids = {device["id"] for device in devices}
    if len(device_ids) != len(devices) or any(definition.get("device_id", "device-1") not in device_ids for definition in definitions):
        raise HTTPException(status_code=422, detail="Profile devices are invalid or a connection references a missing device")
    routing = validate_routing(payload.routing, current={})
    profile_id = uuid.uuid4().hex[:12]
    write_action(f"profile-create:{profile_id}", f"Создание профиля «{payload.name}»…", progress=15)
    try:
        connections = provision_connections(profile_id, definitions)
    except Exception as exc:
        write_action(f"profile-create:{profile_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    item = {
        "id": profile_id,
        "name": payload.name.strip(),
        "connections": connections,
        "devices": devices,
        "subscriptions": {device["id"]: secrets.token_urlsafe(32) for device in devices},
        "routing": routing,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    sync_legacy_profile_fields(item)
    data = profiles()
    data.append(item)
    save_profiles(data)
    write_action(f"profile-create:{profile_id}", f"Профиль «{item['name']}» создан", state="done", progress=100)
    return profile_response(item)


@app.patch("/api/mihomo/profiles/{profile_id}", dependencies=[Depends(auth_required)])
@serialized_profile_mutation
def update_profile(profile_id: str, payload: ProfileUpdate) -> dict[str, Any]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    write_action(f"profile-update:{profile_id}", f"Обновление профиля «{item.get('name', '')}»…", progress=15)
    if payload.name is not None:
        item["name"] = payload.name.strip()
    if payload.routing is not None:
        item["routing"] = validate_routing(payload.routing, current=item.get("routing", {}))
    if payload.devices is not None:
        devices = [device.model_dump() for device in payload.devices]
        if len({device["id"] for device in devices}) != len(devices):
            raise HTTPException(status_code=422, detail="Duplicate profile device id")
        item["devices"] = devices
        current_subscriptions = item.get("subscriptions", {})
        item["subscriptions"] = {
            device["id"]: current_subscriptions.get(device["id"]) or secrets.token_urlsafe(32)
            for device in devices
        }
    if payload.connections is not None:
        definitions = validate_connection_inputs(payload.connections)
        device_ids = {str(device.get("id")) for device in item.get("devices", [])}
        if any(definition.get("device_id") not in device_ids for definition in definitions):
            raise HTTPException(status_code=422, detail="A connection references a missing profile device")
        current_connections = {str(connection.get("id")): connection for connection in item.get("connections", [])}
        next_ids = {definition["id"] for definition in definitions}
        for connection_id, connection in current_connections.items():
            if connection_id not in next_ids:
                deprovision(profile_id, connection["component"], connection.get("credential", {}))
        next_connections: list[dict[str, Any]] = []
        for definition in definitions:
            current_connection = current_connections.get(definition["id"])
            if current_connection and current_connection.get("component") == definition["component"] and current_connection.get("settings", {}) == definition.get("settings", {}):
                next_connections.append({**definition, "credential": current_connection.get("credential", {})})
            else:
                if current_connection:
                    deprovision(profile_id, current_connection["component"], current_connection.get("credential", {}))
                next_connections.append({**definition, "credential": provision(profile_id, definition["component"], definition["id"], definition.get("settings", {}))})
        item["connections"] = next_connections
        sync_legacy_profile_fields(item)
    device_ids = {str(device.get("id")) for device in item.get("devices", [])}
    if any(connection.get("device_id", "device-1") not in device_ids for connection in item.get("connections", [])):
        raise HTTPException(status_code=422, detail="A connection references a missing profile device")
    elif payload.channels is not None:
        next_channels = validate_channels(payload.channels)
        current = list(item.get("channels", []))
        credentials = dict(item.get("credentials", {}))
        for module_id in current:
            if module_id not in next_channels:
                deprovision(profile_id, module_id, credentials.get(module_id, {}))
                credentials.pop(module_id, None)
        try:
            for module_id in next_channels:
                if module_id not in current:
                    credentials[module_id] = provision(profile_id, module_id)
        except Exception as exc:
            write_action(f"profile-update:{profile_id}", str(exc), state="failed", progress=100)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        item["channels"] = next_channels
        item["credentials"] = credentials
        item.pop("connections", None)
        normalized = normalize_profile(item)
        item.clear()
        item.update(normalized)
    item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_profiles(data)
    write_action(f"profile-update:{profile_id}", f"Профиль «{item['name']}» обновлён", state="done", progress=100)
    return profile_response(item)


@app.delete("/api/mihomo/profiles/{profile_id}", dependencies=[Depends(auth_required)])
@serialized_profile_mutation
def delete_profile(profile_id: str) -> dict[str, Any]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    write_action(f"profile-delete:{profile_id}", f"Удаление профиля «{item.get('name', '')}»…", progress=20)
    errors: list[str] = []
    for connection in item.get("connections", []):
        try:
            deprovision(profile_id, connection.get("component", ""), connection.get("credential", {}))
        except Exception as exc:
            errors.append(f"{connection.get('name') or connection.get('component')}: {exc}")
    if errors:
        detail = "; ".join(errors)
        write_action(f"profile-delete:{profile_id}", detail, state="failed", progress=100)
        # Keep profile metadata visible. Some adapters are idempotent, so the
        # administrator can retry deletion instead of losing track of a live
        # credential that failed to deprovision.
        raise HTTPException(status_code=500, detail=f"Не удалось полностью удалить профиль: {detail}")
    save_profiles([entry for entry in data if entry.get("id") != profile_id])
    write_action(f"profile-delete:{profile_id}", "Профиль удалён", state="done", progress=100)
    return {"removed": profile_id}


def q(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render_proxy(module_id: str, credential: dict[str, Any], proxy_name: str) -> list[str]:
    server = public_endpoint()
    if module_id in ("transport-wg", "transport-awg"):
        name = "AWG" if module_id == "transport-awg" else "WG"
        lines = [
            f"  - name: {q(proxy_name or name)}",
            "    type: wireguard",
            f"    server: {q(server)}",
            f"    port: {int(credential['port'])}",
            f"    ip: {q(str(credential['ip']).split('/', 1)[0])}",
            f"    private-key: {q(credential['private_key'])}",
            f"    public-key: {q(credential['server_public_key'])}",
            "    allowed-ips: [\"0.0.0.0/0\"]",
            "    udp: true",
            f"    mtu: {int(credential['mtu'])}",
        ]
        if module_id == "transport-awg":
            awg = credential["amnezia"]
            lines += [
                "    amnezia-wg-option:",
                "      version: 2",
                f"      jc: {awg['jc']}",
                f"      jmin: {awg['jmin']}",
                f"      jmax: {awg['jmax']}",
                f"      s1: {awg['s1']}",
                f"      s2: {awg['s2']}",
                f"      h1: {awg['h1']}",
                f"      h2: {awg['h2']}",
                f"      h3: {awg['h3']}",
                f"      h4: {awg['h4']}",
            ]
        return lines
    if module_id == "transport-shadowsocks":
        return [
            f"  - name: {q(proxy_name or 'SS')}",
            "    type: ss",
            f"    server: {q(server)}",
            f"    port: {int(credential['port'])}",
            f"    cipher: {q(credential['method'])}",
            f"    password: {q(credential['password'])}",
            "    udp: true",
        ]
    if module_id == "transport-reality":
        # Read transport/SNI/path from the current module configuration. A
        # settings change applies to all profiles served by this Xray instance.
        # UUID remains profile-specific and comes from the stored credential.
        effective = dict(credential)
        if not effective.get("direct_tag"):
            effective = {**effective, **reality_connection_settings()}
        transport = str(effective.get("transport", "xhttp"))
        lines = [
            f"  - name: {q(proxy_name or 'VLESS')}",
            "    type: vless",
            f"    server: {q(server)}",
            f"    port: {int(effective['port'])}",
            f"    uuid: {q(credential['uuid'])}",
            '    encryption: ""',
            "    udp: true",
            "    tls: true",
            f"    servername: {q(effective['servername'])}",
            "    client-fingerprint: chrome",
            f"    network: {'tcp' if transport == 'raw' else transport}",
            "    reality-opts:",
            f"      public-key: {q(effective['public_key'])}",
            f"      short-id: {q(effective['short_id'])}",
        ]
        if transport == "xhttp":
            lines += [
                "    xhttp-opts:",
                f"      path: {q(effective.get('path', '/'))}",
                f"      mode: {q(effective.get('xhttp_mode', 'auto'))}",
            ]
        elif transport == "grpc":
            lines += [
                "    grpc-opts:",
                f"      grpc-service-name: {q(str(effective.get('path', '/vless')).lstrip('/'))}",
            ]
        return lines
    return []


def render_vless_cdn(credential: dict[str, Any], proxy_name: str) -> list[str]:
    transport = str(credential.get("cdn_transport", "websocket"))
    lines = [
        f"  - name: {q(proxy_name)}",
        "    type: vless",
        f"    server: {q(credential['cdn_domain'])}",
        "    port: 443",
        f"    uuid: {q(credential['uuid'])}",
        '    encryption: ""',
        "    udp: true",
        "    tls: true",
        f"    servername: {q(credential['cdn_domain'])}",
        "    client-fingerprint: chrome",
        f"    network: {'ws' if transport == 'websocket' else transport}",
    ]
    if transport == "xhttp":
        lines += ["    xhttp-opts:", f"      path: {q(credential['cdn_path'])}", f"      mode: {q(credential.get('cdn_xhttp_mode', 'auto'))}"]
    elif transport == "grpc":
        lines += ["    grpc-opts:", f"      grpc-service-name: {q(str(credential['cdn_path']).lstrip('/'))}"]
    else:
        lines += ["    ws-opts:", f"      path: {q(credential['cdn_path'])}", "      headers:", f"        Host: {q(credential['cdn_domain'])}"]
    return lines


def render_profile(item: dict[str, Any], device_id: str | None = None) -> str:
    default_names = {
        "transport-awg": "AWG",
        "transport-wg": "WG",
        "transport-reality": "VRX",
        "transport-shadowsocks": "SS",
    }
    normalized = normalize_profile(item)
    selected_device = device_id or str(normalized.get("devices", [{"id": "device-1"}])[0].get("id", "device-1"))
    connections = [connection for connection in normalized.get("connections", []) if connection.get("component") in default_names and connection.get("device_id", "device-1") == selected_device]
    if not connections:
        raise HTTPException(status_code=409, detail="У профиля нет подключений Mihomo")
    used_names: set[str] = set()
    rendered: list[tuple[dict[str, Any], str, str | None]] = []
    for index, connection in enumerate(connections):
        component = str(connection["component"])
        base = str(connection.get("name") or default_names[component]).strip()
        name = base
        suffix = 2
        while name in used_names:
            name = f"{base} {suffix}"
            suffix += 1
        used_names.add(name)
        cdn_name = None
        credential = connection.get("credential", {})
        if component == "transport-reality" and credential.get("cdn_enabled"):
            cdn_name = f"{name} · CDN"
            used_names.add(cdn_name)
        rendered.append((connection, name, cdn_name))
    routing = {**routing_settings(), **item.get("routing", {})}
    mode = str(routing.get("mode", "rule"))
    lines = [
        "mixed-port: 7890",
        "allow-lan: false",
        f"mode: {mode}",
        "log-level: warning",
        "ipv6: false",
        "tun:",
        # A portable profile must start without a privileged TUN driver.
        # Desktop clients can enable TUN explicitly after import; mixed-port
        # remains immediately usable on every supported platform.
        "  enable: false",
        "  stack: mixed",
        "  auto-route: true",
        "  strict-route: true",
        "  dns-hijack:",
        '    - "any:53"',
    ]
    dns = dns_settings()
    lines += [
        "dns:",
        "  enable: true",
        f"  enhanced-mode: {dns['enhanced_mode']}",
        "  nameserver:",
        f"    - {q(dns['nameserver'])}",
        "  fallback:",
        f"    - {q(dns['fallback'])}",
    ]
    lines.append("proxies:")
    for connection, name, cdn_name in rendered:
        lines.extend(render_proxy(str(connection["component"]), connection.get("credential", {}), name))
        if cdn_name:
            lines.extend(render_vless_cdn(connection.get("credential", {}), cdn_name))
    group_type = str(routing.get("strategy", "fallback"))
    lines += [
        "proxy-groups:",
        '  - name: "GATE.312"',
        f"    type: {group_type}",
        "    proxies:",
    ]
    for _, name, cdn_name in rendered:
        lines.append(f"      - {q(name)}")
        if cdn_name:
            lines.append(f"      - {q(cdn_name)}")
    if group_type in ("fallback", "url-test"):
        lines += [
            f"    url: {q(routing.get('test_url', 'https://www.gstatic.com/generate_204'))}",
            f"    interval: {int(routing.get('interval', 180))}",
        ]
    lines.append("rules:")
    raw_rules = str(routing.get("rules", "")).replace("\r", "")
    for rule in raw_rules.split("\n"):
        rule = rule.strip()
        if rule and not rule.startswith("#"):
            lines.append(f"  - {q(rule)}")
    lines.append('  - "MATCH,GATE.312"')
    return "\n".join(lines) + "\n"


@app.get(
    "/api/mihomo/profiles/{profile_id}/config",
    response_class=PlainTextResponse,
    dependencies=[Depends(auth_required)],
)
def profile_config(profile_id: str, device_id: str | None = None) -> str:
    item = next((entry for entry in profiles() if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    normalized = normalize_profile(item)
    selected_device = device_id or str(normalized.get("devices", [{"id": "device-1"}])[0].get("id", "device-1"))
    if selected_device not in {str(device.get("id")) for device in normalized.get("devices", [])}:
        raise HTTPException(status_code=404, detail="Profile device not found")
    config = render_profile(normalized, selected_device)
    if CORE_BIN.is_file():
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as handle:
            handle.write(config)
            temp_path = handle.name
        try:
            CORE_HOME.mkdir(parents=True, exist_ok=True)
            result = run(str(CORE_BIN), "-t", "-d", str(CORE_HOME), "-f", temp_path)
            if result.returncode:
                raise HTTPException(
                    status_code=500,
                    detail=(result.stderr or result.stdout).strip() or "Mihomo rejected generated config",
                )
        finally:
            Path(temp_path).unlink(missing_ok=True)
    return config


@app.get(
    "/api/mihomo/profiles/{profile_id}/subscription",
    dependencies=[Depends(auth_required)],
)
@serialized_profile_mutation
def profile_subscription(profile_id: str, device_id: str | None = None) -> dict[str, str]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    normalized = normalize_profile(item)
    selected_device = device_id or str(normalized["devices"][0].get("id", "device-1"))
    if selected_device not in {str(device.get("id")) for device in normalized["devices"]}:
        raise HTTPException(status_code=404, detail="Profile device not found")
    subscriptions = normalized["subscriptions"]
    token = str(subscriptions.get(selected_device, ""))
    if not token:
        token = secrets.token_urlsafe(32)
        subscriptions[selected_device] = token
        item["subscriptions"] = subscriptions
        save_profiles(data)
    return {"path": f"/api/mihomo/subscriptions/{token}"}


@app.get("/api/mihomo/subscriptions/{token}", response_class=PlainTextResponse)
def public_profile_subscription(token: str) -> PlainTextResponse:
    selected_profile: dict[str, Any] | None = None
    selected_device = ""
    for item in profiles():
        for device_id, saved_token in item.get("subscriptions", {}).items():
            if isinstance(saved_token, str) and hmac.compare_digest(saved_token, token):
                selected_profile = item
                selected_device = str(device_id)
                break
        if selected_profile:
            break
    if not selected_profile:
        raise HTTPException(status_code=404, detail="Subscription not found")
    config = render_profile(selected_profile, selected_device)
    return PlainTextResponse(
        config,
        media_type="text/yaml; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="mihomo.yaml"',
            "Profile-Update-Interval": "24",
        },
    )
