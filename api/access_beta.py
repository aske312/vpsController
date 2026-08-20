from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


DirectTransport = Literal["awg", "wg", "vless-reality-xhttp", "shadowsocks"]
DIRECT_TRANSPORTS: tuple[DirectTransport, ...] = ("awg", "wg", "vless-reality-xhttp", "shadowsocks")
SUPPORTED_CAPABILITIES = (*DIRECT_TRANSPORTS, "mihomo")
TRANSPORT_LABELS = {
    "awg": "AmneziaWG",
    "wg": "WireGuard",
    "vless-reality-xhttp": "VLESS Reality",
    "shadowsocks": "Shadowsocks",
    "mihomo": "Mihomo",
}
TRANSPORT_TO_MIHOMO_MODULE = {
    "awg": "transport-awg",
    "wg": "transport-wg",
    "vless-reality-xhttp": "transport-reality",
    "shadowsocks": "transport-shadowsocks",
}
MIHOMO_MODULE_TO_TRANSPORT = {value: key for key, value in TRANSPORT_TO_MIHOMO_MODULE.items()}
PLATFORM_LABELS = {
    "windows": "Windows",
    "ios": "iPhone / iPad",
    "android": "Android",
    "macos": "macOS",
    "linux": "Linux",
    "router": "Router",
    "other": "Другое",
}


class AccessUserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=300)


class RoutingPolicy(BaseModel):
    mode: Literal["rule", "global"] = "rule"
    strategy: Literal["fallback", "url-test", "select"] = "fallback"
    test_url: str = Field(default="https://www.gstatic.com/generate_204", min_length=1, max_length=500)
    interval: int = Field(default=180, ge=30, le=3600)
    rules: str = Field(default="", max_length=12000)


class AccessDeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    platform: Literal["windows", "ios", "android", "macos", "linux", "router", "other"] = "windows"
    transports: list[str] = Field(default_factory=list, max_length=8)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)


class AccessDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    platform: Literal["windows", "ios", "android", "macos", "linux", "router", "other"] | None = None
    transports: list[str] | None = Field(default=None, max_length=8)
    routing: RoutingPolicy | None = None
    enabled: bool | None = None


class AccessUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None


class DirectConnectionCreate(BaseModel):
    protocol: DirectTransport


class DirectConnectionBatchCreate(BaseModel):
    transports: list[DirectTransport] | None = Field(default=None, max_length=8)


class SmartProfileCreate(BaseModel):
    transports: list[DirectTransport] | None = Field(default=None, max_length=8)
    strategy: Literal["fallback", "url-test", "select"] = "fallback"
    routing: RoutingPolicy | None = None


