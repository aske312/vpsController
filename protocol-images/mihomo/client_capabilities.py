"""Capabilities of our exporters, shared with the profile editor."""
import json
from pathlib import Path

CAPABILITIES = json.loads(Path(__file__).with_name("client-capabilities.json").read_text(encoding="utf-8"))
CLIENT_EXTENSIONS = json.loads(Path(__file__).with_name("client-extensions.json").read_text(encoding="utf-8"))
FEATURES = {key for caps in CAPABILITIES.values() for key in caps["features"]}
RULES = set(CAPABILITIES["mihomo"]["rules"])


def device_capabilities(format, os=None, client=None):
    caps = {key: list(value) if isinstance(value, list) else value for key, value in CAPABILITIES[format].items()}
    extension = CLIENT_EXTENSIONS.get(client, {})
    if extension.get("format") == format:
        for key in ("components", "transports", "features"):
            caps[key] += extension.get(key, [])
    if os is not None and os not in {"windows", "macos", "linux"}:
        caps["rules"] = [key for key in caps["rules"] if key not in {"direct_games_enabled", "direct_p2p_enabled"}]
    return caps


def compatible_routing(routing, format, os=None, client=None):
    result = dict(routing)
    caps = device_capabilities(format, os, client)
    for key in FEATURES | RULES:
        if key in result and key not in caps["features"] + caps["rules"]:
            result[key] = False
    if "strategy" in result and result.get("strategy", "") not in caps["strategies"]:
        result["strategy"] = next(iter(caps["strategies"]), "")
    result["client_config_format"] = format
    return result


def connection_supported(connection, format, direct_settings=None, client=None):
    caps = device_capabilities(format, client=client)
    if connection.get("component") not in caps["components"]:
        return False
    if connection.get("component") != "transport-reality":
        return True
    credential = connection.get("credential", {})
    settings = connection.get("settings", {})
    mode = settings.get("route_mode") or credential.get("route_mode") or "both"
    variants = ["tls"] if mode == "tls" else ["cdn"] if mode == "cdn" else ["direct", "cdn"] if mode == "both" and (settings.get("cdn_enabled") or credential.get("cdn_enabled")) else ["direct"]
    for variant in variants:
        if variant == "direct":
            kind = settings.get("transport") or credential.get("transport")
            if not credential.get("direct_tag") and direct_settings and not settings.get("transport"):
                kind = direct_settings().get("transport", kind)
            kind = kind or "xhttp"
        else:
            kind = settings.get(f"{variant}_transport") or credential.get(f"{variant}_transport") or ("websocket" if variant == "cdn" else "xhttp")
        if kind not in caps["transports"]:
            return False
    return True
