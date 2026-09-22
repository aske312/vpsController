"""Persistent recovery for DNS configuration, under the shared worker lock."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from application_operation import atomic_json

DNS_KEYS = {"WG_DNS", "AWG_DNS", "SHADOWSOCKS_DNS", "VRX_DNS"}
FILES = ("DNS_SETTINGS_FILE", "VLESS_CONFIG", "OPENVPN_CONFIG", "OPENVPN_SETTINGS",
         "IKEV2_CONFIG", "IKEV2_SETTINGS", "SYSTEM_RESOLVED_DROPIN", "SYSTEM_RESOLV_CONF")
UNITS = {"apply_vrx": "vps-control-vless-reality-xhttp.service",
         "apply_openvpn": "vps-control-openvpn.service", "apply_ikev2": "vps-control-ikev2.service",
         "apply_system": "systemd-resolved.service"}


def snapshot_path(api):
    return api.DATA_DIR / "dns-recovery.json"


def env_dns(path):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {line.split("=", 1)[0]: line for line in lines if line.split("=", 1)[0] in DNS_KEYS}


def replace_bytes(path: Path, content: bytes, mode: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".dns-restore-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(mode)
        if path.exists() and hasattr(os, "chown"):
            details = path.stat()
            os.chown(temporary, details.st_uid, details.st_gid)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(api, settings):
    recovery = snapshot_path(api)
    if recovery.exists():
        raise RuntimeError("Сначала восстановите DNS после предыдущей прерванной операции")
    values = settings.model_dump()
    selected = {"DNS_SETTINGS_FILE"}
    if values["apply_vrx"] and api.VLESS_CONFIG.exists():
        selected.add("VLESS_CONFIG")
    for scope in ("openvpn", "ikev2"):
        if values[f"apply_{scope}"] and getattr(api, f"{scope.upper()}_CONFIG").exists():
            selected.update({f"{scope.upper()}_CONFIG", f"{scope.upper()}_SETTINGS"})
    if values["apply_system"] or api.SYSTEM_RESOLVED_DROPIN.exists():
        selected.update({"SYSTEM_RESOLVED_DROPIN", "SYSTEM_RESOLV_CONF"})
    files = {}
    for name in selected:
        path = getattr(api, name)
        if path.is_symlink():
            if name != "SYSTEM_RESOLV_CONF":
                raise ValueError("DNS-конфигурация является ссылкой; требуется проверка")
            files[name] = {"link": os.readlink(path)}
        elif path.exists():
            files[name] = {"content": base64.b64encode(path.read_bytes()).decode("ascii"), "mode": path.stat().st_mode & 0o777}
        else:
            files[name] = None
    units = {}
    for field, unit in UNITS.items():
        file = {"apply_vrx": "VLESS_CONFIG", "apply_openvpn": "OPENVPN_CONFIG", "apply_ikev2": "IKEV2_CONFIG", "apply_system": "SYSTEM_RESOLVED_DROPIN"}[field]
        if file not in selected:
            continue
        observation = api.observe_service(unit)
        if observation["unit_present"] is False and field == "apply_system":
            continue
        state = observation["runtime"]["state"]
        if state not in {"running", "stopped"}:
            raise RuntimeError("Неизвестно исходное состояние DNS-службы; настройки не изменены")
        units[unit] = state
    atomic_json(recovery, {"schema": 1, "operation_id": os.environ.get("VPS_CONTROL_OPERATION_ID"),
                           "files": files, "env_existed": api.ENV_FILE.exists(), "env": env_dns(api.ENV_FILE), "units": units})
    try:
        api.update_dns_settings(settings)
        for unit, before in units.items():
            if before == "stopped":
                api.run("systemctl", "stop", unit, timeout=30, check=True)
            if api.observe_service(unit)["runtime"]["state"] != before:
                raise RuntimeError("Не подтверждено состояние DNS-службы после применения")
        if values["apply_system"]:
            if units.get(UNITS["apply_system"]) == "running":
                api.run("resolvectl", "query", "--cache=no", "example.com", timeout=20, check=True)
            else:
                api.run("getent", "ahosts", "example.com", timeout=20, check=True)
        recovery.unlink()
    except Exception as cause:
        try:
            recover(api)
        except Exception as restore_error:
            raise RuntimeError("DNS не применён; восстановление не подтверждено, снимок сохранён") from restore_error
        raise RuntimeError("DNS не применён; прежние файлы и состояния служб восстановлены") from cause


def recover(api):
    recovery = snapshot_path(api)
    if not recovery.exists():
        return
    value = json.loads(recovery.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("files"), dict) or not set(value["files"]).issubset(FILES) or not isinstance(value.get("units"), dict) or not set(value["units"]).issubset(UNITS.values()) or not isinstance(value.get("env"), dict) or not set(value["env"]).issubset(DNS_KEYS):
        raise ValueError("Недопустимый снимок DNS")
    if any(state not in {"running", "stopped"} for state in value["units"].values()):
        raise ValueError("Недопустимое состояние службы")
    decoded = {}
    for name, saved in value["files"].items():
        if saved is None:
            decoded[name] = None
        elif "link" in saved:
            if name != "SYSTEM_RESOLV_CONF" or not isinstance(saved["link"], str):
                raise ValueError("Недопустимая ссылка DNS")
            decoded[name] = saved
        else:
            if type(saved.get("mode")) is not int or not 0 <= saved["mode"] <= 0o777:
                raise ValueError("Недопустимые права DNS")
            decoded[name] = {"content": base64.b64decode(saved["content"], validate=True), "mode": saved["mode"]}
    if any(not isinstance(line, str) or "\n" in line or "\r" in line or not line.startswith(f"{key}=") for key, line in value["env"].items()):
        raise ValueError("Недопустимые DNS-параметры окружения")
    failed = []
    def attempt(callback):
        try:
            callback()
        except Exception as error:
            failed.append(type(error).__name__)
    for name, saved in decoded.items():
        def restore(name=name, saved=saved):
            path = getattr(api, name)
            if saved is None:
                path.unlink(missing_ok=True)
            elif "link" in saved:
                if not path.is_symlink() or os.readlink(path) != saved["link"]:
                    raise RuntimeError("Ссылка resolv.conf изменилась; требуется проверка")
            else:
                if path.is_symlink():
                    raise RuntimeError("DNS-файл заменён ссылкой; требуется проверка")
                replace_bytes(path, saved["content"], saved["mode"])
        attempt(restore)
    def restore_environment():
        if api.ENV_FILE.is_symlink():
            raise ValueError("Environment replaced by symlink")
        lines = api.ENV_FILE.read_text(encoding="utf-8").splitlines() if api.ENV_FILE.exists() else []
        lines = [line for line in lines if line.split("=", 1)[0] not in DNS_KEYS]
        lines.extend(value["env"].values())
        if not lines and not value.get("env_existed", True):
            api.ENV_FILE.unlink(missing_ok=True)
        else:
            replace_bytes(api.ENV_FILE, ("\n".join(lines) + "\n").encode("utf-8"), 0o600)
    attempt(restore_environment)
    if not failed:
        for unit, state in value["units"].items():
            attempt(lambda unit=unit, state=state: api.run("systemctl", "restart" if state == "running" else "stop", unit, timeout=30, check=True))
    for unit, state in value["units"].items():
        def verify(unit=unit, state=state):
            if api.observe_service(unit)["runtime"]["state"] != state:
                raise RuntimeError("DNS runtime recovery not confirmed")
        attempt(verify)
    if failed:
        raise RuntimeError("Восстановление DNS не подтверждено; снимок сохранён")
    recovery.unlink()