def create_access_beta_router(
    *,
    data_dir: Path,
    protocol_detector: Callable[[], dict[str, dict]],
    auth_dependency: Callable[..., None],
    direct_create: Callable[[str, DirectTransport], dict[str, Any]],
    direct_delete: Callable[[str], dict[str, Any]],
    direct_list: Callable[[], list[dict[str, Any]]],
    mihomo_request: Callable[[str, str, dict[str, Any] | None], Any],
) -> APIRouter:
    """Create the beta user/device orchestration layer.

    This router owns the logical model (user -> device -> direct connections /
    smart Mihomo profile) while reusing the existing protocol implementations
    through narrow adapters supplied by api.main. This keeps the beta contour
    independent from the existing manual pages and makes it removable without
    changing their data model.
    """

    router = APIRouter(prefix="/api/access-beta", tags=["access-beta"])
    state_file = data_dir / "access-beta.json"
    backup_file = data_dir / "access-beta.json.bak"
    mutation_lock = threading.Lock()

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def routing_defaults() -> dict[str, Any]:
        return {
            "mode": "rule",
            "strategy": "fallback",
            "test_url": "https://www.gstatic.com/generate_204",
            "interval": 180,
            "rules": "",
        }

    def routing_dict(value: RoutingPolicy | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(value, RoutingPolicy):
            return value.model_dump()
        if isinstance(value, dict):
            try:
                return RoutingPolicy(**value).model_dump()
            except Exception:
                return routing_defaults()
        return routing_defaults()

    def empty_state() -> dict[str, Any]:
        return {"version": 4, "users": []}

    def normalize_state(payload: dict[str, Any]) -> dict[str, Any]:
        """Migrate the metadata-only v1 beta state in-place in memory."""
        users = payload.setdefault("users", [])
        if not isinstance(users, list):
            raise HTTPException(status_code=500, detail="Beta access-profile storage has invalid format")
        for user in users:
            if not isinstance(user, dict):
                raise HTTPException(status_code=500, detail="Beta access-profile storage has invalid user record")
            devices = user.setdefault("devices", [])
            if not isinstance(devices, list):
                raise HTTPException(status_code=500, detail="Beta access-profile storage has invalid device record")
            for device in devices:
                if not isinstance(device, dict):
                    raise HTTPException(status_code=500, detail="Beta access-profile storage has invalid device record")
                device.setdefault("transports", [])
                device.setdefault("connections", [])
                device.setdefault("smart_profile", None)
                device["routing"] = routing_dict(device.get("routing"))
                platform = str(device.get("platform") or "other")
                device["platform_label"] = PLATFORM_LABELS.get(platform, "Другое")
        payload["version"] = 4
        return payload

    def _read_state_path(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid beta registry root")
        return normalize_state(payload)

    def _save_backup(payload: dict[str, Any]) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = data_dir / f".{backup_file.name}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(rendered, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, backup_file)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _restore_backup() -> dict[str, Any] | None:
        if not backup_file.exists():
            return None
        try:
            payload = _read_state_path(backup_file)
            data_dir.mkdir(parents=True, exist_ok=True)
            rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            tmp = data_dir / f".{state_file.name}.{uuid.uuid4().hex}.restore"
            tmp.write_text(rendered, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, state_file)
            return payload
        except (OSError, ValueError, json.JSONDecodeError, HTTPException):
            return None

    def read_state() -> dict[str, Any]:
        if not state_file.exists():
            restored = _restore_backup()
            return restored if restored is not None else empty_state()
        try:
            payload = _read_state_path(state_file)
            if not backup_file.exists():
                try:
                    _save_backup(payload)
                except OSError:
                    pass
            return payload
        except (OSError, ValueError, json.JSONDecodeError, HTTPException) as exc:
            restored = _restore_backup()
            if restored is not None:
                return restored
            raise HTTPException(status_code=500, detail="Beta access-profile storage is unreadable") from exc

    def write_state(payload: dict[str, Any]) -> None:
        payload["version"] = 4
        data_dir.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = data_dir / f".{state_file.name}.{uuid.uuid4().hex}.tmp"
        backup_tmp = data_dir / f".{backup_file.name}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(rendered, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, state_file)
            backup_tmp.write_text(rendered, encoding="utf-8")
            os.chmod(backup_tmp, 0o600)
            os.replace(backup_tmp, backup_file)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Unable to save beta access profiles") from exc
        finally:
            for candidate in (tmp, backup_tmp):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass

    def manager_call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return mihomo_request(method, path, payload)

    def mihomo_snapshot(mihomo_installed: bool) -> dict[str, Any]:
        base = {
            "manager_available": False,
            "manager_error": "",
            "modules": [],
            "installed_transports": [],
        }
        if not mihomo_installed:
            return base
        try:
            response = manager_call("GET", "/api/mihomo/modules")
            raw_items = response.get("items", []) if isinstance(response, dict) else []
            modules = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                module_id = str(item.get("id") or "")
                transport = MIHOMO_MODULE_TO_TRANSPORT.get(module_id)
                if not transport:
                    continue
                modules.append({
                    "id": module_id,
                    "transport": transport,
                    "name": str(item.get("name") or TRANSPORT_LABELS[transport]),
                    "installed": bool(item.get("installed")),
                    "active": bool(item.get("active")),
                    "version": str(item.get("installed_version") or item.get("version") or ""),
                })
            return {
                "manager_available": True,
                "manager_error": "",
                "modules": modules,
                "installed_transports": [item["transport"] for item in modules if item["installed"]],
            }
        except Exception as exc:  # manager is an optional neighbouring service
            detail = getattr(exc, "detail", None) or str(exc)
            return {**base, "manager_error": str(detail or "Mihomo Manager unavailable")}

    def capability_snapshot() -> dict[str, Any]:
        detected = protocol_detector()
        items = []
        for protocol_id in SUPPORTED_CAPABILITIES:
            image = detected.get(protocol_id, {})
            installed = bool(image.get("installed"))
            items.append({
                "id": protocol_id,
                "name": str(image.get("name") or TRANSPORT_LABELS[protocol_id]),
                "installed": installed,
                "active": bool(image.get("active")),
                "kind": "aggregator" if protocol_id == "mihomo" else "transport",
                "service": str(image.get("service") or ""),
                "version": str(image.get("installed_version") or image.get("version") or ""),
            })
        installed_transports = [item["id"] for item in items if item["kind"] == "transport" and item["installed"]]
        mihomo_installed = any(item["id"] == "mihomo" and item["installed"] for item in items)
        smart = mihomo_snapshot(mihomo_installed)
        return {
            "items": items,
            "installed_transports": installed_transports,
            "mihomo_installed": mihomo_installed,
            "mihomo_candidate_transports": list(installed_transports) if mihomo_installed else [],
            "mihomo": smart,
            "detected_at": now_iso(),
        }

    def manager_profiles(caps: dict[str, Any]) -> list[dict[str, Any]]:
        if not caps.get("mihomo", {}).get("manager_available"):
            return []
        try:
            response = manager_call("GET", "/api/mihomo/profiles")
            items = response.get("items", []) if isinstance(response, dict) else []
            return [item for item in items if isinstance(item, dict) and item.get("id")]
        except Exception:
            return []

    def manager_profile_ids(caps: dict[str, Any]) -> set[str]:
        return {str(item.get("id")) for item in manager_profiles(caps)}

    def normalize_transports(values: list[str]) -> list[str]:
        requested = list(dict.fromkeys(values))
        invalid = [value for value in requested if value not in DIRECT_TRANSPORTS]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Unsupported transport: {invalid[0]}")
        installed = set(capability_snapshot()["installed_transports"])
        missing = [value for value in requested if value not in installed]
        if missing:
            label = TRANSPORT_LABELS.get(missing[0], missing[0])
            raise HTTPException(status_code=409, detail=f"{label} is not installed on this server")
        return requested

    def find_user_device(state: dict[str, Any], user_id: str, device_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        user = next((item for item in state["users"] if item.get("id") == user_id), None)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        device = next((item for item in user.get("devices", []) if item.get("id") == device_id), None)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        device.setdefault("connections", [])
        device.setdefault("smart_profile", None)
        return user, device

    def direct_backend_snapshot() -> dict[str, dict[str, Any]]:
        try:
            return {
                str(item.get("id")): item
                for item in direct_list()
                if isinstance(item, dict) and item.get("id")
            }
        except Exception:
            return {}

    def public_connection(connection: dict[str, Any], backend: dict[str, dict[str, Any]]) -> dict[str, Any]:
        backend_id = str(connection.get("backend_client_id") or "")
        backend_item = backend.get(backend_id)
        protocol = str(connection.get("protocol") or "")
        present = bool(backend_item and backend_item.get("protocol") == protocol)
        return {
            key: value
            for key, value in connection.items()
            if key != "config"
        } | {
            "backend_present": present,
            "status": "ready" if present else "missing",
            "has_export": bool(connection.get("config")),
        }

    def enrich_user(
        user: dict[str, Any],
        caps: dict[str, Any],
        backend: dict[str, dict[str, Any]],
        profile_ids: set[str],
    ) -> dict[str, Any]:
        available = set(caps["installed_transports"])
        smart_installed = set(caps.get("mihomo", {}).get("installed_transports", []))
        result = {**user}
        devices = []
        for device in user.get("devices", []):
            desired = list(device.get("transports", []))
            connections = [public_connection(item, backend) for item in device.get("connections", [])]
            smart = device.get("smart_profile")
            public_smart = None
            if isinstance(smart, dict):
                backend_profile_id = str(smart.get("backend_profile_id") or "")
                public_smart = {
                    **smart,
                    "backend_present": backend_profile_id in profile_ids,
                    "status": "ready" if backend_profile_id in profile_ids else "missing",
                }
            devices.append({
                **device,
                "connections": connections,
                "smart_profile": public_smart,
                "transports": desired,
                "routing": routing_dict(device.get("routing")),
                "available_transports": [value for value in desired if value in available],
                "missing_transports": [value for value in desired if value not in available],
                "mihomo_ready": bool(
                    caps.get("mihomo", {}).get("manager_available")
                    and any(value in smart_installed for value in desired)
                ),
                "mihomo_supported_transports": [value for value in desired if value in smart_installed],
                "mihomo_missing_transports": [value for value in desired if value not in smart_installed],
                "provisioning": "managed",
                "connection_count": len(connections),
                "healthy_connection_count": sum(1 for item in connections if item["status"] == "ready"),
            })
        result["devices"] = devices
        result["device_count"] = len(devices)
        result["connection_count"] = sum(len(device["connections"]) for device in devices)
        return result

    def safe_connection_name(user: dict[str, Any], device: dict[str, Any], protocol: str) -> str:
        label = {
            "awg": "AWG",
            "wg": "WG",
            "vless-reality-xhttp": "VLESS",
            "shadowsocks": "SS",
        }.get(protocol, protocol.upper())
        raw = f"{user.get('name', 'user')}-{device.get('name', 'device')}-{label}"
        value = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
        if len(value) < 2:
            value = f"device-{label}"
        return value[:48]

    def find_connection(device: dict[str, Any], connection_id: str) -> dict[str, Any]:
        connection = next((item for item in device.get("connections", []) if item.get("id") == connection_id), None)
        if not connection:
            raise HTTPException(status_code=404, detail="Connection not found")
        return connection

    def provision_direct_locked(
        state: dict[str, Any],
        user: dict[str, Any],
        device: dict[str, Any],
        protocol: DirectTransport,
    ) -> dict[str, Any]:
        if protocol not in device.get("transports", []):
            raise HTTPException(status_code=409, detail=f"{TRANSPORT_LABELS[protocol]} is not enabled for this device")
        caps = capability_snapshot()
        if protocol not in caps["installed_transports"]:
            raise HTTPException(status_code=409, detail=f"{TRANSPORT_LABELS[protocol]} is not installed on this server")

        for current in device.get("connections", []):
            if current.get("protocol") == protocol:
                raise HTTPException(
                    status_code=409,
                    detail=f"{TRANSPORT_LABELS[protocol]} connection already exists in the device contour; clear a MISSING record before recreating it",
                )

        response = direct_create(safe_connection_name(user, device, protocol), protocol)
        backend_id = str(response.get("id") or "")
        config = str(response.get("config") or "")
        if not backend_id or not config:
            if backend_id:
                try:
                    direct_delete(backend_id)
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail="Protocol adapter returned an incomplete connection")
        connection = {
            "id": f"con_{uuid.uuid4().hex[:12]}",
            "kind": "direct",
            "protocol": protocol,
            "backend_client_id": backend_id,
            "filename": str(response.get("filename") or f"{protocol}.conf"),
            "config": config,
            "created_at": now_iso(),
        }
        device.setdefault("connections", []).append(connection)
        user["updated_at"] = now_iso()
        device["updated_at"] = now_iso()
        try:
            write_state(state)
        except Exception:
            try:
                direct_delete(backend_id)
            except Exception:
                pass
            device["connections"] = [item for item in device.get("connections", []) if item.get("id") != connection["id"]]
            raise
        return connection

    def remove_direct_locked(
        state: dict[str, Any],
        user: dict[str, Any],
        device: dict[str, Any],
        connection: dict[str, Any],
    ) -> None:
        backend_id = str(connection.get("backend_client_id") or "")
        if backend_id:
            try:
                direct_delete(backend_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        device["connections"] = [item for item in device.get("connections", []) if item.get("id") != connection.get("id")]
        user["updated_at"] = now_iso()
        device["updated_at"] = now_iso()
        write_state(state)

    def smart_channels_for(device: dict[str, Any], requested: list[str] | None, caps: dict[str, Any]) -> tuple[list[str], list[str]]:
        transports = list(dict.fromkeys(requested if requested is not None else device.get("transports", [])))
        if not transports:
            raise HTTPException(status_code=422, detail="Choose at least one transport for the Smart profile")
        invalid = [item for item in transports if item not in DIRECT_TRANSPORTS]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Unsupported transport: {invalid[0]}")
        not_enabled = [item for item in transports if item not in device.get("transports", [])]
        if not_enabled:
            raise HTTPException(status_code=409, detail=f"{TRANSPORT_LABELS[not_enabled[0]]} is not enabled for this device")
        if not caps.get("mihomo", {}).get("manager_available"):
            detail = caps.get("mihomo", {}).get("manager_error") or "Mihomo Manager is unavailable"
            raise HTTPException(status_code=409, detail=detail)
        installed = set(caps.get("mihomo", {}).get("installed_transports", []))
        missing = [item for item in transports if item not in installed]
        if missing:
            names = ", ".join(TRANSPORT_LABELS[item] for item in missing)
            raise HTTPException(status_code=409, detail=f"Install Mihomo transport modules first: {names}")
        return transports, [TRANSPORT_TO_MIHOMO_MODULE[item] for item in transports]

    def recovery_snapshot(
        state: dict[str, Any],
        caps: dict[str, Any],
        backend: dict[str, dict[str, Any]],
        profiles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        direct_refs = {
            str(connection.get("backend_client_id") or "")
            for user in state.get("users", [])
            for device in user.get("devices", [])
            for connection in device.get("connections", [])
            if connection.get("backend_client_id")
        }
        smart_refs = {
            str(device.get("smart_profile", {}).get("backend_profile_id") or "")
            for user in state.get("users", [])
            for device in user.get("devices", [])
            if isinstance(device.get("smart_profile"), dict)
        }
        smart_candidates = [
            item for item in profiles
            if str(item.get("id") or "") not in smart_refs and " / " in str(item.get("name") or "")
        ]
        suffix = re.compile(r"-(?:AWG|WG|VLESS|SS)$", re.IGNORECASE)
        direct_candidates = [
            item for backend_id, item in backend.items()
            if backend_id not in direct_refs and suffix.search(str(item.get("name") or ""))
        ]
        return {
            "state_file_exists": state_file.exists(),
            "backup_file_exists": backup_file.exists(),
            "smart_candidates": len(smart_candidates),
            "direct_candidates": len(direct_candidates),
            "available": bool(smart_candidates),
        }

    def recover_registry_locked() -> dict[str, int]:
        state = read_state()
        caps = capability_snapshot()
        backend = direct_backend_snapshot()
        profiles = manager_profiles(caps)
        referenced_direct = {
            str(connection.get("backend_client_id") or "")
            for user in state.get("users", [])
            for device in user.get("devices", [])
            for connection in device.get("connections", [])
            if connection.get("backend_client_id")
        }
        referenced_smart = {
            str(device.get("smart_profile", {}).get("backend_profile_id") or "")
            for user in state.get("users", [])
            for device in user.get("devices", [])
            if isinstance(device.get("smart_profile"), dict)
        }
        recovered_users = recovered_devices = recovered_smart = recovered_direct = 0
        changed = False
        for profile in profiles:
            profile_id = str(profile.get("id") or "")
            profile_name = str(profile.get("name") or "").strip()
            if not profile_id or profile_id in referenced_smart or " / " not in profile_name:
                continue
            user_name, device_name = [part.strip() for part in profile_name.split(" / ", 1)]
            if not user_name or not device_name:
                continue
            user = next((item for item in state["users"] if str(item.get("name") or "").casefold() == user_name.casefold()), None)
            if user is None:
                user = {
                    "id": f"usr_{uuid.uuid4().hex[:12]}", "name": user_name, "note": "", "enabled": True,
                    "created_at": now_iso(), "devices": [], "recovered": True,
                }
                state["users"].append(user)
                recovered_users += 1
            device = next((item for item in user.get("devices", []) if str(item.get("name") or "").casefold() == device_name.casefold()), None)
            channels = [str(item) for item in profile.get("channels", []) if str(item) in MIHOMO_MODULE_TO_TRANSPORT]
            transports = list(dict.fromkeys(MIHOMO_MODULE_TO_TRANSPORT[item] for item in channels))
            routing = routing_dict(profile.get("routing"))
            if device is None:
                device = {
                    "id": f"dev_{uuid.uuid4().hex[:12]}", "name": device_name, "platform": "other",
                    "platform_label": PLATFORM_LABELS["other"], "enabled": True, "transports": list(transports),
                    "routing": routing, "connections": [], "smart_profile": None, "created_at": now_iso(), "recovered": True,
                }
                user.setdefault("devices", []).append(device)
                recovered_devices += 1
            else:
                device["transports"] = list(dict.fromkeys([*device.get("transports", []), *transports]))
                device["routing"] = routing
            if not device.get("smart_profile"):
                device["smart_profile"] = {
                    "id": f"smart_{uuid.uuid4().hex[:12]}", "backend_profile_id": profile_id,
                    "transports": transports, "channels": channels, "strategy": routing["strategy"],
                    "routing": routing, "created_at": str(profile.get("created_at") or now_iso()), "recovered": True,
                }
                referenced_smart.add(profile_id)
                recovered_smart += 1
                changed = True
            for protocol in DIRECT_TRANSPORTS:
                expected = safe_connection_name(user, device, protocol)
                match = next((
                    item for backend_id, item in backend.items()
                    if backend_id not in referenced_direct
                    and str(item.get("protocol") or "") == protocol
                    and str(item.get("name") or "") == expected
                ), None)
                if not match:
                    continue
                backend_id = str(match.get("id") or "")
                if not backend_id:
                    continue
                device.setdefault("connections", []).append({
                    "id": f"con_{uuid.uuid4().hex[:12]}", "kind": "direct", "protocol": protocol,
                    "backend_client_id": backend_id, "filename": f"{expected}-{protocol}.txt", "config": "",
                    "created_at": now_iso(), "recovered": True,
                })
                if protocol not in device["transports"]:
                    device["transports"].append(protocol)
                referenced_direct.add(backend_id)
                recovered_direct += 1
                changed = True
        if changed:
            write_state(state)
        return {
            "users": recovered_users, "devices": recovered_devices,
            "smart_profiles": recovered_smart, "direct_connections": recovered_direct,
        }

    @router.get("/capabilities")
    def capabilities(_: None = Depends(auth_dependency)) -> dict[str, Any]:
        return capability_snapshot()

    @router.post("/recover")
    def recover(_: None = Depends(auth_dependency)) -> dict[str, Any]:
        with mutation_lock:
            result = recover_registry_locked()
        return {"recovered": result}

    @router.get("/users")
    def users(_: None = Depends(auth_dependency)) -> dict[str, Any]:
        caps = capability_snapshot()
        state = read_state()
        backend = direct_backend_snapshot()
        profiles = manager_profiles(caps)
        profile_ids = {str(item.get("id")) for item in profiles if item.get("id")}
        return {
            "beta": True,
            "provisioning": "managed",
            "users": [enrich_user(user, caps, backend, profile_ids) for user in state["users"]],
            "capabilities": caps,
            "recovery": recovery_snapshot(state, caps, backend, profiles),
        }

    @router.post("/users")
    def create_user(payload: AccessUserCreate, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        name = payload.name.strip()
        note = payload.note.strip()
        if not name:
            raise HTTPException(status_code=422, detail="User name is required")
        with mutation_lock:
            state = read_state()
            if any(str(item.get("name", "")).casefold() == name.casefold() for item in state["users"]):
                raise HTTPException(status_code=409, detail="A user with this name already exists")
            created = {
                "id": f"usr_{uuid.uuid4().hex[:12]}",
                "name": name,
                "note": note,
                "enabled": True,
                "created_at": now_iso(),
                "devices": [],
            }
            state["users"].append(created)
            write_state(state)
        caps = capability_snapshot()
        return {"user": enrich_user(created, caps, direct_backend_snapshot(), manager_profile_ids(caps))}

    @router.patch("/users/{user_id}")
    def update_user(user_id: str, payload: AccessUserUpdate, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        with mutation_lock:
            state = read_state()
            user = next((item for item in state["users"] if item.get("id") == user_id), None)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            if payload.name is not None:
                name = payload.name.strip()
                if not name:
                    raise HTTPException(status_code=422, detail="User name is required")
                if any(item is not user and str(item.get("name", "")).casefold() == name.casefold() for item in state["users"]):
                    raise HTTPException(status_code=409, detail="A user with this name already exists")
                user["name"] = name
            if payload.note is not None:
                user["note"] = payload.note.strip()
            if payload.enabled is not None:
                user["enabled"] = payload.enabled
            user["updated_at"] = now_iso()
            write_state(state)
        caps = capability_snapshot()
        return {"user": enrich_user(user, caps, direct_backend_snapshot(), manager_profile_ids(caps))}

    def cleanup_device_resources_locked(
        state: dict[str, Any],
        user: dict[str, Any],
        device: dict[str, Any],
    ) -> list[str]:
        """Delete every real resource owned by a device.

        Successful removals are immediately reflected in the in-memory registry.
        Failed resources stay attached to the device so the UI never loses track
        of a credential that may still be active on the server. The caller owns
        the single write_state() commit for the whole cascade.
        """
        errors: list[str] = []

        smart = device.get("smart_profile")
        if isinstance(smart, dict):
            backend_id = str(smart.get("backend_profile_id") or "")
            try:
                if backend_id:
                    try:
                        manager_call("DELETE", f"/api/mihomo/profiles/{backend_id}")
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
                device["smart_profile"] = None
            except Exception as exc:
                errors.append(f"Mihomo: {getattr(exc, 'detail', None) or exc}")

        remaining: list[dict[str, Any]] = []
        for connection in list(device.get("connections", [])):
            backend_id = str(connection.get("backend_client_id") or "")
            label = TRANSPORT_LABELS.get(str(connection.get("protocol") or ""), "Direct")
            try:
                if backend_id:
                    try:
                        direct_delete(backend_id)
                    except HTTPException as exc:
                        if exc.status_code != 404:
                            raise
            except Exception as exc:
                remaining.append(connection)
                errors.append(f"{label}: {getattr(exc, 'detail', None) or exc}")

        device["connections"] = remaining
        device["updated_at"] = now_iso()
        user["updated_at"] = now_iso()
        return errors

    @router.delete("/users/{user_id}")
    def delete_user(user_id: str, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        """Cascade-delete every device and connection owned by a user."""
        errors: list[str] = []
        removed_devices = 0
        with mutation_lock:
            state = read_state()
            user = next((item for item in state["users"] if item.get("id") == user_id), None)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            remaining_devices: list[dict[str, Any]] = []
            for device in list(user.get("devices", [])):
                device_errors = cleanup_device_resources_locked(state, user, device)
                if device_errors:
                    remaining_devices.append(device)
                    prefix = str(device.get("name") or device.get("id") or "Device")
                    errors.extend(f"{prefix}: {message}" for message in device_errors)
                else:
                    removed_devices += 1

            user["devices"] = remaining_devices
            if errors:
                user["updated_at"] = now_iso()
            else:
                state["users"] = [item for item in state["users"] if item.get("id") != user_id]
            write_state(state)

        if errors:
            raise HTTPException(
                status_code=500,
                detail="Не удалось полностью удалить пользователя: " + "; ".join(errors),
            )
        return {"deleted": user_id, "devices_deleted": removed_devices}

    @router.post("/users/{user_id}/devices")
    def create_device(user_id: str, payload: AccessDeviceCreate, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Device name is required")
        transports = normalize_transports(payload.transports)
        with mutation_lock:
            state = read_state()
            user = next((item for item in state["users"] if item.get("id") == user_id), None)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            devices = user.setdefault("devices", [])
            if any(str(item.get("name", "")).casefold() == name.casefold() for item in devices):
                raise HTTPException(status_code=409, detail="This user already has a device with this name")
            device = {
                "id": f"dev_{uuid.uuid4().hex[:12]}",
                "name": name,
                "platform": payload.platform,
                "platform_label": PLATFORM_LABELS[payload.platform],
                "enabled": True,
                "transports": transports,
                "routing": routing_dict(payload.routing),
                "connections": [],
                "smart_profile": None,
                "created_at": now_iso(),
            }
            devices.append(device)
            user["updated_at"] = now_iso()
            write_state(state)
        caps = capability_snapshot()
        enriched = enrich_user({"devices": [device]}, caps, direct_backend_snapshot(), manager_profile_ids(caps))["devices"][0]
        return {"device": enriched}

    @router.patch("/users/{user_id}/devices/{device_id}")
    def update_device(user_id: str, device_id: str, payload: AccessDeviceUpdate, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        transports = normalize_transports(payload.transports) if payload.transports is not None else None
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            if payload.name is not None:
                name = payload.name.strip()
                if not name:
                    raise HTTPException(status_code=422, detail="Device name is required")
                if any(item is not device and str(item.get("name", "")).casefold() == name.casefold() for item in user.get("devices", [])):
                    raise HTTPException(status_code=409, detail="This user already has a device with this name")
                device["name"] = name
            if payload.platform is not None:
                device["platform"] = payload.platform
                device["platform_label"] = PLATFORM_LABELS[payload.platform]
            if transports is not None:
                in_use = {str(item.get("protocol")) for item in device.get("connections", [])}
                smart = device.get("smart_profile")
                if isinstance(smart, dict):
                    in_use.update(str(item) for item in smart.get("transports", []))
                removed_in_use = sorted(in_use.difference(transports))
                if removed_in_use:
                    names = ", ".join(TRANSPORT_LABELS.get(item, item) for item in removed_in_use)
                    raise HTTPException(status_code=409, detail=f"Remove existing connections before disabling: {names}")
                device["transports"] = transports
            if payload.routing is not None:
                next_routing = routing_dict(payload.routing)
                smart = device.get("smart_profile")
                if isinstance(smart, dict):
                    backend_id = str(smart.get("backend_profile_id") or "")
                    if backend_id:
                        manager_call("PATCH", f"/api/mihomo/profiles/{backend_id}", {"routing": next_routing})
                    smart["routing"] = next_routing
                    smart["strategy"] = next_routing["strategy"]
                device["routing"] = next_routing
            if payload.enabled is not None:
                device["enabled"] = payload.enabled
            device["updated_at"] = now_iso()
            user["updated_at"] = now_iso()
            write_state(state)
        caps = capability_snapshot()
        enriched = enrich_user({"devices": [device]}, caps, direct_backend_snapshot(), manager_profile_ids(caps))["devices"][0]
        return {"device": enriched}

    @router.delete("/users/{user_id}/devices/{device_id}")
    def delete_device(user_id: str, device_id: str, _: None = Depends(auth_dependency)) -> dict[str, Any]:
        """Cascade-delete Direct + Smart resources, then remove the device."""
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            direct_count = len(device.get("connections", []))
            smart_count = 1 if isinstance(device.get("smart_profile"), dict) else 0
            errors = cleanup_device_resources_locked(state, user, device)
            if not errors:
                user["devices"] = [item for item in user.get("devices", []) if item.get("id") != device_id]
                user["updated_at"] = now_iso()
            write_state(state)

        if errors:
            raise HTTPException(
                status_code=500,
                detail="Не удалось полностью удалить устройство: " + "; ".join(errors),
            )
        return {
            "deleted": device_id,
            "connections_deleted": direct_count,
            "smart_deleted": bool(smart_count),
        }

    @router.post("/users/{user_id}/devices/{device_id}/connections")
    def create_direct_connection(
        user_id: str,
        device_id: str,
        payload: DirectConnectionCreate,
        _: None = Depends(auth_dependency),
    ) -> dict[str, Any]:
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            connection = provision_direct_locked(state, user, device, payload.protocol)
        return {"connection": public_connection(connection, direct_backend_snapshot())}

    @router.post("/users/{user_id}/devices/{device_id}/connections/provision-selected")
    def provision_selected_connections(
        user_id: str,
        device_id: str,
        payload: DirectConnectionBatchCreate,
        _: None = Depends(auth_dependency),
    ) -> dict[str, Any]:
        """Provision all selected direct transports, rolling back this batch on failure."""
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            requested = list(dict.fromkeys(payload.transports if payload.transports is not None else device.get("transports", [])))
            if not requested:
                raise HTTPException(status_code=422, detail="Choose at least one device transport")
            invalid = [item for item in requested if item not in DIRECT_TRANSPORTS]
            if invalid:
                raise HTTPException(status_code=422, detail=f"Unsupported transport: {invalid[0]}")
            created: list[dict[str, Any]] = []
            try:
                existing_protocols = {
                    str(item.get("protocol"))
                    for item in device.get("connections", [])
                }
                for protocol in requested:
                    if protocol in existing_protocols:
                        continue
                    created.append(provision_direct_locked(state, user, device, protocol))
            except Exception:
                # Only compensate resources created by this request. Existing
                # device connections remain untouched.
                for connection in reversed(created):
                    backend_id = str(connection.get("backend_client_id") or "")
                    try:
                        if backend_id:
                            direct_delete(backend_id)
                    except Exception:
                        pass
                    device["connections"] = [
                        item for item in device.get("connections", [])
                        if item.get("id") != connection.get("id")
                    ]
                write_state(state)
                raise
        backend = direct_backend_snapshot()
        return {"connections": [public_connection(item, backend) for item in device.get("connections", [])]}

    @router.delete("/users/{user_id}/devices/{device_id}/connections/{connection_id}")
    def delete_direct_connection(
        user_id: str,
        device_id: str,
        connection_id: str,
        _: None = Depends(auth_dependency),
    ) -> dict[str, str]:
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            connection = find_connection(device, connection_id)
            remove_direct_locked(state, user, device, connection)
        return {"deleted": connection_id}

    @router.get("/users/{user_id}/devices/{device_id}/connections/{connection_id}/export")
    def export_direct_connection(
        user_id: str,
        device_id: str,
        connection_id: str,
        _: None = Depends(auth_dependency),
    ) -> dict[str, str]:
        state = read_state()
        _, device = find_user_device(state, user_id, device_id)
        connection = find_connection(device, connection_id)
        config = str(connection.get("config") or "")
        if not config:
            raise HTTPException(status_code=404, detail="Connection export is unavailable")
        return {
            "filename": str(connection.get("filename") or "connection.txt"),
            "config": config,
            "protocol": str(connection.get("protocol") or ""),
        }

    @router.post("/users/{user_id}/devices/{device_id}/smart")
    def create_smart_profile(
        user_id: str,
        device_id: str,
        payload: SmartProfileCreate,
        _: None = Depends(auth_dependency),
    ) -> dict[str, Any]:
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            if device.get("smart_profile"):
                raise HTTPException(status_code=409, detail="Smart profile already exists for this device")
            caps = capability_snapshot()
            transports, channels = smart_channels_for(device, payload.transports, caps)
            profile_name = f"{user.get('name', 'User')} / {device.get('name', 'Device')}"
            routing = routing_dict(payload.routing if payload.routing is not None else device.get("routing"))
            if payload.routing is None and payload.strategy:
                routing["strategy"] = payload.strategy
            created = manager_call("POST", "/api/mihomo/profiles", {
                "name": profile_name[:80],
                "channels": channels,
                "routing": routing,
            })
            backend_id = str(created.get("id") or "") if isinstance(created, dict) else ""
            if not backend_id:
                raise HTTPException(status_code=500, detail="Mihomo Manager returned an incomplete profile")
            smart = {
                "id": f"smart_{uuid.uuid4().hex[:12]}",
                "backend_profile_id": backend_id,
                "transports": transports,
                "channels": channels,
                "strategy": routing["strategy"],
                "routing": routing,
                "created_at": now_iso(),
            }
            device["smart_profile"] = smart
            device["updated_at"] = now_iso()
            user["updated_at"] = now_iso()
            try:
                write_state(state)
            except Exception:
                try:
                    manager_call("DELETE", f"/api/mihomo/profiles/{backend_id}")
                except Exception:
                    pass
                device["smart_profile"] = None
                raise
        return {"smart_profile": {**smart, "backend_present": True, "status": "ready"}}

    @router.delete("/users/{user_id}/devices/{device_id}/smart")
    def delete_smart_profile(
        user_id: str,
        device_id: str,
        _: None = Depends(auth_dependency),
    ) -> dict[str, str]:
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            smart = device.get("smart_profile")
            if not isinstance(smart, dict):
                raise HTTPException(status_code=404, detail="Smart profile not found")
            backend_id = str(smart.get("backend_profile_id") or "")
            if backend_id:
                try:
                    manager_call("DELETE", f"/api/mihomo/profiles/{backend_id}")
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
            device["smart_profile"] = None
            device["updated_at"] = now_iso()
            user["updated_at"] = now_iso()
            write_state(state)
        return {"deleted": str(smart.get("id") or backend_id)}

    @router.get(
        "/users/{user_id}/devices/{device_id}/smart/export",
        response_class=PlainTextResponse,
    )
    def export_smart_profile(
        user_id: str,
        device_id: str,
        _: None = Depends(auth_dependency),
    ) -> str:
        state = read_state()
        _, device = find_user_device(state, user_id, device_id)
        smart = device.get("smart_profile")
        if not isinstance(smart, dict):
            raise HTTPException(status_code=404, detail="Smart profile not found")
        backend_id = str(smart.get("backend_profile_id") or "")
        if not backend_id:
            raise HTTPException(status_code=404, detail="Smart profile backend id is missing")
        value = manager_call("GET", f"/api/mihomo/profiles/{backend_id}/config")
        if not isinstance(value, str):
            raise HTTPException(status_code=500, detail="Mihomo Manager returned an invalid config")
        return value

    @router.post("/users/{user_id}/devices/{device_id}/cleanup")
    def cleanup_device(
        user_id: str,
        device_id: str,
        _: None = Depends(auth_dependency),
    ) -> dict[str, Any]:
        """Remove every resource owned by a device, keeping failed items visible."""
        with mutation_lock:
            state = read_state()
            user, device = find_user_device(state, user_id, device_id)
            errors = cleanup_device_resources_locked(state, user, device)
            write_state(state)

        if errors:
            raise HTTPException(status_code=500, detail="; ".join(errors))
        return {"cleaned": device_id, "connections": 0, "smart": False}

    return router
