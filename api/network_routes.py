"""Versioned Network bindings, embedded in the atomic legacy settings document.

Runtime configurations and credentials belong to consumers. Retiring a binding
only removes admission for new consumers and preserves the old binding snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

FIELDS = {"cdn": "cdn_domains", "tls_relay": "tls_relay_domains", "udp_relay": "udp_relay_domains"}
NAMESPACE = uuid.UUID("ea4d1e50-5468-4d2e-8f66-e3ccdf1171f9")


def addresses(settings):
    result = set()
    for kind, field in FIELDS.items():
        values = settings.get(field, [])
        if not isinstance(values, list):
            raise ValueError("Invalid route list")
        primary = settings.get(field[:-1], "")
        result.update((kind, str(value).strip().lower()) for value in [primary, *values] if value)
    return result


def migrate(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("Invalid Network settings")
    if "route_registry" in settings:
        registry = copy.deepcopy(settings["route_registry"])
        if not isinstance(registry, dict) or registry.get("schema") != 1 or not isinstance(registry.get("bindings"), list) or not isinstance(registry.get("endpoints"), list):
            raise ValueError("Повреждён реестр маршрутов")
        endpoints = {}
        for item in registry["endpoints"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("address"), str) or not item["address"] or item["id"] in endpoints:
                raise ValueError("Invalid route endpoint")
            endpoints[item["id"]] = item["address"]
        identities, active = set(), set()
        for item in registry["bindings"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid route binding")
            identity, endpoint = item.get("id"), item.get("endpoint_id")
            if not isinstance(identity, str) or not identity or identity in identities or not isinstance(endpoint, str) or endpoint not in endpoints:
                raise ValueError("Invalid route binding identity")
            identities.add(identity)
            if item.get("kind") not in FIELDS or item.get("address") != endpoints[endpoint] or item.get("state") not in {"active", "retired"}:
                raise ValueError("Invalid route binding reference")
            key = (item["kind"], item["address"])
            if item["state"] == "active":
                if key in active:
                    raise ValueError("Duplicate active route binding")
                active.add(key)
            revision, applied = item.get("desired_revision"), item.get("applied_revision")
            if type(revision) is not int or revision < 1 or (applied is not None and (type(applied) is not int or not 1 <= applied <= revision)):
                raise ValueError("Invalid route revision")
            consumers = item.get("consumers")
            if not isinstance(consumers, list) or any(not isinstance(consumer, dict) or not isinstance(consumer.get("id"), str) or not consumer["id"] for consumer in consumers):
                raise ValueError("Invalid route consumers")
        return registry
    registry = {"schema": 1, "endpoints": [], "bindings": []}
    for kind, address in sorted(addresses(settings)):
        add(registry, kind, address, legacy=True)
    return registry


def add(registry: dict, kind: str, address: str, *, legacy=False):
    endpoint_id = uuid.uuid5(NAMESPACE, address).hex
    if not any(item["id"] == endpoint_id for item in registry["endpoints"]):
        registry["endpoints"].append({"id": endpoint_id, "address": address, "display_name": address})
    registry["bindings"].append({
        "id": uuid.uuid5(NAMESPACE, f"legacy:{kind}:{address}").hex if legacy else uuid.uuid4().hex,
        "endpoint_id": endpoint_id, "address": address, "kind": kind,
        "transport": "udp" if kind == "udp_relay" else "tcp",
        "listen_port": None, "origin": None, "relay_host_id": None,
        "desired_revision": 1, "applied_revision": None,
        "state": "active", "legacy": legacy,
        "check": {"state": "unknown", "ready": False, "reason": "Проверка DNS не подтверждает listener, forwarding и обмен данными"},
        "consumers": [],
    })


def synchronize(previous: dict, desired: dict, consumers: list[dict]) -> dict:
    registry = migrate(previous)
    wanted = addresses(desired)
    active = set()
    for binding in registry["bindings"]:
        if binding["state"] != "active":
            continue
        key = (binding["kind"], binding["address"])
        binding["consumers"] = [copy.deepcopy(item) for item in consumers if (item["kind"], item["address"]) == key]
        if key not in wanted:
            binding["state"] = "retired"
            binding["retired_at"] = datetime.now(timezone.utc).isoformat()
            binding["forwarding_retained"] = True
        else:
            active.add(key)
    for kind, address in sorted(wanted - active):
        add(registry, kind, address)
    return registry


def consumer_state(registry: dict, consumer_id: str) -> dict:
    bindings = [binding for binding in registry["bindings"] if binding["state"] == "retired"
                and any(item["id"] == consumer_id for item in binding.get("consumers", []))]
    return {"state": "stale" if bindings else "current", "bindings": [binding["id"] for binding in bindings],
            "addresses": list(dict.fromkeys(binding["address"] for binding in bindings)),
            "reason": "Маршрут удалён из Network; настройки подключения сохранены" if bindings else ""}


def retired(registry: dict, kind: str, address: str) -> bool:
    matches = [item for item in registry["bindings"] if item["kind"] == kind and item["address"] == address.strip().lower()]
    return bool(matches) and not any(item["state"] == "active" for item in matches)


def record_check(registry: dict, kind: str, address: str, result: dict) -> dict:
    """Commit one explicit probe result without counting polling or duplicates."""
    address = address.strip().lower()
    matches = [item for item in registry["bindings"]
               if item.get("kind") == kind and item.get("address") == address]
    if not matches:
        raise ValueError("Unknown route binding")
    binding = next((item for item in matches if item.get("state") == "active"), matches[-1])
    if binding.get("state") != "active":
        binding["check"] = {**binding.get("check", {}), "state": "stale", "ready": False,
                             "reason": "Маршрут удалён; внешнее forwarding сохранено"}
        return registry
    check = dict(binding.get("check") or {})
    revision = binding.get("desired_revision", 1)
    payload = {key: result.get(key) for key in ("status", "ready", "route", "resolved", "matches_origin", "message")}
    result_key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    same_revision = check.get("checked_revision") == revision
    duplicate = same_revision and check.get("result_key") == result_key
    ready = bool(result.get("ready"))
    if not duplicate:
        if ready:
            failed_checks = 0
            state = "ready"
        else:
            failed_checks = int(check.get("failed_checks", 0)) + 1 if same_revision else 1
            state = "error" if failed_checks >= 10 else "warning"
        check.update({"state": state, "ready": ready, "failed_checks": failed_checks,
                      "checked_revision": revision, "result_key": result_key,
                      "reason": str(result.get("message") or "Проверка маршрута не подтверждена"),
                      "last_error": None if ready else str(result.get("message") or "Проверка не пройдена")})
    binding["check"] = check
    return registry
