"""Clean only temporary artifacts registered by application operations."""
import json
import os
import re
import shutil
from pathlib import Path

from application_operation import atomic_json, operation_id, read, TERMINAL


def temporary_path(data_dir: Path, path: Path) -> Path:
    root = data_dir / "tmp"
    if root.is_symlink() or path.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError("Temporary directory cannot be a symlink")
    resolved = path.resolve()
    if resolved.parent != root.resolve() or not re.fullmatch(r"(?:test-)?update\.[A-Za-z0-9]+", resolved.name):
        raise ValueError("Temporary path is outside the operation workspace")
    return resolved


def register(data_dir: Path, path: Path, identity: str) -> None:
    target = temporary_path(data_dir, path)
    atomic_json(target / ".vps-control-temp.json", {"schema": 1, "operation_id": operation_id(identity)})


def cleanup(data_dir: Path) -> dict:
    result = {"removed": 0, "preserved": 0}
    root = data_dir / "tmp"
    if root.is_symlink():
        raise ValueError("Temporary directory cannot be a symlink")
    for candidate in root.glob("*"):
        try:
            target = temporary_path(data_dir, candidate)
            marker = target / ".vps-control-temp.json"
            if marker.is_symlink():
                raise ValueError("Untrusted artifact marker")
            value = json.loads(marker.read_text(encoding="utf-8"))
            if value.get("schema") != 1:
                raise ValueError("Unknown artifact schema")
            identity = operation_id(value.get("operation_id", "invalid"))
            action = read(data_dir / "operations" / f"{identity}.json")
            if action.get("state") not in TERMINAL:
                result["preserved"] += 1
                continue
            # rmtree removes contained links themselves, never their targets.
            shutil.rmtree(target)
            result["removed"] += 1
        except (OSError, ValueError, AttributeError):
            result["preserved"] += 1
    return result


if __name__ == "__main__":
    import sys
    data_dir = Path(sys.argv[2])
    if sys.argv[1] == "register":
        register(data_dir, Path(sys.argv[3]), os.environ["VPS_CONTROL_OPERATION_ID"])
    elif sys.argv[1] == "cleanup":
        print(json.dumps(cleanup(data_dir)))
    else:
        raise SystemExit("Unknown cleanup action")
