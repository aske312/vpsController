"""Apply the existing timer renderer with rollback of settings and timer state."""
from __future__ import annotations

import os
import base64
import json
import subprocess
import tempfile
from pathlib import Path

from application_operation import atomic_json

KINDS = ("reboot", "cleanup", "update")


def configure(settings: dict, settings_file: Path, control: list[str], *,
              unit_dir=Path("/etc/systemd/system"), run=subprocess.run) -> None:
    recovery_file = settings_file.with_name("automation-recovery.json")
    if recovery_file.exists():
        raise RuntimeError("Предыдущая настройка расписаний прервана; проверьте службы и сохранённый снимок восстановления перед новым применением")

    def command(*args):
        return run(list(args), capture_output=True, text=True, timeout=30, check=False)

    states = {}
    snapshots = {}
    for kind in KINDS:
        unit = f"vps-control-auto-{kind}.timer"
        observation = command("systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState")
        values = dict(line.split("=", 1) for line in observation.stdout.splitlines() if "=" in line)
        if observation.returncode not in (0, 4) or values.get("LoadState") not in ("loaded", "not-found"):
            raise RuntimeError("Не удалось проверить текущее состояние расписаний")
        if values.get("LoadState") == "loaded" and (values.get("ActiveState") not in ("active", "inactive") or
                values.get("UnitFileState") not in ("enabled", "disabled")):
            raise RuntimeError("Расписание меняет состояние или настроено нестандартно; сначала проверьте службу")
        states[unit] = values
        for suffix in ("service", "timer"):
            path = unit_dir / f"vps-control-auto-{kind}.{suffix}"
            if path.is_symlink():
                raise RuntimeError("Нельзя заменить ссылку вместо файла расписания")
            snapshots[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None

    settings_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = settings_file.read_bytes() if settings_file.exists() else None
    with tempfile.TemporaryDirectory(prefix="automation-", dir=settings_file.parent) as temporary:
        candidate = Path(temporary) / "candidate.json"
        atomic_json(candidate, settings)
        # Persist before touching systemd; a killed worker must not lose the
        # original files or allow the next attempt to overwrite the snapshot.
        atomic_json(recovery_file, {
            "operation_id": os.environ.get("VPS_CONTROL_OPERATION_ID"),
            "states": states,
            "settings": base64.b64encode(previous).decode("ascii") if previous is not None else None,
            "files": {str(path): {"content": base64.b64encode(snapshot[0]).decode("ascii"), "mode": snapshot[1]}
                      if snapshot is not None else None for path, snapshot in snapshots.items()},
        })
        try:
            result = run([*control, "automation-apply", str(candidate)], capture_output=True, text=True,
                         timeout=120, check=False, env={**os.environ, "VPS_CONTROL_AUTOMATION_CHILD": "1"})
            if result.returncode:
                raise RuntimeError("Не удалось применить расписания")
            for kind in KINDS:
                unit = f"vps-control-auto-{kind}.timer"
                values = command("systemctl", "show", unit, "--property=ActiveState,UnitFileState")
                observed = dict(line.split("=", 1) for line in values.stdout.splitlines() if "=" in line)
                enabled = settings[kind]["enabled"]
                if values.returncode or observed.get("ActiveState") != ("active" if enabled else "inactive") or observed.get("UnitFileState") != ("enabled" if enabled else "disabled"):
                    raise RuntimeError("Состояние таймеров не подтверждено")
            atomic_json(settings_file, settings)
            recovery_file.unlink()
        except Exception as cause:
            try:
                recover(settings_file, unit_dir=unit_dir, run=run)
            except Exception as recovery_error:
                raise RuntimeError("Не удалось полностью восстановить расписания; требуется проверка служб") from recovery_error
            raise RuntimeError("Не удалось применить расписания; прежние настройки восстановлены") from cause


def recover(settings_file: Path, *, unit_dir=Path("/etc/systemd/system"), run=subprocess.run) -> None:
    """Restore the persisted snapshot under the caller's mutation lock.

    Validate the complete snapshot before any command or write. Keep it until
    all files, settings and systemd observations confirm recovery.
    """
    recovery_file = settings_file.with_name("automation-recovery.json")
    if not recovery_file.exists():
        return
    value = json.loads(recovery_file.read_text(encoding="utf-8"))
    expected_units = {f"vps-control-auto-{kind}.timer" for kind in KINDS}
    expected_paths = {str(unit_dir / f"vps-control-auto-{kind}.{suffix}") for kind in KINDS for suffix in ("service", "timer")}
    if not isinstance(value, dict) or not isinstance(value.get("states"), dict) or set(value["states"]) != expected_units or not isinstance(value.get("files"), dict) or set(value["files"]) != expected_paths:
        raise ValueError("Неверный снимок восстановления расписаний")
    files = {}
    for name, snapshot in value["files"].items():
        path = Path(name)
        if path.is_symlink():
            raise ValueError("Файл расписания заменён ссылкой; восстановление остановлено")
        if snapshot is None:
            files[path] = None
        else:
            if not isinstance(snapshot, dict) or type(snapshot.get("mode")) is not int or not 0 <= snapshot["mode"] <= 0o777:
                raise ValueError("Неверные права в снимке расписаний")
            files[path] = (base64.b64decode(snapshot["content"], validate=True), snapshot["mode"])
    for state in value["states"].values():
        if not isinstance(state, dict) or state.get("LoadState") not in {"loaded", "not-found"}:
            raise ValueError("Неверное состояние службы в снимке")
        if state["LoadState"] == "loaded" and (state.get("ActiveState") not in {"active", "inactive"} or state.get("UnitFileState") not in {"enabled", "disabled"}):
            raise ValueError("Неподдерживаемое состояние службы в снимке")
    previous = base64.b64decode(value["settings"], validate=True) if value["settings"] is not None else None
    if settings_file.is_symlink():
        raise ValueError("Настройки заменены ссылкой; восстановление остановлено")

    def command(*args):
        return run(list(args), capture_output=True, text=True, timeout=30, check=False)

    failed = []
    def attempt(callback):
        try:
            callback()
        except Exception as error:
            failed.append(type(error).__name__)

    def restore(path, snapshot):
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=".automation-restore-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(snapshot[0])
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(snapshot[1])
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    # Missing timers may reject disable; readback below is the authority.
    for unit in expected_units:
        attempt(lambda unit=unit: command("systemctl", "disable", "--now", unit))
    for path, snapshot in files.items():
        attempt(lambda path=path, snapshot=snapshot: restore(path, snapshot))
    attempt(lambda: restore(settings_file, (previous, 0o600) if previous is not None else None))
    result = command("systemctl", "daemon-reload")
    if result.returncode:
        failed.append("daemon-reload")
    if not failed:
        for unit, state in value["states"].items():
            if state.get("UnitFileState") == "enabled":
                attempt(lambda unit=unit: command("systemctl", "enable", unit))
            if state.get("ActiveState") == "active":
                attempt(lambda unit=unit: command("systemctl", "start", unit))
    for unit, expected in value["states"].items():
        def verify(unit=unit, expected=expected):
            result = command("systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState")
            observed = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if result.returncode not in (0, 4) or observed.get("LoadState") != expected["LoadState"]:
                raise RuntimeError("Не подтверждена установка таймера")
            if expected["LoadState"] == "loaded" and any(observed.get(key) != expected[key] for key in ("ActiveState", "UnitFileState")):
                raise RuntimeError("Не подтверждено восстановление таймера")
        attempt(verify)
    if failed:
        raise RuntimeError("Восстановление расписаний не подтверждено; снимок сохранён")
    recovery_file.unlink()
