"""Explicit component ownership and private configuration snapshots.

Image manifests remain the source of paths and defaults. Discovering a systemd
unit never grants ownership. This module does not start or restart services.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.RLock()
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SNAPSHOT_FILES = 5000


class RegistryError(ValueError):
    pass


def component_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise RegistryError("Некорректный идентификатор компонента")
    return value


def paths_from_manifest(manifest: dict, interface: str, install_dir: Path, data_dir: Path, *, kind="backup_paths") -> list[Path]:
    paths = []
    for rule in manifest.get("installation", {}).get(kind, []):
        value = os.getenv(rule.get("env", "")) or rule["path"]
        for key, replacement in (("interface", interface), ("install_dir", install_dir), ("data_dir", data_dir)):
            value = value.replace("{" + key + "}", str(replacement))
        paths.append(Path(value))
    return paths


def inventory(paths: list[Path]) -> list[dict]:
    entries = {}
    size = 0
    approved = [path.absolute() for path in paths if not path.is_symlink()]
    for root in paths:
        # Following a symlink could silently exclude its target from recovery.
        if any(parent.is_symlink() for parent in root.parents):
            raise RegistryError("Конфигурация использует символическую ссылку; автоматическое принятие недоступно")
        try:
            root.stat()
        except FileNotFoundError:
            continue
        candidates = [root]
        if root.is_dir():
            # os.walk reports inaccessible directories through onerror instead
            # of silently producing an incomplete backup like Path.rglob can.
            def onerror(error):
                raise error
            for folder, directories, files in os.walk(root, onerror=onerror, followlinks=False):
                candidates.extend(Path(folder) / name for name in directories + files)
        for path in candidates:
            name = str(path.absolute())
            if name in entries:
                continue
            if len(entries) >= MAX_SNAPSHOT_FILES:
                raise RegistryError("Объём конфигурации превышает предел резервной копии (5000 файлов)")
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                target = path.resolve(strict=True)
                if not any(target == allowed or (allowed.is_dir() and target.is_relative_to(allowed)) for allowed in approved):
                    raise RegistryError("Ссылка ведёт за пределы конфигурации компонента; принятие остановлено")
                entries[name] = {"path": name, "kind": "symlink", "target": os.readlink(path), "size": 0, "mode": stat.S_IMODE(details.st_mode), "sha256": ""}
                continue
            if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode)):
                raise RegistryError("Конфигурация содержит ссылку или специальный файл; принятие остановлено")
            is_file = stat.S_ISREG(details.st_mode)
            size += details.st_size if is_file else 0
            if size > MAX_SNAPSHOT_BYTES or len(entries) >= MAX_SNAPSHOT_FILES:
                raise RegistryError("Объём конфигурации превышает предел резервной копии (128 МБ / 5000 файлов)")
            digest = ""
            if is_file:
                with path.open("rb") as source:
                    checksum = hashlib.sha256()
                    for chunk in iter(lambda: source.read(65536), b""):
                        checksum.update(chunk)
                    digest = checksum.hexdigest()
            entries[name] = {"path": name, "kind": "file" if is_file else "directory", "size": details.st_size if is_file else 0,
                             "mode": stat.S_IMODE(details.st_mode), "sha256": digest}
    return sorted(entries.values(), key=lambda entry: entry["path"])


def fingerprint(entries: list[dict], context: dict) -> str:
    return hashlib.sha256(json.dumps({"entries": entries, "context": context}, sort_keys=True).encode()).hexdigest()


class ComponentRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.root = data_dir / "components"

    def receipt_path(self, image_id: str) -> Path:
        return self.root / component_id(image_id) / "receipt.json"

    def receipt(self, image_id: str) -> dict | None:
        try:
            value = json.loads(self.receipt_path(image_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RegistryError("Запись управления компонентом недоступна") from exc
        if not isinstance(value, dict) or value.get("schema") != 1 or value.get("id") != image_id or value.get("origin") not in {"installed", "adopted"} or type(value.get("retained", False)) is not bool:
            raise RegistryError("Запись управления компонентом повреждена")
        return value

    def management(self, image_id: str) -> dict:
        try:
            receipt = self.receipt(image_id)
        except RegistryError as exc:
            return {"state": "unknown", "reason": str(exc)}
        if not receipt:
            return {"state": "unmanaged", "reason": "Нужно явное принятие под управление; доступны просмотр и диагностика"}
        return {"state": "managed", "origin": receipt["origin"], "accepted_at": receipt.get("accepted_at"),
                "retained": receipt.get("retained", False), "backup_id": receipt.get("backup_id"), "reason": "Компонент принят под управление"}

    def require_managed(self, image_id: str, *, allow_retained=False) -> None:
        management = self.management(image_id)
        if management["state"] != "managed":
            raise RegistryError("Компонент доступен только для просмотра и диагностики. Сначала примите его под управление")
        if management.get("retained") and not allow_retained:
            raise RegistryError("Исполняемая часть компонента удалена; настройки сохранены для повторной установки")

    @contextmanager
    def locked(self):
        with _lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (self.root / "registry.lock").open("a+b") as lock:
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield

    def write_receipt(self, image_id: str, origin: str, **details) -> dict:
        path = self.receipt_path(image_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {"schema": 1, "id": image_id, "origin": origin, "accepted_at": datetime.now(timezone.utc).isoformat(), **details}
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as target:
                os.chmod(temporary, 0o600)
                json.dump(value, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return value

    def adopt(self, image_id: str, paths: list[Path], context: dict, expected_fingerprint: str) -> dict:
        with self.locked():
            existing = self.receipt(image_id)
            if existing:
                return self.management(image_id)  # Reconcile a lost successful response.
            entries = inventory(paths)
            if not entries or fingerprint(entries, context) != expected_fingerprint:
                raise RegistryError("Конфигурация изменилась; повторите предварительную проверку")
            backup_id = uuid.uuid4().hex
            backup = self.data_dir / "component-backups" / component_id(image_id) / backup_id
            backup.mkdir(parents=True, mode=0o700)
            archive_path = backup / "configuration.tar"
            try:
                if shutil.disk_usage(backup).free < sum(entry["size"] for entry in entries) * 2 + 1024 * 1024:
                    raise RegistryError("Недостаточно места для резервной копии конфигурации")
                with archive_path.open("xb") as raw:
                    os.chmod(archive_path, 0o600)
                    with tarfile.open(fileobj=raw, mode="w") as archive:
                        for index, entry in enumerate(entries):
                            archive.add(entry["path"], arcname=f"files/{index}", recursive=False)
                    raw.flush()
                    os.fsync(raw.fileno())
                if inventory(paths) != entries:
                    raise RegistryError("Конфигурация изменилась во время копирования; принятие остановлено")
                metadata = backup / "manifest.json"
                with metadata.open("x", encoding="utf-8") as target:
                    os.chmod(metadata, 0o600)
                    json.dump({"schema": 1, "id": image_id, "files": entries, "context": context}, target, ensure_ascii=False)
                    target.flush()
                    os.fsync(target.fileno())
                self.write_receipt(image_id, "adopted", backup_id=backup_id, fingerprint=expected_fingerprint)
            except Exception:
                # Only this just-created, fixed subtree can be removed.
                boundary = (self.data_dir / "component-backups").resolve()
                target = backup.resolve()
                if target != boundary and target.is_relative_to(boundary):
                    shutil.rmtree(target)
                raise
            return self.management(image_id)

    def purge(self, image_id: str, paths: list[Path], ensure_stopped) -> None:
        """Explicitly erase retained data after proving runtime absence.

        Callers hold the common operation lock. Shared environment, packages,
        units and other components' client rows are never purge targets.
        """
        with self.locked():
            self.require_managed(image_id, allow_retained=True)
            if not self.management(image_id).get("retained"):
                raise RegistryError("Сначала удалите исполняемую часть компонента")
            ensure_stopped()
            backups = self.data_dir / "component-backups" / component_id(image_id)
            targets = list(dict.fromkeys([*paths, backups]))
            if not paths:
                raise RegistryError("Образ не описывает данные для полной очистки")
            for target in targets:
                if not target.is_absolute() or len(target.parts) < 4 or any(part == ".." for part in target.parts):
                    raise RegistryError("Небезопасный путь очистки в описании компонента")
                if any(parent.is_symlink() for parent in target.parents):
                    raise RegistryError("Родительский каталог данных является ссылкой; очистка остановлена")
            # Inventory validates link boundaries and accessibility before the
            # first removal. A failure keeps ownership/retained state for retry.
            inventory(targets)
            client_file = self.data_dir / "clients.json"
            clients = None
            if image_id != "mihomo" and client_file.exists():
                clients = json.loads(client_file.read_text(encoding="utf-8"))
                if not isinstance(clients, list) or any(not isinstance(row, dict) for row in clients):
                    raise RegistryError("Список подключений повреждён; очистка остановлена")
            ensure_stopped()
            for target in sorted(targets, key=lambda path: not path.is_symlink()):
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                elif target.is_dir():
                    shutil.rmtree(target)
            if clients is not None:
                temporary = client_file.with_name(f".clients.{uuid.uuid4().hex}.tmp")
                try:
                    with temporary.open("x", encoding="utf-8") as output:
                        os.chmod(temporary, 0o600)
                        json.dump([row for row in clients if row.get("protocol") != image_id], output, ensure_ascii=False)
                        output.flush()
                        os.fsync(output.fileno())
                    temporary.replace(client_file)
                finally:
                    temporary.unlink(missing_ok=True)
            self.receipt_path(image_id).unlink()


def main() -> int:
    import argparse
    from component_state import image_files
    from service_state import observe_service
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check-install", "begin-install", "check-managed", "record-install", "record-removal", "purge"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        image_id = component_id(manifest["id"])
        registry = ComponentRegistry(args.data_dir)
        if args.action == "purge":
            import subprocess
            interface = os.getenv(manifest.get("interface_env", ""), {"wg": "wg0", "awg": "awg0"}.get(image_id, ""))
            def ensure_stopped():
                interfaces = [interface] if image_id in {"wg", "awg"} else ["mh-wg0", "mh-awg0"] if image_id == "mihomo" else []
                if any((Path("/sys/class/net") / name).exists() for name in interfaces):
                    raise RegistryError("Сетевой интерфейс компонента ещё существует; данные не очищены")
                units = {manifest.get("service", "").replace("{interface}", interface)}
                patterns = ["vps-control-shadowsocks@*.service"] if image_id == "shadowsocks" else []
                if image_id == "mihomo":
                    patterns = ["vps-control-mihomo-*.service", "vps-control-mihomo-*.target"]
                    units.update({"wg-quick@mh-wg0.service", "awg-quick@mh-awg0.service"})
                if patterns:
                    response = subprocess.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", *patterns], capture_output=True, text=True, timeout=10, check=True)
                    units.update(line.split()[0] for line in response.stdout.splitlines() if line.strip())
                for unit in units:
                    observed = observe_service(unit)
                    if observed["unit_present"] is not False and observed["state"] not in {"inactive", "failed"}:
                        raise RegistryError("Компонент запущен или его состояние неизвестно; данные не очищены")
            registry.purge(image_id, paths_from_manifest(manifest, interface, args.manifest.parents[2], args.data_dir, kind="data_paths"), ensure_stopped)
        elif args.action == "check-managed":
            registry.require_managed(image_id)
        elif args.action in {"record-install", "record-removal"}:
            if args.action == "record-removal":
                interface = os.getenv(manifest.get("interface_env", ""), {"wg": "wg0", "awg": "awg0"}.get(image_id, ""))
                unit = observe_service(manifest.get("service", "").replace("{interface}", interface))
                if unit["unit_present"] is not False and unit["state"] not in {"inactive", "failed"}:
                    raise RegistryError("Остановка компонента не подтверждена; удаление нельзя считать завершённым")
            with registry.locked():
                receipt = registry.receipt(image_id)
                if receipt:
                    registry.write_receipt(image_id, receipt["origin"], **{key: value for key, value in receipt.items() if key not in {"id", "schema", "origin", "retained"}}, retained=args.action == "record-removal")
                else:
                    raise RegistryError("Не найдена запись начала установки")
        else:
            interface = os.getenv(manifest.get("interface_env", ""), {"wg": "wg0", "awg": "awg0"}.get(image_id, ""))
            service = manifest.get("service", "").replace("{interface}", interface)
            files = image_files(manifest, interface, args.manifest.parents[2])
            unit = observe_service(service)
            present = any(value is True for value in files) or unit["active"] or ("@" not in service and unit["unit_present"] is True)
            if present:
                registry.require_managed(image_id, allow_retained=True)
                if registry.management(image_id).get("retained") and unit["active"]:
                    raise RegistryError("Удалённый компонент снова запущен вне панели; установка остановлена")
            elif not files or None in files or unit["unit_present"] is None:
                raise RegistryError("Нельзя подтвердить отсутствие компонента; установка не запущена")
            if args.action == "check-install":
                print("existing" if present or registry.receipt(image_id) else "fresh")
            if args.action == "begin-install":
                with registry.locked():
                    if not registry.receipt(image_id):
                        registry.write_receipt(image_id, "installed", retained=False)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, RegistryError) else "Не удалось проверить запись управления компонентом", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
