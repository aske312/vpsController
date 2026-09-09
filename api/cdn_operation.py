"""Durable CDN command results, independent of the HTTP connection being changed."""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

import cdn_security

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
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(operation, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


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
            ["systemctl", "show", f"vps-control-cdn-security-{operation_id}.service", "--property=ActiveState", "--property=LoadState"],
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


def start(enabled: bool, operation_id: str, control_command: str) -> dict:
    path = operation_path(operation_id)
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    with (DIRECTORY / "start.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            existing = status(operation_id)
            if existing["enabled"] != enabled:
                raise OperationConflict("Эта команда уже отправлена с другой настройкой")
            return existing
        current = status()
        if current and current["state"] in ACTIVE:
            raise OperationConflict("Проверка CF уже выполняется. Дождитесь результата.")
        operation = {
            "id": operation_id, "enabled": enabled, "state": "queued", "progress": 0,
            "message": "Команда принята", "created_at": time.time(),
        }
        # Persist before launch: a fast worker must not have its result overwritten.
        write(operation)
        pointer = DIRECTORY / "current.tmp"
        pointer.write_text(operation_id)
        pointer.replace(DIRECTORY / "current")
        try:
            result = subprocess.run(
                ["systemd-run", f"--unit=vps-control-cdn-security-{operation_id}", "--collect", "--property=Type=exec",
                 control_command, "cdn-security", "enable" if enabled else "disable", operation_id],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except subprocess.TimeoutExpired:
            # Launch acknowledgement may be lost; only the status reader resolves it.
            return operation
        except OSError:
            traceback.print_exc()
            result = None
        if result is None or result.returncode:
            existing = status(operation_id)
            if existing["state"] != "queued":
                return existing
            if result is not None:
                print(result.stderr, file=sys.stderr)
            operation.update(state="failed", message="Не удалось запустить проверку CF. Подробности сохранены в журнале.")
            write(operation)
        return status(operation_id)


def run(operation_id: str) -> None:
    operation = json.loads(operation_path(operation_id).read_text(encoding="utf-8"))

    def progress(value: int, message: str) -> None:
        operation.update(state="running", progress=value, message=message)
        write(operation)

    try:
        progress(5, "Подготовка проверки CF")
        cdn_security.configure_aop(operation["enabled"], progress=progress)
    except Exception:
        traceback.print_exc()
        operation.update(state="failed", message="Проверка CF не завершена. Проверьте настройки Cloudflare и журнал команды.")
        write(operation)
        raise
    operation.update(state="succeeded", progress=100, message="Проверка сертификата CF включена" if operation["enabled"] else "Проверка сертификата CF выключена")
    write(operation)


if __name__ == "__main__":
    run(sys.argv[1])
