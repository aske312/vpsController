"""Durable lower-bound usage from authenticated protocol counters, never estimates.

Past traffic before collection and traffic lost between core resets cannot be
reconstructed. A QUIC observation replay becomes a baseline, not fresh traffic.
"""
import hashlib
import json
import math
import threading
import time


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
    now = int(time.time())
    today = now // 86400
    with LOCK:
        state = read(path, {"devices": {}, "sources": {}})
        before = json.dumps(state, sort_keys=True)
        traffic_evidence = {}
        history = state.setdefault("history", {"schema": 1, "started_at": {}, "days": {}})
        if history.get("schema") != 1 or not isinstance(history.get("started_at"), dict) or not isinstance(history.get("days"), dict):
            raise ValueError("Invalid traffic history")
        # Calendar days have stable UTC boundaries on every installation.
        history["days"] = {day: values for day, values in history["days"].items() if int(day) >= today - 29}
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
            history["started_at"].setdefault(device, now)
            daily = history["days"].setdefault(str(today), {})
            daily.setdefault(device, 0)
            sampled_at = previous.get("sampled_at") if previous else None
            traffic_signature = hashlib.sha256(json.dumps([connection.get("settings", {}), credential], sort_keys=True).encode()).hexdigest()
            directions = [row.get("rx_bytes"), row.get("tx_bytes")]
            directional = all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in directions)
            same_source = bool(previous and previous.get("traffic_signature") == traffic_signature
                               and previous.get("epoch") == epoch and previous.get("counter_epoch") == row.get("counter_epoch")
                               and current >= previous["last"])
            if directional and same_source and any(previous.get(key) is not None and directions[index] < previous[key]
                                                   for index, key in enumerate(("last_rx", "last_tx"))):
                same_source = False
            verified_at = previous.get("traffic_verified_at") if same_source else None
            rx_at = previous.get("rx_at") if same_source else None
            tx_at = previous.get("tx_at") if same_source else None
            fresh = type(sampled_at) is int and 0 <= now - sampled_at <= 120
            if same_source and fresh and row.get("service_active") is not False:
                if directional:
                    for index, direction_key in enumerate(("last_rx", "last_tx")):
                        old = previous.get(direction_key)
                        if old is not None and directions[index] > old:
                            if index == 0: rx_at = now
                            else: tx_at = now
                    if all(type(at) is int and 0 <= now - at <= 120 for at in (rx_at, tx_at)):
                        verified_at = max(verified_at or 0, min(rx_at, tx_at))
                elif connection.get("component") == "transport-shadowsocks" and "used_bytes" in row and current > previous["last"]:
                    # libev's native payload counter, never merely TCP sessions.
                    verified_at = now
            if row.get("service_active") is False:
                verified_at = rx_at = tx_at = None
            if type(verified_at) is int and verified_at <= now and row.get("service_active") is not False:
                traffic_evidence[str(connection["id"])] = {"traffic_verified_at": verified_at, "traffic_observed_at": now}
            # Initial counters can include old traffic. A delta crossing
            # midnight cannot be split honestly; retain it only in all-time
            # accounting. Period values remain measured lower bounds.
            if type(sampled_at) is int and sampled_at <= now and sampled_at // 86400 == today:
                daily[device] += delta
            state["sources"][key] = {"last": current, "epoch": epoch, "counter_epoch": row.get("counter_epoch"), "sampled_at": now,
                                     "traffic_signature": traffic_signature, "traffic_verified_at": verified_at,
                                     "last_rx": int(directions[0]) if directional else None, "last_tx": int(directions[1]) if directional else None,
                                     "rx_at": rx_at, "tx_at": tx_at}
        if json.dumps(state, sort_keys=True) != before:
            atomic(path, state)
        def usage(value, device=None):
            starts = history["started_at"]
            since = starts.get(device) if device is not None else min(starts.values(), default=None)
            periods = {}
            for name, days in (("day", 1), ("week", 7), ("month", 30)):
                first = today - days + 1
                measured = [values[device] if device is not None else sum(values.values())
                            for day, values in history["days"].items() if first <= int(day) <= today
                            and (device in values if device is not None else bool(values))]
                total = sum(measured) if since is not None and measured else None
                periods[name] = {"used_bytes": total, "stats_available": total is not None, "stats_partial": total is not None,
                                 "started_at": first * 86400, "ended_at": now}
            return {"used_bytes": value, "stats_available": value is not None, "stats_partial": value is not None,
                    "history_started_at": since, "periods": periods}
        devices = {key: usage(value, key) for key, value in state["devices"].items()}
        for device in profile.get("devices", []):
            devices.setdefault(str(device["id"]), usage(None, str(device["id"])))
        return {"summary": usage(sum(state["devices"].values()) if state["devices"] else None), "devices": devices, "traffic_evidence": traffic_evidence}
