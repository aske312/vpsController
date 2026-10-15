"""Self-contained release recovery, retained outside the replaceable application tree.

Manual recovery: sudo python3 /var/lib/vps-control/recovery/application-update/recover.py rollback
The command takes the same worker lock as the panel; it does not replay the update.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

DIRECTORY = "recovery/application-update"
ORIGIN = ".application-recovery-origin"


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".recovery-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def marker(data: Path) -> Path:
    # Older releases already understand this barrier. Never replace a marker
    # belonging to security recovery, and only remove our exact ownership token.
    return data / "recovery/security-recovery.txt"


def status(data: Path) -> dict:
    root = data / DIRECTORY
    required = root.exists() or root.is_symlink()
    command = "rollback"
    if required:
        try:
            value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("phase") == "committed":
                command = "commit"
        except (OSError, ValueError):
            pass  # A corrupt journal still blocks all mutations.
    return {"application_required": required, "application_command": command}


def prepare(data: Path, installed: Path, previous: Path, files: list[Path], snippets: Path,
            operation_id: str, legacy: bool = False, candidate_backup: Path | None = None) -> dict:
    data, installed, previous = data.absolute(), installed.absolute(), previous.absolute()
    root = data / DIRECTORY
    if root.exists() or root.is_symlink() or marker(data).exists() or marker(data).is_symlink():
        raise ValueError("Предыдущее восстановление ещё не завершено")
    if installed.is_symlink() or not installed.is_dir() or previous.exists() or previous.is_symlink():
        raise ValueError("Небезопасный или занятый путь версии приложения")
    if previous.parent != installed.parent or not previous.name.startswith(installed.name + ".rollback."):
        raise ValueError("Неожиданный путь предыдущей версии")
    if candidate_backup is not None and (candidate_backup.absolute() != data / "test-app-backup"
                                        or candidate_backup.is_symlink() or not candidate_backup.is_dir()):
        raise ValueError("Неожиданный путь сохранённой production-версии")
    token = uuid.uuid4().hex
    value = {"schema": 1, "kind": "application-update", "token": token, "operation_id": operation_id,
             "phase": "preparing", "installed": str(installed), "previous": str(previous),
             "failed": str(data / "tmp" / ("interrupted-update." + token)), "legacy": legacy,
             "files": [], "snippets": str(snippets.absolute()), "test_rollback": candidate_backup is not None}
    if candidate_backup is not None:
        value["failed"] = str(candidate_backup.absolute())
    root.mkdir(parents=True, mode=0o700)
    shutil.copy2(Path(__file__), root / "recover.py")
    atomic(root / "manifest.json", value)
    # Exclusive creation protects existing security recovery even if another
    # writer unexpectedly races preparation.
    with marker(data).open("x", encoding="utf-8") as stream:
        os.chmod(marker(data), 0o600)
        json.dump({"kind": value["kind"], "token": token, "operation_id": operation_id}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(marker(data).parent)
    for index, path in enumerate(files):
        path = path.absolute()
        if path.is_symlink():
            raise ValueError("Восстановление не поддерживает перенаправленные файлы служб")
        backup = root / f"file-{index}"
        if path.exists():
            shutil.copy2(path, backup)
        value["files"].append({"path": str(path), "backup": backup.name, "present": path.exists()})
    if snippets.is_symlink():
        raise ValueError("Небезопасный каталог конфигурации шлюза")
    if snippets.exists():
        shutil.copytree(snippets, root / "snippets", symlinks=False)
    (installed / ORIGIN).write_text(token, encoding="utf-8")
    for path in [installed / ORIGIN, *(p for p in root.rglob("*") if p.is_file())]:
        with path.open("r+b" if os.name == "nt" else "rb") as stream:
            os.fsync(stream.fileno())
    for path in [*(p for p in root.rglob("*") if p.is_dir()), root, installed]:
        sync_directory(path)
    value["phase"] = "prepared"
    atomic(root / "manifest.json", value)
    return value


def load(data: Path) -> dict:
    root = data / DIRECTORY
    value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if value.get("schema") != 1 or value.get("kind") != "application-update" or not re.fullmatch(r"[0-9a-f]{32}", value.get("token", "")):
        raise ValueError("Повреждённое состояние восстановления приложения")
    installed, previous, failed = (Path(value[key]) for key in ("installed", "previous", "failed"))
    if (not installed.is_absolute() or installed == Path(installed.anchor) or installed.is_symlink()
            or previous.parent != installed.parent or not previous.name.startswith(installed.name + ".rollback.")
            or failed != (data.absolute() / "test-app-backup" if value.get("test_rollback") is True
                          else data.absolute() / "tmp" / ("interrupted-update." + value["token"]))
            or previous.is_symlink() or failed.is_symlink() or root.is_symlink()
            or (data / "tmp").is_symlink()):
        raise ValueError("Небезопасные пути восстановления приложения")
    if marker(data).exists():
        barrier = json.loads(marker(data).read_text(encoding="utf-8"))
        if barrier.get("kind") != value["kind"] or barrier.get("token") != value["token"]:
            raise ValueError("Защитный маркер принадлежит другому восстановлению")
    elif value["phase"] not in {"preparing", "restored", "committed"}:
        raise ValueError("Защитный маркер восстановления отсутствует")
    return value


def phase(data: Path, value: dict, name: str) -> None:
    value["phase"] = name
    atomic(data / DIRECTORY / "manifest.json", value)


def clear(data: Path, value: dict) -> None:
    load(data)  # Recheck ownership immediately before releasing admission.
    # All changes and readiness checks are complete before clearing admission.
    # Preserve evidence by a single directory rename rather than deleting the
    # recovery helper while cleanup is still fallible.
    archive = data / "recovery" / ("completed-application-update." + value["token"])
    if archive.exists() or archive.is_symlink():
        raise ValueError("Путь архива восстановления уже занят")
    marker(data).unlink(missing_ok=True)
    sync_directory(marker(data).parent)
    (data / DIRECTORY).rename(archive)
    sync_directory(marker(data).parent)


def run(*command: str) -> str:
    result = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    return result.stdout


def healthy(value: dict) -> None:
    services = ["vps-control-api.service"]
    if not value["legacy"]:
        services.extend(["vps-control-web.service", "caddy.service"])
    run("systemctl", "is-active", "--quiet", *services)
    for port, suffix in ((8000, "/api/health"), (3000, "/")):
        run("curl", "--fail", "--silent", "--retry", "10", "--retry-connrefused", "--retry-delay", "2",
            f"http://127.0.0.1:{port}{suffix}")


def rollback(data: Path) -> None:
    value = load(data)
    root, installed, previous, failed = data / DIRECTORY, Path(value["installed"]), Path(value["previous"]), Path(value["failed"])
    if value["phase"] == "committed":
        raise ValueError("Новая версия уже принята; повтор отката запрещён")
    if value["phase"] == "restored":
        healthy(value)
        clear(data, value)
        return
    if value["phase"] == "preparing":
        # The caller cannot swap until prepare returns successfully.
        clear(data, value)
        return
    source = previous if previous.exists() else installed
    if (not source.is_dir() or (source / ORIGIN).is_symlink()
            or (source / ORIGIN).read_text(encoding="utf-8") != value["token"]):
        raise ValueError("Исходная версия приложения недоступна; изменения заблокированы")
    if previous.exists() and installed.exists() and failed.exists():
        raise ValueError("Путь сохранения отклонённой версии уже занят")
    if (installed / "venv").is_symlink() or (previous / "venv").is_symlink():
        raise ValueError("Небезопасный путь Python-окружения приложения")
    run("systemctl", "stop", "vps-control-web.service", "vps-control-api.service")
    if previous.exists():
        if (previous / ORIGIN).read_text(encoding="utf-8") != value["token"]:
            raise ValueError("Предыдущая версия не принадлежит этой операции")
        if installed.exists():
            if failed.exists():
                raise ValueError("Путь сохранения отклонённой версии уже занят")
            if (installed / "venv").is_dir() and not (previous / "venv").exists():
                shutil.move(str(installed / "venv"), str(previous / "venv"))
                sync_directory(installed)
                sync_directory(previous)
            failed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            installed.rename(failed)
            sync_directory(failed.parent)
            sync_directory(installed.parent)
        previous.rename(installed)
        sync_directory(installed.parent)
    # This identity survives interruption before, between and after the renames.
    if not installed.is_dir() or (installed / ORIGIN).read_text(encoding="utf-8") != value["token"]:
        raise ValueError("Исходная версия приложения недоступна; изменения заблокированы")
    phase(data, value, "restoring")
    for item in value["files"]:
        path, backup = Path(item["path"]), root / item["backup"]
        if path.is_symlink() or backup.parent != root or backup.is_symlink():
            raise ValueError("Небезопасный путь конфигурации восстановления")
        if item["present"]:
            shutil.copy2(backup, path)
        else:
            path.unlink(missing_ok=True)
    snippets = Path(value["snippets"])
    if snippets == Path(snippets.anchor) or snippets.is_symlink():
        raise ValueError("Небезопасный каталог шлюза")
    snippets.mkdir(parents=True, exist_ok=True)
    for snippet in snippets.glob("*.caddy"):
        snippet.unlink()
    if (root / "snippets").exists():
        shutil.copytree(root / "snippets", snippets, dirs_exist_ok=True)
    for path in [*(Path(item["path"]) for item in value["files"] if item["present"]),
                 *(p for p in snippets.rglob("*") if p.is_file())]:
        with path.open("r+b" if os.name == "nt" else "rb") as stream:
            os.fsync(stream.fileno())
        sync_directory(path.parent)
    sync_directory(snippets)
    run("systemctl", "daemon-reload")
    if value["legacy"]:
        run("systemctl", "stop", "vps-control-web.service", "caddy.service")
        run("docker", "start", "vps-control-web-1", "vps-control-gateway-1")
        run("systemctl", "restart", "vps-control-api.service")
    else:
        run("systemctl", "restart", "vps-control-api.service", "vps-control-web.service")
        run("systemctl", "reload-or-restart", "caddy.service")
    if run("systemctl", "list-unit-files", "--no-legend", "vps-control-mihomo-manager.service").strip():
        run("systemctl", "restart", "vps-control-mihomo-manager.service")
        run("systemctl", "is-active", "--quiet", "vps-control-mihomo-manager.service")
    healthy(value)
    phase(data, value, "restored")
    # Keep the rejected tree as evidence; no recursive delete during recovery.
    clear(data, value)
    (installed / ORIGIN).unlink(missing_ok=True)


def commit(data: Path) -> None:
    value = load(data)
    healthy({**value, "legacy": False})
    phase(data, value, "committed")
    clear(data, value)


@contextmanager
def worker_lock(data: Path, inherited_fd: int | None):
    with (data / "application-worker.lock").open("a+b") as stream:
        fd = stream.fileno() if inherited_fd is None else inherited_fd
        # A shell child shares its inherited lock description. Manual invocation
        # opens a new description and cannot race a still-running worker.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "rollback", "commit"))
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/vps-control"))
    parser.add_argument("--worker-lock-fd", type=int)
    parser.add_argument("--installed", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--file", action="append", type=Path, default=[])
    parser.add_argument("--snippets", type=Path)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--candidate-backup", type=Path)
    args = parser.parse_args()
    try:
        with worker_lock(args.data_dir, args.worker_lock_fd):
            if args.command == "prepare":
                prepare(args.data_dir, args.installed, args.previous, args.file, args.snippets,
                        os.environ.get("VPS_CONTROL_OPERATION_ID", ""), args.legacy, args.candidate_backup)
            elif args.command == "rollback":
                rollback(args.data_dir)
            else:
                commit(args.data_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Восстановление приложения не завершено; новые изменения заблокированы: {exc}\n")


if __name__ == "__main__":
    main()
