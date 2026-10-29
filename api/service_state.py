"""Read-only systemd observations. Unit presence is not component installation."""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone

_observations: ContextVar[dict | None] = ContextVar("service_observations", default=None)
_PROPERTIES = "LoadState,ActiveState,SubState,UnitFileState,NRestarts,ActiveEnterTimestamp,Description"
_TIMER_PROPERTIES = ",LastTriggerUSec,NextElapseUSecRealtime"


@contextmanager
def observation_scope():
    """Reuse systemd reads within one response, never across HTTP requests."""
    if _observations.get() is not None:
        yield
        return
    token = _observations.set({})
    try:
        yield
    finally:
        _observations.reset(token)


def observe_service(unit: str, *, timer: bool = False) -> dict:
    scope = _observations.get()
    key = (unit, timer)
    if scope is not None and key in scope:
        return deepcopy(scope[key])
    observation = _observe_service(unit, timer=timer)
    if scope is not None:
        scope[key] = deepcopy(observation)
    return observation


def prime_services(units, *, timers=()) -> None:
    """Batch fresh observations into the current response's private scope."""
    scope = _observations.get()
    if scope is None:
        return
    keys = {(unit, False) for unit in units} | {(unit, True) for unit in timers}
    keys.difference_update(scope)
    if not keys:
        return
    names = sorted({unit for unit, _ in keys})
    try:
        result = subprocess.run(
            ["systemctl", "show", *names, "--property=Id," + _PROPERTIES + _TIMER_PROPERTIES],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return  # Individual reads retain their existing failure semantics.
    if result.returncode not in (0, 4):
        return
    checked_at = datetime.now(timezone.utc).isoformat()
    for block in result.stdout.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = properties.get("Id")
        if not properties.get("LoadState"):
            continue
        for timer in (False, True):
            key = (unit, timer)
            if key in keys:
                scope[key] = _service_observation(properties, checked_at, timer=timer)


def _observe_service(unit: str, *, timer: bool = False) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    properties = {}
    try:
        result = subprocess.run(
            ["systemctl", "show", unit,
             "--property=" + _PROPERTIES + (_TIMER_PROPERTIES if timer else "")],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if result.returncode == 0:
            properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        elif result.returncode == 4 and "LoadState=not-found" in result.stdout.splitlines():
            properties = {"LoadState": "not-found", "ActiveState": "inactive"}
    except (OSError, subprocess.TimeoutExpired):
        pass

    return _service_observation(properties, checked_at, timer=timer)


def _service_observation(properties, checked_at, *, timer=False):
    reason = "Не удалось получить состояние службы"
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
    observation = {
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
    if timer:
        observation.update({
            "last_trigger": None if present is False else properties.get("LastTriggerUSec"),
            "next_run": None if present is False else properties.get("NextElapseUSecRealtime"),
        })
    return observation


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


def observed_active(observation: dict) -> bool | None:
    """A failed read is not an observation of an inactive service."""
    if observation["unit_present"] is False:
        return False
    if observation["unit_present"] is not True or observation["runtime"]["state"] == "unknown":
        return None
    return observation["active"]


def observed_enabled(observation: dict) -> bool | None:
    if observation["unit_present"] is False:
        return False
    if observation["unit_present"] is not True:
        return None
    state = observation["unit_file_state"]
    if state in {"enabled", "enabled-runtime"}:
        return True
    if state in {"disabled", "static", "masked", "masked-runtime", "linked", "linked-runtime", "indirect", "generated", "transient"}:
        return False
    return None


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
