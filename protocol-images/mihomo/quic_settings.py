"""Edit managed QUIC settings while preserving listeners and runtime state."""
from copy import deepcopy
import json
import os


def listener(config, module):
    if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list) or any(not isinstance(row, dict) for row in config["inbounds"]):
        raise RuntimeError("Управляемая конфигурация QUIC повреждена")
    matches = [row for row in config["inbounds"] if row.get("type") == module]
    if len(matches) != 1 or matches[0].get("tag") != f"{module}-in":
        raise RuntimeError("Не найден однозначный управляемый QUIC listener")
    return matches[0]


def render(config, module, values, secret=None):
    candidate = deepcopy(config)
    inbound = listener(candidate, module)
    if any(row is not inbound and row.get("listen_port") == int(values["port"]) for row in candidate["inbounds"]):
        raise RuntimeError("UDP-порт QUIC занят другим listener конфигурации")
    inbound["listen_port"] = int(values["port"])
    if module == "hysteria2":
        inbound.update(up_mbps=int(values["up_mbps"]), down_mbps=int(values["down_mbps"]))
        if values["obfs"]:
            existing = inbound.get("obfs", {})
            if not isinstance(existing, dict) or existing.get("type") != "salamander" or not existing.get("password"):
                inbound["obfs"] = {"type": "salamander", "password": secret}
        else:
            inbound.pop("obfs", None)
    else:
        inbound.update(congestion_control=values["congestion_control"], heartbeat=values["heartbeat"])
    return candidate


def prepare(api, module_id, previous_values, values):
    path = api.quic_root(module_id) / "config.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Управляемая конфигурация QUIC отсутствует или заменена ссылкой")
    original = path.read_bytes()
    try:
        config = json.loads(original)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Управляемая конфигурация QUIC повреждена") from exc
    module = api.quic_module_name(module_id)
    inbound = listener(config, module)
    if inbound.get("listen_port") != previous_values["port"]:
        raise RuntimeError("UDP-порт конфигурации не совпадает с настройками; сначала восстановите компонент")
    if not isinstance(inbound.get("users"), list) or any(not isinstance(row, dict) for row in inbound["users"]):
        raise RuntimeError("Не удалось подтвердить пользователей QUIC listener")
    if module == "hysteria2" and bool(inbound.get("obfs")) != bool(previous_values["obfs"]):
        raise RuntimeError("Обфускация конфигурации не совпадает с настройками; сначала восстановите компонент")
    if module == "hysteria2" and previous_values["obfs"]:
        obfs = inbound["obfs"]
        if not isinstance(obfs, dict) or obfs.get("type") != "salamander" or not isinstance(obfs.get("password"), str) or not obfs["password"]:
            raise RuntimeError("Не удалось подтвердить существующий ключ обфускации QUIC")
    breaking = {"port", "obfs"} if module == "hysteria2" else {"port"}
    if inbound["users"] and any(values[key] != previous_values[key] for key in breaking):
        raise RuntimeError("Listener содержит существующих пользователей. Изменение порта или обфускации требует перехода на новые подключения")
    state = api.observe_service(api.SERVICE_BY_MODULE[module_id])["runtime"]["state"]
    if state not in {"running", "stopped"}:
        raise RuntimeError("Состояние QUIC неизвестно; применение настроек приостановлено")
    candidate = render(config, module, values)
    return original, candidate if candidate != config else None, state


def apply(api, module_id, prepared):
    original, candidate, state = prepared
    path = api.quic_root(module_id) / "config.json"
    unit = api.SERVICE_BY_MODULE[module_id]
    if path.is_symlink() or path.read_bytes() != original or api.observe_service(unit)["runtime"]["state"] != state:
        raise RuntimeError("Конфигурация или состояние QUIC изменились перед применением настроек")
    if candidate is None:
        return
    inbound = listener(candidate, api.quic_module_name(module_id))
    if module_id == "transport-hysteria2" and "obfs" in inbound and not inbound["obfs"].get("password"):
        inbound["obfs"]["password"] = api.quic_obfs_secret()
    temporary = path.with_name("config.candidate.json")
    api.atomic_json(temporary, candidate)
    try:
        result = api.run(str(api.SINGBOX_BIN), "check", "-c", str(temporary))
        if result.returncode:
            # Core stderr may contain credentials; keep the public error generic.
            raise RuntimeError("sing-box отклонил конфигурацию QUIC")
        if path.is_symlink() or path.read_bytes() != original or api.observe_service(unit)["runtime"]["state"] != state:
            raise RuntimeError("Конфигурация или состояние QUIC изменились при проверке настроек")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if state == "running":
        api.run("systemctl", "reset-failed", unit)
        api.run("systemctl", "restart", unit, check=True)
        if not api.service_stably_active(unit):
            raise RuntimeError("Служба QUIC не осталась активной после изменения настроек")
        api.start_quic_telemetry(module_id, candidate)
    elif api.observe_service(unit)["runtime"]["state"] != "stopped":
        raise RuntimeError("Остановленное состояние QUIC изменилось при применении настроек")
