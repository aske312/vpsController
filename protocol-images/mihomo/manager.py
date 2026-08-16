from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import os
import secrets
import re
import shutil
import subprocess
import tempfile
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
CORE_BIN = APP_ROOT / "api" / "bin" / "mihomo"

TRANSPORTS = {
    "transport-wg",
    "transport-awg",
    "transport-shadowsocks",
    "transport-reality",
}
NON_TRANSPORTS = {"dns-private", "routing-policy"}
KNOWN_MODULES = TRANSPORTS | NON_TRANSPORTS

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


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    channels: list[str] | None = None


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
            options = item.get("options", [])
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


def module_payload(module_id: str) -> dict[str, Any]:
    info = manifest(module_id)
    settings = module_settings(module_id)
    installed = module_is_installed(module_id)
    service = SERVICE_BY_MODULE.get(module_id)
    return {
        **info,
        "installed": installed,
        "active": systemctl_active(service) if service else installed,
        "service": service or "",
        "settings_values": settings,
    }


def call_module_script(module_id: str, action: str) -> None:
    script = SUBMODULE_ROOT / module_id / f"{action}.sh"
    if not script.is_file():
        raise RuntimeError(f"Missing {script.name} for {module_id}")
    env = os.environ.copy()
    env["MIHOMO_SETTINGS_FILE"] = str(SETTINGS_ROOT / f"{module_id}.json")
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
    core_version = ""
    core_build = ""
    if CORE_BIN.is_file():
        result = run(str(CORE_BIN), "-v")
        core_build = (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 else ""
        match = re.search(r"\bv?(\d+\.\d+\.\d+)\b", core_build)
        core_version = match.group(1) if match else core_build
    return {
        "id": "mihomo",
        "name": "Mihomo",
        "active": systemctl_active("vps-control-mihomo-manager.service"),
        "installed": True,
        "version": core_version,
        "core_version": core_version,
        "core_build": core_build,
        "modules_installed": len(installed_modules),
        "modules_total": len(KNOWN_MODULES),
        "profiles": len(profiles()),
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
        "dns-private",
        "routing-policy",
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
        try:
            call_module_script(module_id, "install")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return get_module_settings(module_id)


@app.post("/api/mihomo/modules/{module_id}/install", dependencies=[Depends(auth_required)])
def install_module(module_id: str) -> dict[str, Any]:
    manifest(module_id)
    if module_is_installed(module_id):
        return module_payload(module_id)
    # Create defaults before the installer so the first install is deterministic.
    settings_path = SETTINGS_ROOT / f"{module_id}.json"
    if not settings_path.exists():
        atomic_json(settings_path, default_settings(module_id))
    try:
        call_module_script(module_id, "install")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    value = state()
    value["modules"][module_id] = True
    value["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(value)
    return module_payload(module_id)


@app.delete("/api/mihomo/modules/{module_id}", dependencies=[Depends(auth_required)])
def remove_module(module_id: str) -> dict[str, Any]:
    manifest(module_id)
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
    try:
        call_module_script(module_id, "uninstall")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    value = state()
    value["modules"][module_id] = False
    value["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(value)
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


def add_reality_credential(profile_id: str) -> dict[str, Any]:
    config_path = CONFIG_ROOT / "reality" / "config.json"
    config = load_json(config_path, {})
    try:
        clients = config["inbounds"][0]["settings"]["clients"]
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
        clients = config["inbounds"][0]["settings"]["clients"]
        user_id = credential.get("uuid")
        config["inbounds"][0]["settings"]["clients"] = [
            item for item in clients if item.get("id") != user_id
        ]
        atomic_json(config_path, config, mode=0o640)
        shutil.chown(config_path, user="root", group="nogroup")
        run("systemctl", "restart", "vps-control-mihomo-reality.service")
    except (TypeError, KeyError, IndexError):
        pass


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
def create_profile(payload: ProfileCreate) -> dict[str, Any]:
    channels = validate_channels(payload.channels)
    profile_id = uuid.uuid4().hex[:12]
    try:
        credentials = create_profile_credentials(profile_id, channels)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    item = {
        "id": profile_id,
        "name": payload.name.strip(),
        "channels": channels,
        "credentials": credentials,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data = profiles()
    data.append(item)
    save_profiles(data)
    return item


@app.patch("/api/mihomo/profiles/{profile_id}", dependencies=[Depends(auth_required)])
def update_profile(profile_id: str, payload: ProfileUpdate) -> dict[str, Any]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    if payload.name is not None:
        item["name"] = payload.name.strip()
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
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        item["channels"] = next_channels
        item["credentials"] = credentials
    item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_profiles(data)
    return item


@app.delete("/api/mihomo/profiles/{profile_id}", dependencies=[Depends(auth_required)])
def delete_profile(profile_id: str) -> dict[str, Any]:
    data = profiles()
    item = next((entry for entry in data if entry.get("id") == profile_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Profile not found")
    for module_id, credential in dict(item.get("credentials", {})).items():
        try:
            deprovision(profile_id, module_id, credential)
        except Exception:
            pass
    save_profiles([entry for entry in data if entry.get("id") != profile_id])
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
    routing = module_settings("routing-policy")
    routing_enabled = module_is_installed("routing-policy")
    mode = str(routing.get("mode", "rule")) if routing_enabled else "rule"
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
    if module_is_installed("dns-private"):
        dns = module_settings("dns-private")
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
    group_type = "select"
    if routing_enabled:
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
    if routing_enabled:
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
            result = run(str(CORE_BIN), "-t", "-f", temp_path)
            if result.returncode:
                raise HTTPException(
                    status_code=500,
                    detail=(result.stderr or result.stdout).strip() or "Mihomo rejected generated config",
                )
        finally:
            Path(temp_path).unlink(missing_ok=True)
    return config
