"""Prepare parallel VLESS listeners without invalidating the previous YAML."""
from copy import deepcopy
import hashlib
import json
import secrets
import uuid

GRACE_SECONDS = 15 * 60
DELIVERY_SETTLE_SECONDS = 30


def transition_delivery_revision(profile, device_id):
    """Bind a subscription response to this device and this transition only."""
    device = next((item for item in profile.get("devices", []) if item.get("id") == device_id), {})
    value = {
        "name": profile.get("name"),
        "routing": device.get("routing", profile.get("routing", {})),
        "connections": [item for item in profile.get("connections", []) if item.get("device_id") == device_id],
        "retiring": [{"id": item.get("id"), "credential": item.get("credential"),
                      "deadline": item.get("transition_deadline", item.get("expires_at"))}
                     for item in profile.get("retiring_connections", []) if item.get("device_id") == device_id],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mark_transition_delivered(profile, device_id, revision, served_at):
    if transition_delivery_revision(profile, device_id) != revision:
        return False
    changed = False
    for entry in profile.get("retiring_connections", []):
        if entry.get("device_id") == device_id and entry.get("yaml_served_revision") != revision:
            entry.setdefault("transition_deadline", entry["expires_at"])
            entry.update(yaml_served_revision=revision, yaml_served_at=served_at,
                         expires_at=min(entry["expires_at"], served_at + DELIVERY_SETTLE_SECONDS))
            changed = True
    return changed


def stage_vless_transition(config, connection, decryption, encryption):
    credential = deepcopy(connection.get("credential", {}))
    identity = credential.get("uuid")
    previous = [entry for entry in config.get("inbounds", [])
                if identity and entry.get("protocol") == "vless"
                and any(client.get("id") == identity for client in entry.get("settings", {}).get("clients", []))]
    if not previous or any(len(entry["settings"]["clients"]) != 1 for entry in previous):
        raise ValueError("Невозможно безопасно обновить шифрование канала: проверьте конфигурацию сервера.")

    route_id = secrets.token_hex(16)
    credential.update(uuid=str(uuid.uuid4()), encryption=encryption, direct_tag=f"mihomo-vless-{route_id}")
    occupied = {int(entry.get("port", 0)) for entry in config.get("inbounds", [])}
    for old in previous:
        inbound = deepcopy(old)
        old_port = int(old.get("port", 0))
        port = next((value for value in range(max(1024, old_port + 1), 65536) if value not in occupied), None)
        if port is None:
            port = next((value for value in range(1024, 65536) if value not in occupied), None)
        if port is None:
            raise ValueError("Нет свободного порта для перехода на новую конфигурацию.")
        occupied.add(port)
        kind = ""
        for candidate in ("cdn", "tls"):
            if connection.get("credential", {}).get(f"{candidate}_enabled") and (
                old_port == int(connection["credential"].get(f"{candidate}_port", 0))
                or str(old.get("tag", "")).startswith(f"mihomo-vless-{candidate}-")
            ):
                kind = candidate
                break
        inbound["port"] = port
        inbound["tag"] = f"mihomo-vless-{kind + '-' if kind else ''}{route_id}"
        inbound["settings"]["clients"][0]["id"] = credential["uuid"]
        inbound["settings"]["decryption"] = decryption
        credential[f"{kind}_port" if kind else "port"] = port
        if kind:
            path = "/" + secrets.token_hex(16)
            credential[f"{kind}_path"] = path
            stream = inbound["streamSettings"]
            network = stream.get("network", "tcp")
            field, path_key = {
                "ws": ("wsSettings", "path"), "websocket": ("wsSettings", "path"),
                "xhttp": ("xhttpSettings", "path"), "httpupgrade": ("httpupgradeSettings", "path"),
                "grpc": ("grpcSettings", "serviceName"),
            }[network]
            stream[field][path_key] = path.lstrip("/") if network == "grpc" else path
        config["inbounds"].append(inbound)
    return credential
