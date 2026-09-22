"""Durable admission and history for the existing application-action contract."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TERMINAL = {"succeeded", "failed", "cancelled"}
_mutex = threading.RLock()
_worker_mutex = threading.Lock()


class OperationConflict(ValueError):
    pass


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except FileNotFoundError:
        return ""
    except (OSError, IndexError):
        return None


def worker_observation(value: dict, observe) -> dict:
    if value.get("unit"):
        return observe(value["unit"])
    pid = value.get("worker_pid")
    if not isinstance(pid, int) or pid <= 0 or not value.get("worker_start"):
        return {"unit_present": None, "active": False, "state": "unknown"}
    actual = process_start(pid)
    if actual is None:
        return {"unit_present": None, "active": False, "state": "unknown"}
    active = actual == value["worker_start"]
    return {"unit_present": active, "active": active, "state": "active" if active else "inactive"}


def operation_id(value: str | None = None) -> str:
    value = value or uuid.uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Некорректный идентификатор операции")
    return value


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise OperationConflict("Состояние предыдущей операции недоступно; сначала проверьте её результат") from exc
    if not isinstance(value, dict):
        raise OperationConflict("Состояние предыдущей операции повреждено")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as target:
            os.chmod(temporary, 0o600)
            json.dump(value, target, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def persist_payload(data_dir: Path, identity: str, value: dict) -> tuple[Path, str]:
    """Caller holds admission lock. Secrets stay out of argv and public history."""
    identity = operation_id(identity)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = data_dir / "operation-inputs" / f"{identity}.json"
    if path.is_symlink():
        raise OperationConflict("Файл запроса заменён ссылкой")
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise OperationConflict("Идентификатор уже используется для другого запроса")
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("xb") as target:
            os.chmod(path, 0o600)
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
    return path, digest


def load_payload(data_dir: Path, identity: str, path: Path, digest: str) -> dict:
    expected = data_dir / "operation-inputs" / f"{operation_id(identity)}.json"
    if path.is_symlink() or path.resolve() != expected.resolve():
        raise ValueError("Invalid operation payload path")
    encoded = path.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError("Operation payload changed after admission")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("Invalid operation payload")
    return value


@contextmanager
def locked(data_dir: Path):
    with _mutex:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (data_dir / "application-action.lock").open("a+b") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


@contextmanager
def mutation_lock(data_dir: Path):
    """Share the shell worker's lock with short API mutations."""
    if not _worker_mutex.acquire(blocking=False):
        raise OperationConflict("Другая изменяющая операция уже выполняется")
    try:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (data_dir / "application-worker.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OperationConflict("Дождитесь завершения текущей фоновой операции") from None
            yield
    finally:
        _worker_mutex.release()


def require_recovery_clear(data_dir: Path, action: str | None = None) -> None:
    if action in {"mihomo-module-recover:profiles", "dns-recover", "automation-recover", "network-check", "integrity-check", "doctor", "ssh-access-rollback", "ssh-access-disable"}:
        return
    pending = [name for name in ("dns-recovery.json", "automation-recovery.json", "mihomo/profile-recovery/manifest.json", "recovery/security-recovery.txt") if (data_dir / name).exists()]
    if pending:
        raise OperationConflict("Сначала восстановите прерванные настройки DNS/расписаний/Mihomo/защиты; новые изменения заблокированы")


@contextmanager
def short_mutation(data_dir: Path):
    require_recovery_clear(data_dir)
    with mutation_lock(data_dir):
        with locked(data_dir):
            current = read(data_dir / "application-action.json")
            if current and current.get("state") not in TERMINAL:
                raise OperationConflict("Дождитесь завершения текущей операции")
        yield


def save(data_dir: Path, action_file: Path, value: dict) -> None:
    # History is authoritative for reconciling a lost response. Keep the legacy
    # pointer for the existing UI and shell readers.
    if value.get("id"):
        atomic_json(data_dir / "operations" / f"{operation_id(value['id'])}.json", value)
    atomic_json(action_file, value)
    if value.get("id"):
        from operation_log import append
        append(data_dir, value)
    if value.get("state") in TERMINAL:
        prune(data_dir, value.get("id"))


def start(data_dir: Path, action_file: Path, action: str, command: list[str], launch,
          *, request_id: str | None = None, message: str = "Команда принята", properties: tuple[str, ...] = (), observe=None) -> dict:
    identity = operation_id(request_id)
    request_hash = hashlib.sha256(json.dumps([action, command, properties]).encode()).hexdigest()
    with locked(data_dir):
        existing = read(data_dir / "operations" / f"{identity}.json")
        if existing:
            if existing.get("request_hash") != request_hash:
                raise OperationConflict("Идентификатор уже использован для другой команды")
            if observe and read(action_file).get("id") == identity:
                return reconcile(data_dir, action_file, existing, observe)
            return existing
        with mutation_lock(data_dir):
            require_recovery_clear(data_dir, action)
            previous = read(action_file)
            if previous.get("id") and observe:
                previous = reconcile(data_dir, action_file, previous, observe)
            if previous and previous.get("state") not in TERMINAL:
                raise OperationConflict("Предыдущая операция ещё не завершена или её результат неизвестен")
            unit = f"vps-control-action-{time.time_ns()}"
            value = {"id": identity, "request_hash": request_hash, "unit": f"{unit}.service", "action": action,
                     "state": "queued", "progress": 0, "message": message, "started_at": timestamp(), "updated_at": timestamp(), "boot_id": boot_id()}
            try:
                save(data_dir, action_file, value)
            except OSError:
                # A history record may have been committed before its pointer.
                # Record that no launch occurred if storage is still writable.
                try:
                    atomic_json(data_dir / "operations" / f"{identity}.json", {**value, "state": "failed", "message": "Не удалось сохранить допуск; процесс не запускался"})
                except OSError:
                    pass
                raise
    try:
        result = launch(["systemd-run", f"--unit={unit}", "--collect", "--property=Type=exec",
                         f"--setenv=VPS_CONTROL_OPERATION_ID={identity}", f"--setenv=VPS_CONTROL_OPERATION_UNIT={unit}.service",
                         *properties, *command], capture_output=True, text=True, timeout=10, check=False)
    except subprocess.TimeoutExpired:
        value.update(state="unknown", message="Подтверждение запуска не получено; проверяем состояние без повторного запуска")
    except OSError:
        value.update(state="failed", message="Не удалось запустить команду")
    else:
        if result.returncode:
            value.update(state="failed", message="Сервер отклонил запуск команды; подробности в журнале")
    with locked(data_dir):
        current = read(data_dir / "operations" / f"{identity}.json")
        if current.get("state") != "queued":
            return current
        if value["state"] != "queued":
            try:
                save(data_dir, action_file, value)
            except OSError:
                return {**value, "state": "unknown", "message": "Результат запуска временно недоступен; проверьте состояние операции"}
        return value



def write_status(data_dir: Path, action_file: Path, action: str, state: str, progress: int, message: str,
                 started_at: str, identity: str, unit: str = "") -> dict:
    identity = operation_id(identity)
    with locked(data_dir):
        previous = read(action_file)
        if previous.get("id") and previous["id"] != identity and previous.get("state") not in TERMINAL:
            raise OperationConflict("Другая операция уже владеет текущим состоянием")
        stored = read(data_dir / "operations" / f"{identity}.json")
        if stored.get("state") in TERMINAL:
            return stored
        value = {**stored, "id": identity, "action": action, "state": state, "progress": progress,
                 "message": message, "started_at": stored.get("started_at", started_at), "updated_at": timestamp(), "boot_id": stored.get("boot_id", boot_id())}
        if unit or stored.get("unit"):
            value["unit"] = unit or stored["unit"]
        pid = os.getenv("VPS_CONTROL_WORKER_PID", "")
        if pid.isdigit():
            value.update(worker_pid=int(pid), worker_start=stored.get("worker_start") or process_start(int(pid)))
        if previous.get("id") and previous["id"] != identity and stored:
            atomic_json(data_dir / "operations" / f"{identity}.json", value)
        else:
            save(data_dir, action_file, value)
        return value


def history(data_dir: Path, limit: int = 100) -> list[dict]:
    paths = sorted((path for path in (data_dir / "operations").glob("*.json") if re.fullmatch(r"[0-9a-f]{32}", path.stem)), key=lambda path: path.stat().st_mtime, reverse=True)
    items = []
    for path in paths[:limit]:
        try:
            items.append(read(path))
        except OperationConflict:
            items.append({"id": path.stem, "state": "unknown", "message": "Запись операции недоступна"})
    return items


def prune(data_dir: Path, protected: str | None, *, retention_days=None, byte_budget=None, directory: Path | None = None) -> None:
    """Only terminal operation records; current/unknown/active records survive."""
    from operation_policy import read as retention_policy

    try:
        policy = retention_policy(data_dir)
    except (OSError, ValueError):
        return  # Never delete history using guessed settings; the settings API reports the fault.
    retention_days = policy["retention_days"] if retention_days is None else retention_days
    byte_budget = policy["disk_limit_mb"] * 1024 * 1024 if byte_budget is None else byte_budget
    protected_ids = {protected}
    recovery_root = data_dir.parent if data_dir.name == "mihomo" else data_dir
    for recovery in (recovery_root / "dns-recovery.json", recovery_root / "automation-recovery.json", recovery_root / "mihomo/profile-recovery/manifest.json", recovery_root / "recovery/security-recovery.txt"):
        if recovery.exists():
            try:
                protected_ids.add(read(recovery).get("operation_id"))
            except OperationConflict:
                return  # Preserve recovery evidence when its owner is unknown.
    for marker in (data_dir / "tmp").glob("*/.vps-control-temp.json"):
        try:
            protected_ids.add(json.loads(marker.read_text(encoding="utf-8")).get("operation_id"))
        except (OSError, ValueError, AttributeError):
            continue
    paths = sorted((directory or data_dir / "operations").glob("*.json"), key=lambda path: path.stat().st_mtime)
    total = sum(path.stat().st_size for path in paths)
    threshold = time.time() - retention_days * 86400
    for path in paths:
        if path.stem in protected_ids or path.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}", path.stem):
            continue
        try:
            value = read(path)
        except OperationConflict:
            continue
        details = path.stat()
        if value.get("state") in TERMINAL and (details.st_mtime < threshold or total > byte_budget):
            if directory is None:
                payload = data_dir / "operation-inputs" / f"{path.stem}.json"
                if payload.is_symlink():
                    continue
                try:
                    payload.unlink(missing_ok=True)
                except OSError:
                    continue
            path.unlink()
            total -= details.st_size


