"""Durable CDN command results, independent of the HTTP connection being changed."""
from __future__ import annotations

import fcntl
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import cdn_security
import ech_settings
import application_operation
from service_state import observe_service

DIRECTORY = Path("/var/lib/vps-control/cdn-operations")
ACTIVE = {"queued", "running"}


class OperationConflict(ValueError):
    pass


def operation_path(operation_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError("Invalid operation ID")
    return DIRECTORY / f"{operation_id}.json"


def write(operation: dict) -> None:
    path = operation_path(operation["id"])
    application_operation.atomic_json(path, operation)
    from operation_log import append
    append(DIRECTORY.parent, operation, "cdn")
    current = application_operation.read(DIRECTORY.parent / "operations" / f"{operation['id']}.json")
    if current:
        application_operation.write_status(
            DIRECTORY.parent, DIRECTORY.parent / "application-action.json", current["action"],
            operation["state"], operation.get("progress", 0), operation.get("message", ""),
            current["started_at"], operation["id"], current.get("unit", ""),
        )
    if operation.get("state") in application_operation.TERMINAL:
        application_operation.prune(DIRECTORY.parent, operation["id"], directory=DIRECTORY)


def status(operation_id: str | None = None) -> dict | None:
    if operation_id is None:
        try:
            operation_id = (DIRECTORY / "current").read_text().strip()
        except FileNotFoundError:
            return None
    path = operation_path(operation_id)
    operation = json.loads(path.read_text(encoding="utf-8"))
    if operation["state"] not in ACTIVE or time.time() - operation["created_at"] < 15:
        return operation
    # Detect a reboot/killed worker even after systemd has collected its unit.
    # A successful exit is never inferred from an absent unit or settings alone.
    try:
        result = subprocess.run(
            ["systemctl", "show", operation.get("unit") or f"vps-control-cdn-security-{operation_id}.service", "--property=ActiveState", "--property=LoadState"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return operation
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if properties.get("ActiveState") not in {"inactive", "failed"} and properties.get("LoadState") != "not-found":
        return operation
    # Re-read after querying systemd: the worker can finish during the query.
    operation = json.loads(path.read_text(encoding="utf-8"))
    if operation["state"] in ACTIVE:
        return {**operation, "state": "failed", "message": "Выполнение прервано. Проверьте состояние настройки перед новой командой."}
    return operation


def start(enabled: bool, operation_id: str, control_command: str, *, domain: str | None = None) -> dict:
    path = operation_path(operation_id)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / "start.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            existing = status(operation_id)
            if existing["enabled"] != enabled or existing.get('domain') != domain:
                raise OperationConflict("Эта команда уже отправлена с другой настройкой")
            return existing
        current = status()
        if current and current["state"] in ACTIVE:
            raise OperationConflict("Операция настройки шлюза уже выполняется. Дождитесь результата.")
        operation = {
            "id": operation_id, "enabled": enabled, "state": "queued", "progress": 0,
            "message": "Команда принята", "created_at": time.time(),
        }
        if domain is not None:
            operation.update(kind='ech', domain=domain)
        def launch(command, **kwargs):
            # Common admission reserves the server before this CDN-specific
            # record is published. Both records precede the actual launch.
            unit = next(item.split("=", 1)[1] for item in command if item.startswith("--unit="))
            operation["unit"] = f"{unit}.service"
            write(operation)
            pointer = DIRECTORY / "current.tmp"
            pointer.write_text(operation_id)
            pointer.replace(DIRECTORY / "current")
            return subprocess.run(command, **kwargs)

        try:
            admitted = application_operation.start(
                DIRECTORY.parent, DIRECTORY.parent / "application-action.json",
                f"ech:{domain}" if domain is not None else "cdn-security",
                [control_command, "cdn-security", "enable" if enabled else "disable", operation_id],
                launch, request_id=operation_id, observe=observe_service,
            )
        except application_operation.OperationConflict as exc:
            raise OperationConflict(str(exc)) from exc
        if admitted["state"] == "failed":
            operation.update(state="failed", message=admitted["message"])
            write(operation)
        return status(operation_id)


def run(operation_id: str) -> None:
    # The durable reservation blocks API mutations before the worker starts;
    # the same lock as other workers protects the actual gateway change.
    with application_operation.mutation_lock(DIRECTORY.parent):
        current = application_operation.read(DIRECTORY.parent / "application-action.json")
        if current and current.get("id") != operation_id:
            raise OperationConflict("Другая операция уже управляет сервером")
        _run(operation_id)


def _run(operation_id: str) -> None:
    operation = json.loads(operation_path(operation_id).read_text(encoding="utf-8"))

    def progress(value: int, message: str) -> None:
        operation.update(state="running", progress=value, message=message)
        write(operation)

    try:
        if operation.get('kind') == 'ech':
            progress(5, 'Подготовка ECH')
            operation['result'] = ech_settings.prepare(operation['domain'], progress=progress)
        else:
            progress(5, "Подготовка проверки CF")
            cdn_security.configure_aop(operation["enabled"], progress=progress)
    except Exception as exc:
        traceback.print_exc()
        operation.update(state="failed", message="Проверка CF не завершена. Проверьте настройки Cloudflare и журнал команды.")
        if operation.get('kind') == 'ech':
            operation['message'] = str(exc) if isinstance(exc, ValueError) else 'Не удалось настроить ECH. Подробности сохранены в журнале команды.'
        write(operation)
        raise
    operation.update(state="succeeded", progress=100, message="Проверка сертификата CF включена" if operation["enabled"] else "Проверка сертификата CF выключена")
    if operation.get('kind') == 'ech':
        operation['message'] = 'ECH настроен на сервере. Добавьте выданную HTTPS-запись в DNS.'
    write(operation)


if __name__ == "__main__":
    run(sys.argv[1])
