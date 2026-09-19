"""Apply the existing timer renderer with rollback of settings and timer state."""
from __future__ import annotations

import os
import base64
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
            restored = True
            for unit in states:
                restored &= command("systemctl", "disable", "--now", unit).returncode == 0
            for path, snapshot in snapshots.items():
                try:
                    if snapshot is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(snapshot[0])
                        path.chmod(snapshot[1])
                except OSError:
                    restored = False
            restored &= command("systemctl", "daemon-reload").returncode == 0
            for unit, values in states.items():
                if values.get("UnitFileState") == "enabled":
                    restored &= command("systemctl", "enable", unit).returncode == 0
                if values.get("ActiveState") == "active":
                    restored &= command("systemctl", "start", unit).returncode == 0
            for unit, expected in states.items():
                try:
                    result = command("systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState")
                    observed = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                    restored &= result.returncode in (0, 4) and observed.get("LoadState") == expected.get("LoadState")
                    if expected.get("LoadState") == "loaded":
                        restored &= all(observed.get(key) == expected.get(key) for key in ("ActiveState", "UnitFileState"))
                except (OSError, subprocess.SubprocessError):
                    restored = False
            try:
                if previous is None:
                    settings_file.unlink(missing_ok=True)
                else:
                    settings_file.write_bytes(previous)
                    settings_file.chmod(0o600)
            except OSError:
                restored = False
            if restored:
                recovery_file.unlink()
            message = "Не удалось применить расписания; прежние настройки восстановлены" if restored else "Не удалось полностью восстановить расписания; требуется проверка служб"
            raise RuntimeError(message) from cause
