"""Apply managed VLESS settings without installing or updating Xray."""
from copy import deepcopy
import json


def render(config, values):
    if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list) or any(not isinstance(row, dict) for row in config["inbounds"]):
        raise RuntimeError("Управляемая конфигурация VLESS повреждена")
    candidate = deepcopy(config)
    api_inbounds = [row for row in candidate["inbounds"] if row.get("tag") == "api"]
    if (len(api_inbounds) != 1 or api_inbounds[0].get("listen") != "127.0.0.1"
            or api_inbounds[0].get("protocol") != "dokodemo-door"):
        raise RuntimeError("Не найден однозначный локальный API VLESS")
    port = int(values["api_port"])
    if any(row.get("port") == port for row in candidate["inbounds"] if row.get("tag") != "api"):
        raise RuntimeError("Служебный порт VLESS занят существующим подключением")
    api_inbounds[0]["port"] = port
    for key in ("log", "dns"):
        if key in candidate and not isinstance(candidate[key], dict):
            raise RuntimeError(f"Некорректная секция {key} VLESS")
    candidate.setdefault("log", {})["loglevel"] = values["loglevel"]
    candidate.setdefault("dns", {})["servers"] = [server.strip() for server in str(values["dns"]).split(",") if server.strip()]
    return candidate


def prepare(api, values):
    path = api.CONFIG_ROOT / "reality" / "config.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Управляемая конфигурация VLESS отсутствует или заменена ссылкой")
    original = path.read_bytes()
    try:
        config = json.loads(original)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Управляемая конфигурация VLESS повреждена") from exc
    unit = api.SERVICE_BY_MODULE["transport-reality"]
    state = api.observe_service(unit)["runtime"]["state"]
    if state not in {"running", "stopped"}:
        raise RuntimeError("Состояние VLESS неизвестно; применение настроек приостановлено")
    candidate = render(config, values)
    return original, candidate if candidate != config else None, state


def apply(api, prepared):
    original, candidate, state = prepared
    path = api.CONFIG_ROOT / "reality" / "config.json"
    unit = api.SERVICE_BY_MODULE["transport-reality"]
    if path.is_symlink() or path.read_bytes() != original or api.observe_service(unit)["runtime"]["state"] != state:
        raise RuntimeError("Конфигурация или состояние VLESS изменились перед применением настроек")
    if candidate is None:
        return
    api.apply_reality_config(path, candidate, restart_service=False)
    if state == "running":
        api.run("systemctl", "reset-failed", unit)
        api.run("systemctl", "restart", unit, check=True)
        if not api.service_stably_active(unit):
            raise RuntimeError("Служба VLESS не осталась активной после изменения настроек")
    elif api.observe_service(unit)["runtime"]["state"] != "stopped":
        raise RuntimeError("Остановленное состояние VLESS изменилось при применении настроек")
