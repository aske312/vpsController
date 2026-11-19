"""Retention policy shared by system, CDN and Mihomo operation archives."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

DEFAULTS = {"max_entries": 1000, "retention_days": 90, "disk_limit_mb": 20, "log_retention_days": 7, "log_disk_limit_mb": 20, "revision": "0" * 32}


def root_for(data_dir: Path) -> Path:
    return data_dir.parent if data_dir.name == "mihomo" else data_dir


def read(data_dir: Path) -> dict:
    path = root_for(data_dir) / "operation-history-settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS)
    if not isinstance(value, dict) or type(value.get("retention_days")) is not int or not 1 <= value["retention_days"] <= 730 or type(value.get("disk_limit_mb")) is not int or not 1 <= value["disk_limit_mb"] <= 1024:
        raise ValueError("Настройки хранения истории повреждены")
    if not isinstance(value.get("revision"), str) or len(value["revision"]) != 32:
        raise ValueError("Ревизия хранения истории повреждена")
    for key, maximum in (("log_retention_days", 730), ("log_disk_limit_mb", 1024)):
        value.setdefault(key, DEFAULTS[key])
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise ValueError("Настройки журнала операций повреждены")
    value.setdefault('max_entries', DEFAULTS['max_entries'])
    if type(value['max_entries']) is not int or not 100 <= value['max_entries'] <= 10000:
        raise ValueError('Некорректный лимит количества операций')
    return value


def configure(data_dir: Path, values: dict, expected_revision: str) -> dict:
    from application_operation import locked, atomic_json, OperationConflict

    root = root_for(data_dir)
    with locked(root):
        if read(root)["revision"] != expected_revision:
            raise OperationConflict("Настройки изменились; загрузите актуальные значения. Ваши правки не применены")
        settings = {"retention_days": values["retention_days"], "disk_limit_mb": values["disk_limit_mb"], "revision": uuid.uuid4().hex}
        for key in ("log_retention_days", "log_disk_limit_mb"):
            settings[key] = values.get(key, read(root)[key])
        settings['max_entries'] = values.get('max_entries', read(root)['max_entries'])
        if type(settings['max_entries']) is not int or not 100 <= settings['max_entries'] <= 10000:
            raise ValueError('Некорректный лимит количества операций')
        atomic_json(root / "operation-history-settings.json", settings)
        return settings


def status(data_dir: Path) -> dict:
    root = root_for(data_dir)
    settings = read(root)
    budget = settings["disk_limit_mb"] * 1024 * 1024
    sources = {}
    for name, directory in (("system", root / "operations"), ("mihomo", root / "mihomo" / "operations"), ("cdn", root / "cdn-operations")):
        paths = [path for path in directory.glob("*.json") if not path.is_symlink() and len(path.stem) == 32 and all(c in '0123456789abcdef' for c in path.stem)]
        size = sum(path.stat().st_size for path in paths)
        sources[name] = {"used_bytes": size, "entries": len(paths), "over_limit": size > budget or len(paths) > settings['max_entries']}
    logs_size = sum(path.stat().st_size for path in (root / "operation-logs").glob("*/*.json") if not path.is_symlink())
    return {**settings, "sources": sources, "logs": {"used_bytes": logs_size, "over_limit": logs_size > settings["log_disk_limit_mb"] * 1024 * 1024}}
