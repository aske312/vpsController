"""Read-only systemd observations. Unit presence is not component installation."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def observe_service(unit: str) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    properties = {}
    reason = "Не удалось получить состояние службы"
    try:
        result = subprocess.run(
            ["systemctl", "show", unit,
             "--property=LoadState,ActiveState,SubState,UnitFileState,NRestarts,ActiveEnterTimestamp,Description"],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if result.returncode == 0:
            properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        elif result.returncode == 4 and "LoadState=not-found" in result.stdout.splitlines():
            properties = {"LoadState": "not-found", "ActiveState": "inactive"}
    except (OSError, subprocess.TimeoutExpired):
        pass

    load = properties.get("LoadState", "")
    present = False if load == "not-found" else True if load in {
        "loaded", "masked", "error", "bad-setting", "merged",
    } else None
    state = properties.get("ActiveState") or "unknown"
    substate = properties.get("SubState") or "unknown"
    runtime = "unknown"
    if present is False:
        reason = "Служба не найдена"
    elif state == "failed" or load in {"error", "bad-setting"}:
        runtime, reason = "error", "Systemd сообщает об ошибке службы"
    elif state == "active":
        runtime, reason = "running", "Служба активна; работоспособность проверяется отдельно"
    elif state == "inactive":
        runtime, reason = "stopped", "Служба остановлена"
    elif state in {"activating", "deactivating", "reloading", "refreshing", "maintenance"}:
        reason = "Служба меняет состояние; ожидается подтверждение результата"
    restarts = properties.get("NRestarts", "")
    return {
        "unit_present": present,
        "installed": present is True,  # Legacy field describes the unit, not a component.
        "active": state == "active" and present is not False,
        "state": state,
        "substate": substate,
        "enabled": properties.get("UnitFileState") in {"enabled", "enabled-runtime", "static"},
        "unit_file_state": properties.get("UnitFileState") or "unknown",
        "restarts": int(restarts) if restarts.isdecimal() else None,
        "active_since": properties.get("ActiveEnterTimestamp", ""),
        "description": properties.get("Description", ""),
        "runtime": {"state": runtime, "reason": reason, "checked_at": checked_at},
    }


def ssh_observation(service: dict, socket: dict) -> dict:
    """A listening socket can provide SSH even while ssh.service is inactive."""
    existing = [item for item in (service, socket) if item["unit_present"] is not False]
    priority = {"error": 0, "running": 1, "unknown": 2, "stopped": 3}
    selected = min(existing, key=lambda item: priority[item["runtime"]["state"]]) if existing else service
    result = {**selected, "runtime": dict(selected["runtime"])}
    result["unit_present"] = True if any(item["unit_present"] is True for item in existing) else None if existing else False
    result["installed"] = result["unit_present"] is True
    result["active"] = service["active"] or socket["active"]
    result["enabled"] = service["enabled"] or socket["enabled"]
    result["restarts"] = service["restarts"]
    if socket["active"] and selected is socket and not service["active"]:
        result["substate"] = "socket activation"
    return result


def failed_unit_count() -> int | None:
    try:
        result = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])
