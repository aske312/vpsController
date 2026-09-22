"""Persistent, validated recovery of Mihomo profile files and runtime."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from application_operation import atomic_json


def marker(api):
    return api.PROFILE_FILE.parent / "profile-recovery" / "manifest.json"


def runtime(api, unit, *, restoring=False):
    observed = api.observe_service(unit)
    if observed.get("unit_present") is False:
        return "absent"
    state = observed["runtime"]["state"]
    if state == "error" and restoring:
        return state
    if state not in {"running", "stopped"}:
        raise RuntimeError("Неизвестно состояние службы Mihomo; восстановление не может быть подтверждено")
    return state


def allowed_files(api, modules):
    return {"profiles": api.PROFILE_FILE, "routing": api.ROUTING_SETTINGS_FILE,
            **{f"external-{module}": path for module, path in api.WG_CONFIG_BY_MODULE.items() if module in modules}}


def capture(path):
    if path.is_symlink():
        raise ValueError("Ссылка вместо файла конфигурации Mihomo")
    if not path.exists():
        return None
    details = path.stat()
    return {"content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "mode": details.st_mode & 0o777, "uid": details.st_uid, "gid": details.st_gid}


def config_files(root):
    if root.is_symlink():
        raise ValueError("Каталог конфигурации заменён ссылкой")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Ссылка в каталоге конфигурации Mihomo")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def prepare(api, modules):
    if marker(api).exists():
        raise RuntimeError("Сначала восстановите прерванное изменение профилей Mihomo")
    if not modules.issubset(api.TRANSPORTS):
        raise ValueError("Unknown recovery modules")
    instances = api.ss_runtime.snapshot(api.run, api.CONFIG_ROOT) if "transport-shadowsocks" in modules else None
    states = {module: runtime(api, service) for module in modules if (service := api.SERVICE_BY_MODULE.get(module))}
    value = {"schema": 1, "modules": sorted(modules), "operation_id": os.getenv("VPS_CONTROL_MIHOMO_OPERATION_ID") or api.get_action_payload().get("id"),
             "config_root": str(api.CONFIG_ROOT.resolve()), "states": states,
             "files": {key: capture(path) for key, path in allowed_files(api, modules).items()},
             "targets": {key: str(path.resolve()) for key, path in allowed_files(api, modules).items()},
             "config": {name: capture(path) for name, path in config_files(api.CONFIG_ROOT).items()},
             "instances": {unit: [active, enabled, base64.b64encode(content).decode("ascii") if content is not None else None]
                           for unit, (active, enabled, content) in instances.items()} if instances is not None else None}
    atomic_json(marker(api), value)


def decode(saved):
    if saved is None:
        return None
    if not isinstance(saved, dict) or type(saved.get("mode")) is not int or not 0 <= saved["mode"] <= 0o777:
        raise ValueError("Invalid recovery permissions")
    if any(type(saved.get(key)) is not int or saved[key] < 0 for key in ("uid", "gid")):
        raise ValueError("Invalid recovery ownership")
    return {**saved, "content": base64.b64decode(saved["content"], validate=True)}


def restore_file(path, saved):
    if path.is_symlink():
        raise ValueError("Recovery target replaced by symlink")
    if saved is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".profile-restore-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(saved["content"])
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(saved["mode"])
        if hasattr(os, "chown"):
            os.chown(temporary, saved["uid"], saved["gid"])
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def recover(api):
    path = marker(api)
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("Recovery manifest replaced by symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("modules"), list):
        raise ValueError("Invalid profile recovery snapshot")
    modules = set(value["modules"])
    allowed = allowed_files(api, modules)
    if not modules.issubset(api.TRANSPORTS) or value.get("config_root") != str(api.CONFIG_ROOT.resolve()) or set(value.get("files", {})) != set(allowed) or value.get("targets") != {key: str(target.resolve()) for key, target in allowed.items()}:
        raise ValueError("Recovery snapshot does not match this installation")
    expected_states = {module for module in modules if api.SERVICE_BY_MODULE.get(module)}
    states = value.get("states", {})
    if set(states) != expected_states or any(state not in {"running", "stopped", "absent"} for state in states.values()):
        raise ValueError("Invalid profile runtime snapshot")
    files = {allowed[key]: decode(saved) for key, saved in value["files"].items()}
    config = {}
    for name, saved in value["config"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name or not relative.parts:
            raise ValueError("Invalid profile recovery path")
        config[api.CONFIG_ROOT / relative] = decode(saved)
    current_config = config_files(api.CONFIG_ROOT)
    instances = None
    if value.get("instances") is not None:
        if "transport-shadowsocks" not in modules or not isinstance(value["instances"], dict):
            raise ValueError("Invalid Shadowsocks snapshot")
        instances = {}
        for unit, saved in value["instances"].items():
            if not api.ss_runtime.UNIT.fullmatch(unit) or not isinstance(saved, list) or len(saved) != 3 or type(saved[0]) is not bool or saved[1] not in {"enabled", "enabled-runtime", "disabled", "masked", "masked-runtime"}:
                raise ValueError("Invalid Shadowsocks instance")
            instances[unit] = (saved[0], saved[1], base64.b64decode(saved[2], validate=True) if saved[2] is not None else None)
    failures = []
    def attempt(callback):
        try:
            callback()
        except Exception as error:
            failures.append(str(error))
    ss_current = {}
    if instances is not None:
        try:
            ss_current = api.ss_runtime.snapshot(api.run, api.CONFIG_ROOT, strict=False)
            api.ss_runtime.remove_created(api.run, instances, ss_current)
        except Exception as error:
            failures.append(str(error))
    before_files = len(failures)
    for target, saved in {**files, **config}.items():
        attempt(lambda target=target, saved=saved: restore_file(target, saved))
    for target in current_config.values():
        if target not in config:
            attempt(lambda target=target: target.unlink(missing_ok=True))
    if len(failures) == before_files:
        for module, state in states.items():
            unit = api.SERVICE_BY_MODULE[module]
            def restore_runtime(module=module, unit=unit, state=state):
                observed = runtime(api, unit, restoring=True)
                if state == "absent":
                    if observed != "absent":
                        raise RuntimeError("Unexpected service appeared during profile recovery")
                elif module == "transport-shadowsocks":
                    if observed != state:
                        api.run("systemctl", "start" if state == "running" else "stop", unit, check=True)
                    api.ss_runtime.restore(api.run, instances, ss_current)
                elif state == "running":
                    api.run("systemctl", "reset-failed", unit)
                    api.run("systemctl", "restart", unit, check=True)
                elif observed != "stopped":
                    api.run("systemctl", "stop", unit, check=True)
                if runtime(api, unit) != state:
                    raise RuntimeError("Profile runtime recovery not confirmed")
            attempt(restore_runtime)
        if states.get("transport-reality") == "running":
            attempt(api.rebuild_vless_cdn_snippet)
            if Path("/usr/local/sbin/vps-control").is_file():
                attempt(lambda: api.run("/usr/local/sbin/vps-control", "vless-cdn-firewall", check=True))
            attempt(lambda: api.run("systemctl", "reload", "caddy.service", check=True))
    if failures:
        raise RuntimeError("; ".join(failures))
    path.unlink()


@contextmanager
def transaction(api, modules):
    prepare(api, modules)
    try:
        yield
    except Exception as original:
        try:
            recover(api)
        except Exception as failure:
            raise RuntimeError(f"{original}; rollback incomplete: {failure}") from original
        raise
    else:
        marker(api).unlink()
