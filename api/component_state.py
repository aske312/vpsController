"""Component observations built from image metadata, without changing the host."""
from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path


def observation(state, reason: str, checked_at: str | None) -> dict:
    return {"state": state, "reason": reason, "checked_at": checked_at}


def image_files(manifest: dict, interface: str, install_dir: Path) -> list[bool | None]:
    result = []
    for rule in manifest.get("installation", {}).get("required_paths", []):
        path = os.getenv(rule.get("env", "")) or rule["path"]
        path = path.replace("{interface}", interface).replace("{install_dir}", str(install_dir))
        try:
            mode = Path(path).stat().st_mode
            result.append(stat.S_ISDIR(mode) if rule.get("kind") == "directory" else stat.S_ISREG(mode))
        except FileNotFoundError:
            result.append(False)
        except OSError:
            result.append(None)
    return result


def installation_state(files: list[bool | None], unit: dict, version: str, *, templated: bool, checked_at: str) -> dict:
    if not files:
        return observation(None, "Для образа ещё не заданы признаки установки", checked_at)
    # A shared wg-quick@ template is not proof that this particular instance exists.
    evidence = any(value is True for value in files) or unit.get("active") is True or (not templated and unit.get("unit_present") is True)
    if evidence and (False in files or unit.get("unit_present") is False):
        return observation("incomplete", "Отсутствует обязательный файл или регистрация службы", checked_at)
    if None in files or unit.get("unit_present") is None:
        return observation(None, "Не удалось проверить все признаки установки", checked_at)
    if not evidence:
        return observation("not_installed", "Файлы и регистрация компонента не обнаружены", checked_at)
    if not version:
        return observation(None, "Не удалось определить установленную версию", checked_at)
    return observation("installed", "Обязательные файлы, регистрация и версия подтверждены", checked_at)


def health_state(diagnostics: dict | None, *, now: datetime) -> dict:
    if not diagnostics or diagnostics.get("status") == "pending":
        return observation("unchecked", "Проверка работоспособности ещё не выполнена", None)
    timestamp = diagnostics.get("checked_at")
    timestamp = timestamp if isinstance(timestamp, str) else None
    try:
        checked = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if checked.tzinfo is None or not 0 <= (now - checked).total_seconds() <= 180:
            raise ValueError("stale")
    except (AttributeError, TypeError, ValueError):
        return observation("unknown", "Актуальный результат проверки недоступен", timestamp)
    state = {"healthy": "healthy", "warning": "possible_issues", "critical": "error"}.get(diagnostics.get("status"), "unknown")
    reason = {"healthy": "Проверки пройдены", "possible_issues": "Проверка обнаружила возможные проблемы",
              "error": "Проверка обнаружила ошибку", "unknown": "Результат проверки неизвестен"}[state]
    return observation(state, reason, timestamp)


def component_state(image_id: str, manifest: dict, files: list[bool | None], unit: dict, version: str,
                    diagnostics: dict | None, action: dict | None) -> dict:
    now = datetime.now(timezone.utc)
    installed = installation_state(files, unit, version, templated="@" in manifest.get("service", ""), checked_at=now.isoformat())
    operation = None
    if action is None:
        operation = {"kind": None, "state": "unknown", "id": None}
    elif isinstance(action.get("action"), str) and action["action"].partition(":")[2] == image_id:
        kind = {"protocol-install": "installing", "protocol-update": "updating", "protocol-remove": "removing", "protocol-purge": "removing"}.get(action["action"].partition(":")[0])
        if kind and action.get("state") not in {"succeeded", "failed", "cancelled"}:
            operation = {"kind": kind, "state": action.get("state") or "unknown", "id": action.get("unit"), "started_at": action.get("started_at")}
    return {"installation": installed, "runtime": unit["runtime"], "health": health_state(diagnostics, now=now), "operation": operation}
