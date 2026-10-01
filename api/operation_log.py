"""Bounded, private stage journals. Never ingest command output or environment."""
from __future__ import annotations

import fcntl
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import operation_policy

MAX_EVENTS = 256
MAX_BYTES = 128 * 1024
_mutex = threading.Lock()


def location(root: Path, source: str, identity: str) -> Path:
    if source not in {"system", "mihomo", "cdn"} or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("Invalid operation journal identity")
    return root / "operation-logs" / source / f"{identity}.json"


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _mutex, (root / "operation-logs.lock").open("a+b") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def public_text(value):
    text = str(value or "")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*", "[скрыто]", text)
    text = re.sub(r"(?i)\b(?:https?|ss|vless|vmess|trojan|hysteria2|tuic)://[^\s]+", "[адрес скрыт]", text)
    text = re.sub(r"(?i)(password|passwd|token|secret|private[_ -]?key|authorization|пароль)\s*[:=]\s*[^\s,;]+", r"\1=[скрыто]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [скрыто]", text)
    return "".join(character for character in text if character >= " " or character == "\n")[:1024]


def append(data_dir: Path, value: dict, source: str | None = None):
    """Logging failures must not turn a committed mutation into a failed mutation."""
    from application_operation import atomic_json, read, timestamp
    root = operation_policy.root_for(data_dir)
    source = source or ("mihomo" if data_dir.name == "mihomo" else "system")
    try:
        path = location(root, source, value["id"])
        with locked(root):
            if path.is_symlink():
                raise ValueError("Journal replaced by symlink")
            document = read(path) or {"id": value["id"], "source": source, "events": [], "dropped": 0}
            if not isinstance(document.get("events"), list) or any(not isinstance(event, dict) for event in document["events"]) or type(document.get("dropped")) is not int:
                raise ValueError("Invalid stage journal")
            progress = value.get("progress")
            event = {"state": public_text(value.get("state")), "progress": progress if type(progress) is int else None, "message": public_text(value.get("message"))}
            previous = document["events"][-1] if document["events"] else {}
            if all(previous.get(key) == item for key, item in event.items()):
                return
            event["at"] = timestamp()
            document["events"].append(event)
            document["state"] = value.get("state", "unknown")
            document["updated_at"] = event["at"]
            import json
            while len(document["events"]) > MAX_EVENTS or len(json.dumps(document, ensure_ascii=False).encode()) > MAX_BYTES:
                document["events"].pop(0)
                document["dropped"] += 1
            atomic_json(path, document)
            _prune(root)
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Operation stage journal unavailable; operation result is preserved")


def _prune(root):
    from application_operation import read
    settings = operation_policy.read(root)
    cutoff = time.time() - settings["log_retention_days"] * 86400
    budget = settings["log_disk_limit_mb"] * 1024 * 1024
    protected = set()
    for recovery in (root / "dns-recovery.json", root / "automation-recovery.json", root / "mihomo/profile-recovery/manifest.json"):
        if recovery.exists():
            protected.add(read(recovery).get("operation_id"))
    for base in (root, root / "mihomo"):
        for marker in (base / "tmp").glob("*/.vps-control-temp.json"):
            protected.add(read(marker).get("operation_id"))
    paths = sorted((root / "operation-logs").glob("*/*.json"), key=lambda path: path.stat().st_mtime)
    total = sum(path.stat().st_size for path in paths if not path.is_symlink())
    for path in paths:
        if path.is_symlink() or path.stem in protected:
            continue
        location(root, path.parent.name, path.stem)
        journal = read(path)
        # The authoritative record can be newer than the last journal event.
        archive = {"system": root / "operations", "cdn": root / "cdn-operations", "mihomo": root / "mihomo" / "operations"}[path.parent.name]
        record = read(archive / path.name)
        state = record.get("state", journal.get("state"))
        if state not in {"succeeded", "done", "failed", "cancelled"}:
            continue
        details = path.stat()
        if details.st_mtime < cutoff or total > budget:
            path.unlink()
            total -= details.st_size


def prune(root):
    with locked(root):
        _prune(root)


def get(root, source, identity):
    from application_operation import read
    path = location(root, source, identity)
    if path.is_symlink():
        raise ValueError("Journal replaced by symlink")
    with locked(root):
        _prune(root)
        document = read(path)
        if not document:
            return {"id": identity, "source": source, "events": [], "available": False, "dropped": 0}
        if document.get("id") != identity or document.get("source") != source or not isinstance(document.get("events"), list) or type(document.get("dropped")) is not int:
            raise ValueError("Invalid stage journal")
        events = []
        for event in document["events"]:
            if not isinstance(event, dict):
                raise ValueError("Invalid stage event")
            events.append({"at": str(event.get("at", ""))[:64], "state": public_text(event.get("state")),
                           "progress": event.get("progress") if type(event.get("progress")) is int else None,
                           "message": public_text(event.get("message"))})
        return {"id": identity, "source": source, "events": events, "available": True, "dropped": document["dropped"]}
