"""Durable lower-bound usage from authenticated protocol counters, never estimates.

Past traffic before collection and traffic lost between core resets cannot be
reconstructed. A QUIC observation replay becomes a baseline, not fresh traffic.
"""
import hashlib
import json
import math
import threading


LOCK = threading.Lock()


def measured_bytes(row):
    if not row or row.get("stats_available") is False:
        return None
    values = [row["used_bytes"]] if "used_bytes" in row else [row.get("rx_bytes"), row.get("tx_bytes")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in values):
        return None
    return sum(int(value) for value in values)


def snapshot(profile, rows, root, read, atomic):
    path = root / (hashlib.sha256(str(profile["id"]).encode()).hexdigest() + ".json")
    with LOCK:
        state = read(path, {"devices": {}, "sources": {}})
        before = json.dumps(state, sort_keys=True)
        for connection in profile.get("connections", []):
            row = rows.get(str(connection["id"]))
            current = measured_bytes(row)
            if current is None:
                continue
            credential = connection.get("credential", {})
            principal = next((credential[key] for key in ("instance_id", "public_key", "uuid", "password") if credential.get(key)), "")
            identity = json.dumps([connection["id"], connection.get("component"), principal], sort_keys=True)
            key = hashlib.sha256(identity.encode()).hexdigest()
            previous = state["sources"].get(key)
            epoch = row.get("observed_since")
            if previous is None:
                delta = current
            elif row.get("traffic_scope") == "observation" and epoch != previous.get("epoch"):
                # A reconnect can replay old closed flows. Do not count them twice.
                delta = 0
            elif row.get("counter_epoch") != previous.get("counter_epoch"):
                delta = current
            else:
                delta = current - previous["last"] if current >= previous["last"] else current
            device = str(connection.get("device_id", "device-1"))
            state["devices"][device] = state["devices"].get(device, 0) + delta
            state["sources"][key] = {"last": current, "epoch": epoch, "counter_epoch": row.get("counter_epoch")}
        if json.dumps(state, sort_keys=True) != before:
            atomic(path, state)
        def usage(value):
            return {"used_bytes": value, "stats_available": value is not None, "stats_partial": value is not None}
        devices = {key: usage(value) for key, value in state["devices"].items()}
        for device in profile.get("devices", []):
            devices.setdefault(str(device["id"]), usage(None))
        return {"summary": usage(sum(state["devices"].values()) if state["devices"] else None), "devices": devices}
