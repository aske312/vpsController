from __future__ import annotations

import base64
import functools
import hmac
import ipaddress
import json
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
from fastapi.responses import PlainTextResponse
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


class ModuleSettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channels: list[str] = Field(default_factory=list)
    routing: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    channels: list[str] | None = None
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


def profiles() -> list[dict[str, Any]]:
    value = load_json(PROFILE_FILE, [])
    return value if isinstance(value, list) else []


def save_profiles(value: list[dict[str, Any]]) -> None:
    atomic_json(PROFILE_FILE, value)


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
    return {"schema": routing_schema(), "values": routing_settings()}


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
        update_available_override = bool(installed and installed_package and candidate_package and installed_package != candidate_package)
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
    credentials_count = sum(
        len(profile.get("credentials", {}))
        for profile in profile_items
        if isinstance(profile.get("credentials", {}), dict)
    )
    profiles_in_use = sum(
        1
        for profile in profile_items
        if profile.get("channels") or profile.get("credentials")
    )
    channels_in_use = sorted({
        str(channel)
        for profile in profile_items
        for channel in profile.get("channels", [])
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
    next_values = validate_settings(module_id, patch.values)
    SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_json(SETTINGS_ROOT / f"{module_id}.json", next_values)
    if module_is_installed(module_id) and module_id in TRANSPORTS:
        write_action(f"module-settings:{module_id}", f"Применение настроек {manifest(module_id)['name']}…")
        try:
            call_module_script(module_id, "install")
        except RuntimeError as exc:
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
        if module_id in profile.get("channels", [])
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
        if package:
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
        credential = profile.get("credentials", {}).get(module_id, {})
        if credential.get("ip"):
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


def add_wg_credential(profile_id: str, module_id: str) -> dict[str, Any]:
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
            f"\n# mihomo-profile:{profile_id}\n"
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
        marker = f"# mihomo-profile:{profile_id}"
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
        credential = profile.get("credentials", {}).get("transport-shadowsocks", {})
        try:
            result.add(int(credential.get("port", 0)))
        except (TypeError, ValueError):
            pass
    return result


def add_ss_credential(profile_id: str) -> dict[str, Any]:
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
    atomic_json(config_dir / f"{profile_id}.json", config)
    run("systemctl", "enable", "--now", f"vps-control-mihomo-ss@{profile_id}.service", check=True)
    if shutil.which("ufw") and run("ufw", "status").stdout.startswith("Status: active"):
        run("ufw", "allow", f"{port}/tcp")
        run("ufw", "allow", f"{port}/udp")
    return {"port": port, "password": password, "method": settings["method"]}


def remove_ss_credential(profile_id: str, credential: dict[str, Any]) -> None:
    run("systemctl", "disable", "--now", f"vps-control-mihomo-ss@{profile_id}.service")
    path = CONFIG_ROOT / "shadowsocks" / f"{profile_id}.json"
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


def add_reality_credential(profile_id: str) -> dict[str, Any]:
    config_path = CONFIG_ROOT / "reality" / "config.json"
    config = load_json(config_path, {})
    try:
        clients = reality_inbound(config)["settings"]["clients"]
    except (TypeError, KeyError, IndexError):
        raise RuntimeError("Mihomo Reality server configuration is invalid")
    user_id = str(uuid.uuid4())
    clients.append({"id": user_id, "email": f"mihomo-{profile_id}", "flow": ""})
    atomic_json(config_path, config, mode=0o640)
    shutil.chown(config_path, user="root", group="nogroup")
    run("systemctl", "restart", "vps-control-mihomo-reality.service", check=True)
    env = reality_env()
    return {
        "uuid": user_id,
        "port": int(env.get("PORT", module_settings("transport-reality")["port"])),
        "public_key": env.get("PUBLIC_KEY", ""),
        "short_id": env.get("SHORT_ID", ""),
        "path": env.get("XHTTP_PATH", "/"),
        "servername": env.get("TARGET", "www.intel.com:443").split(":", 1)[0],
    }


def remove_reality_credential(profile_id: str, credential: dict[str, Any]) -> None:
    config_path = CONFIG_ROOT / "reality" / "config.json"
    config = load_json(config_path, {})
    try:
        inbound = reality_inbound(config)
        user_id = credential.get("uuid")
        inbound["settings"]["clients"] = [
            item for item in inbound["settings"]["clients"] if item.get("id") != user_id
        ]
        atomic_json(config_path, config, mode=0o640)
        shutil.chown(config_path, user="root", group="nogroup")
        run("systemctl", "restart", "vps-control-mihomo-reality.service")
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


def shadowsocks_profile_stats(profile_id: str, port: int) -> dict[str, Any]:
    unit = f"vps-control-mihomo-ss@{profile_id}.service"

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


def reality_profile_stats(profile_id: str) -> dict[str, Any]:
    active = systemctl_active("vps-control-mihomo-reality.service")
    if not REALITY_XRAY_BIN.is_file() or not active:
        return {"active": active, "rx_bytes": 0, "tx_bytes": 0}
    email = f"mihomo-{profile_id}"
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
    credentials = item.get("credentials", {})
    channels: dict[str, Any] = {}
    wg_dump: dict[str, dict[str, Any]] | None = None
    awg_dump: dict[str, dict[str, Any]] | None = None
    empty_peer = {"endpoint": None, "rx_bytes": 0, "tx_bytes": 0, "handshake_age_s": None}
    for module_id in item.get("channels", []):
        credential = credentials.get(module_id, {})
        if module_id == "transport-wg":
            wg_dump = wg_like_dump("transport-wg") if wg_dump is None else wg_dump
            channels[module_id] = wg_dump.get(str(credential.get("public_key", "")), empty_peer)
        elif module_id == "transport-awg":
            awg_dump = wg_like_dump("transport-awg") if awg_dump is None else awg_dump
            channels[module_id] = awg_dump.get(str(credential.get("public_key", "")), empty_peer)
        elif module_id == "transport-shadowsocks":
            channels[module_id] = shadowsocks_profile_stats(profile_id, int(credential.get("port", 0)))
        elif module_id == "transport-reality":
            channels[module_id] = reality_profile_stats(profile_id)
    return {"id": profile_id, "channels": channels}


def provision(profile_id: str, module_id: str) -> dict[str, Any]:
    if module_id in ("transport-wg", "transport-awg"):
        return add_wg_credential(profile_id, module_id)
    if module_id == "transport-shadowsocks":
        return add_ss_credential(profile_id)
    if module_id == "transport-reality":
        return add_reality_credential(profile_id)
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
    return {"items": profiles()}


@app.post("/api/mihomo/profiles", dependencies=[Depends(auth_required)])
@serialized_profile_mutation
def create_profile(payload: ProfileCreate) -> dict[str, Any]:
    channels = validate_channels(payload.channels)
    routing = validate_routing(payload.routing, current={})
    profile_id = uuid.uuid4().hex[:12]
    write_action(f"profile-create:{profile_id}", f"Создание профиля «{payload.name}»…", progress=15)
    try:
        credentials = create_profile_credentials(profile_id, channels)
    except Exception as exc:
        write_action(f"profile-create:{profile_id}", str(exc), state="failed", progress=100)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    item = {
        "id": profile_id,
        "name": payload.name.strip(),
        "channels": channels,
        "routing": routing,
        "credentials": credentials,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data = profiles()
    data.append(item)
    save_profiles(data)
    write_action(f"profile-create:{profile_id}", f"Профиль «{item['name']}» создан", state="done", progress=100)
    return item


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
    if payload.channels is not None:
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
    item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_profiles(data)
    write_action(f"profile-update:{profile_id}", f"Профиль «{item['name']}» обновлён", state="done", progress=100)
    return item


@app.delete("/api/mihomo/profiles/{profile_id}", dependencies=[Depends(auth_required)])
@serialized_profile_mutation
def delete_profile(profile_id: str) -> dict[str, Any]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    write_action(f"profile-delete:{profile_id}", f"Удаление профиля «{item.get('name', '')}»…", progress=20)
    errors: list[str] = []
    for module_id, credential in dict(item.get("credentials", {})).items():
        try:
            deprovision(profile_id, module_id, credential)
        except Exception as exc:
            errors.append(f"{module_id}: {exc}")
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


def render_proxy(module_id: str, credential: dict[str, Any]) -> list[str]:
    server = public_endpoint()
    if module_id in ("transport-wg", "transport-awg"):
        name = "AWG" if module_id == "transport-awg" else "WG"
        lines = [
            f"  - name: {q(name)}",
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
            '  - name: "SS"',
            "    type: ss",
            f"    server: {q(server)}",
            f"    port: {int(credential['port'])}",
            f"    cipher: {q(credential['method'])}",
            f"    password: {q(credential['password'])}",
            "    udp: true",
        ]
    if module_id == "transport-reality":
        return [
            '  - name: "VRX"',
            "    type: vless",
            f"    server: {q(server)}",
            f"    port: {int(credential['port'])}",
            f"    uuid: {q(credential['uuid'])}",
            '    encryption: ""',
            "    udp: true",
            "    tls: true",
            f"    servername: {q(credential['servername'])}",
            "    client-fingerprint: chrome",
            "    network: xhttp",
            "    reality-opts:",
            f"      public-key: {q(credential['public_key'])}",
            f"      short-id: {q(credential['short_id'])}",
            "    xhttp-opts:",
            f"      path: {q(credential['path'])}",
            "      mode: auto",
        ]
    return []


def render_profile(item: dict[str, Any]) -> str:
    proxy_names = {
        "transport-awg": "AWG",
        "transport-wg": "WG",
        "transport-reality": "VRX",
        "transport-shadowsocks": "SS",
    }
    channels = [channel for channel in item.get("channels", []) if channel in proxy_names]
    if not channels:
        raise HTTPException(status_code=409, detail="У профиля нет установленных каналов Mihomo")
    credentials = item.get("credentials", {})
    routing = {**routing_settings(), **item.get("routing", {})}
    mode = str(routing.get("mode", "rule"))
    lines = [
        "mixed-port: 7890",
        "allow-lan: false",
        f"mode: {mode}",
        "log-level: warning",
        "ipv6: false",
        "tun:",
        "  enable: true",
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
    for module_id in channels:
        lines.extend(render_proxy(module_id, credentials[module_id]))
    group_type = str(routing.get("strategy", "fallback"))
    lines += [
        "proxy-groups:",
        '  - name: "GATE.312"',
        f"    type: {group_type}",
        "    proxies:",
    ]
    for module_id in channels:
        lines.append(f"      - {q(proxy_names[module_id])}")
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
def profile_config(profile_id: str) -> str:
    item = next((entry for entry in profiles() if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    config = render_profile(item)
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