def reconcile(data_dir: Path, action_file: Path, value: dict, observe) -> dict:
    """Called under the admission lock; missing observations never imply success."""
    if value.get("state") in TERMINAL and value.get("state") != "succeeded":
        return value
    current_boot = boot_id()
    different_boot = bool(current_boot and value.get("boot_id") and value["boot_id"] != current_boot)
    if value.get("state") == "succeeded":
        if different_boot:
            return value  # A PID from a previous boot cannot still own this work.
        snapshot = worker_observation(value, observe)
        if snapshot["unit_present"] is None:
            return {**value, "state": "unknown", "message": "Ожидаем подтверждения завершения процесса"}
        if snapshot["active"] or snapshot.get("state") in {"activating", "deactivating", "reloading"}:
            return {**value, "state": "running"}
        if snapshot.get("state") == "failed":
            value = {**value, "state": "failed", "result": "process-failed", "message": "Процесс завершился с ошибкой после последней записи прогресса"}
            save(data_dir, action_file, value)
        return value
    if different_boot:
        expected_reboot = value.get("state") == "rebooting"
        value = {**value, "state": "succeeded" if expected_reboot else "failed",
                 "result": "success" if expected_reboot else "interrupted", "updated_at": timestamp(),
                 "message": "Перезагрузка подтверждена" if expected_reboot else "Операция прервана перезагрузкой; проверьте фактическое состояние"}
        save(data_dir, action_file, value)
        return value
    snapshot = worker_observation(value, observe)
    if snapshot["unit_present"] is None:
        return {**value, "state": "unknown", "message": "Состояние процесса временно недоступно"}
    if snapshot["active"] or snapshot.get("state") in {"activating", "deactivating", "reloading"}:
        return {**value, "state": "running"}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["started_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return {**value, "state": "unknown"}
    if age < 30:
        return value  # systemd-run may still be admitting the worker.
    # A stopped or collected worker without its final marker is interrupted,
    # not successful. Keep its original progress for diagnostics.
    if snapshot["unit_present"] is False or snapshot.get("state") in {"inactive", "failed"}:
        value = {**value, "state": "failed", "result": "interrupted", "updated_at": timestamp(),
                 "message": "Выполнение прервано без подтверждённого результата. Проверьте фактическое состояние компонента."}
        save(data_dir, action_file, value)
    return value


if __name__ == "__main__":
    import sys
    # Called by the existing shell progress writer; no command execution here.
    data_dir = Path(sys.argv[1]).parent
    write_status(data_dir, Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6],
                 os.environ["VPS_CONTROL_OPERATION_ID"], os.getenv("VPS_CONTROL_OPERATION_UNIT", ""))
