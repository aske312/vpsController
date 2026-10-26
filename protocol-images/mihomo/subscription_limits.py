"""Shared, server-side subscription accounting and enforcement.

Authenticated proxy outbounds carry a profile mark. WireGuard peers are
identified after decryption. A profile's devices and protocols share the same
kernel quota and two rate buckets; client configuration is not trusted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from pydantic import BaseModel, ConfigDict, Field, StrictInt

TABLE = "vps_mihomo_limits"
ROOT = Path("/var/lib/vps-control/mihomo/subscription-limits")
PREFIX = "subscription-"
UNLIMIT = {"speed_mbps": None, "traffic_gb": None, "days": None}
_GROUP_CACHE = {}


class SubscriptionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed_mbps: StrictInt | None = Field(default=None, ge=1, le=100000)
    traffic_gb: StrictInt | None = Field(default=None, ge=1, le=1000000)
    days: StrictInt | None = Field(default=None, ge=1, le=36500)


def normalized(value):
    return SubscriptionLimits.model_validate(value or {}).model_dump()


def limited(value):
    return any(number is not None for number in normalized(value).values())


def set_limits(profile, values, now=None):
    values = normalized(values)
    previous = normalized(profile.get("limits"))
    profile["limits"] = values
    profile["limits_controlled"] = True
    # Editing the profile or extending its day allowance must not renew it.
    if values["days"] is not None and (previous["days"] is None or not profile.get("limits_started_at")):
        profile["limits_started_at"] = int(time.time() if now is None else now)
    if values["days"] is None:
        profile["limits_started_at"] = None


def profile_mark(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
        raise ValueError("Invalid subscription identity")
    # Xray's SO_MARK field is signed int32; keep our namespace below 2**31.
    return 0x31000000 | int(hashlib.sha256(identity.encode()).hexdigest()[:6], 16)


def plan(profiles):
    rows, marks = [], set()
    for profile in profiles:
        if not profile.get("limits_controlled"):
            continue
        identity = profile["id"]
        mark = profile_mark(identity)
        if mark in marks:
            raise ValueError("Subscription mark collision")
        marks.add(mark)
        limits = normalized(profile.get("limits"))
        started = profile.get("limits_started_at")
        if limits["days"] is not None and (type(started) is not int or started <= 0):
            raise ValueError("Subscription start time is missing")
        row = {"id": identity, "mark": mark, "limits": limits,
               "expires_at": started + limits["days"] * 86400 if limits["days"] is not None else None,
               "xray_users": [], "quic_users": {}, "shadowsocks": [], "peers": []}
        connections = [*profile.get("connections", []), *profile.get("retiring_connections", [])]
        for connection in connections:
            component = connection.get("component")
            credential = connection.get("credential", {})
            instance = str(credential.get("instance_id") or f"{identity}-{connection['id']}")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", instance):
                raise ValueError("Invalid managed connection identity")
            if component == "transport-reality":
                row["xray_users"].append(str(credential.get("email") or f"mihomo-{identity}-{connection['id']}"))
            elif component in {"transport-hysteria2", "transport-tuic"}:
                row["quic_users"].setdefault(component.removeprefix("transport-"), []).append(instance)
            elif component == "transport-shadowsocks":
                port = credential.get("port")
                if type(port) is not int or not 1024 <= port <= 65535:
                    raise ValueError("Invalid Shadowsocks accounting port")
                row["shadowsocks"].append({"unit": f"vps-control-mihomo-ss@{instance}.service", "port": port})
            elif component in {"transport-wg", "transport-awg"}:
                address = ipaddress.ip_interface(credential["ip"])
                row["peers"].append({"interface": "mh-wg0" if component == "transport-wg" else "mh-awg0", "ip": str(address.ip)})
            else:
                raise ValueError("Subscription limits do not support this transport")
        for key in ("peers", "shadowsocks"):
            row[key] = list({json.dumps(value, sort_keys=True): value for value in row[key]}.values())
        rows.append(row)
    return {"schema": 1, "profiles": sorted(rows, key=lambda row: row["id"])}


def decorate_xray(config, policy):
    result = deepcopy(config)
    outbounds = [row for row in result.get("outbounds", []) if not str(row.get("tag", "")).startswith(PREFIX)]
    routing = result.setdefault("routing", {})
    rules = [row for row in routing.get("rules", []) if not str(row.get("outboundTag", "")).startswith(PREFIX)]
    controlled = [profile for profile in policy["profiles"] if profile["xray_users"]]
    direct = next((row for row in outbounds if row.get("tag") == "direct" and row.get("protocol") == "freedom"), {"protocol": "freedom", "settings": {}})
    originals = {row.get("tag"): row for row in outbounds}
    cloned = {}
    def marked(profile, outbound):
        key = (profile["id"], outbound.get("tag"))
        if key not in cloned:
            tag = PREFIX + profile["id"] + ("" if outbound is direct else f"-route{len(cloned)}")
            copy = deepcopy(outbound)
            copy["tag"] = tag
            copy.setdefault("streamSettings", {}).setdefault("sockopt", {})["mark"] = profile["mark"]
            outbounds.append(copy)
            cloned[key] = tag
        return cloned[key]
    next_rules = []
    for rule in rules:
        outbound = originals.get(rule.get("outboundTag"), {})
        if outbound.get("protocol") == "freedom":
            # Preserve redirects (notably the VPN-only admin panel) while
            # accounting their traffic. Existing reject/API rules stay intact.
            for profile in controlled:
                users = sorted(set(profile["xray_users"]))
                if rule.get("user"):
                    users = [user for user in users if user in rule["user"]]
                if users:
                    next_rules.append({**deepcopy(rule), "user": users, "outboundTag": marked(profile, outbound)})
        next_rules.append(rule)
    for profile in controlled:
        next_rules.append({"type": "field", "user": sorted(set(profile["xray_users"])), "outboundTag": marked(profile, direct)})
    result["outbounds"] = outbounds
    routing["rules"] = next_rules
    return result


def decorate_quic(config, policy, module):
    result = deepcopy(config)
    outbounds = [row for row in result.get("outbounds", []) if not str(row.get("tag", "")).startswith(PREFIX)]
    route = result.setdefault("route", {})
    rules = [row for row in route.get("rules", []) if not str(row.get("outbound", "")).startswith(PREFIX)]
    additions = []
    for profile in policy["profiles"]:
        users = profile["quic_users"].get(module, [])
        if not users:
            continue
        tag = PREFIX + profile["id"]
        direct = next((row for row in outbounds if row.get("type") == "direct" and not str(row.get("tag", "")).startswith(PREFIX)), {"type": "direct"})
        outbounds.append({**deepcopy(direct), "type": "direct", "tag": tag, "routing_mark": profile["mark"]})
        additions.append({"auth_user": sorted(set(users)), "action": "route", "outbound": tag})
    result["outbounds"] = outbounds
    route["rules"] = additions + rules
    if module == "hysteria2" and additions:
        # The shared listener's old 100 Mbps cap must not override this policy.
        for inbound in result.get("inbounds", []):
            if inbound.get("tag") == "hysteria2-in":
                inbound.pop("up_mbps", None)
                inbound.pop("down_mbps", None)
                inbound["ignore_client_bandwidth"] = True
    return result


def client_connections(profile, connections):
    if not profile.get("limits_controlled"):
        return connections
    result = deepcopy(connections)
    for connection in result:
        if connection.get("component") == "transport-hysteria2":
            # Adaptive congestion control; enforcement is independent of clients.
            connection.setdefault("credential", {}).update(up_mbps=0, down_mbps=0)
            connection.setdefault("settings", {}).update(up_mbps=0, down_mbps=0)
    return result


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".subscription-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.chmod(name, 0o600)
            json.dump(value, target, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return deepcopy(default)


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def command(*args, input_text=None):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Subscription enforcement command failed: {args[0]} ({result.returncode})")
    return result.stdout


def cgroups(policy, previous=None, *, fresh=False):
    result = {}
    for profile in policy["profiles"]:
        for instance in profile["shadowsocks"]:
            unit = instance["unit"]
            cached = (previous or {}).get(unit, {})
            group = cached.get("path", "")
            if fresh or not group or not (Path("/sys/fs/cgroup") / group.lstrip("/")).is_dir():
                timestamp, group = _GROUP_CACHE.get(unit, (0, ""))
                if fresh or time.monotonic() - timestamp > 15:
                    group = command("systemctl", "show", unit, "--property=ControlGroup", "--value").strip()
                    _GROUP_CACHE[unit] = (time.monotonic(), group)
            path = Path("/sys/fs/cgroup") / group.lstrip("/")
            if group.startswith("/") and path.is_dir():
                result[unit] = {"level": len(Path(group).parts) - 1, "id": path.stat().st_ino, "path": group}
    return result


def nft_rules(policy, state, groups, *, existing=False):
    """One owned table only. Never flush the machine's firewall ruleset."""
    lines = [f"delete table inet {TABLE}"] if existing else []
    lines.append(f"table inet {TABLE} {{")
    output, incoming, forward = [], [], []
    for profile in policy["profiles"]:
        name = "p" + str(profile["mark"])
        limits = profile["limits"]
        saved = state.get(profile["id"], {})
        rx, tx = max(0, int(saved.get("rx_bytes", 0))), max(0, int(saved.get("tx_bytes", 0)))
        lines.extend([f"counter {name}_rx {{ packets 0 bytes {rx}; }}", f"counter {name}_tx {{ packets 0 bytes {tx}; }}"])
        if limits["traffic_gb"] is not None:
            quota = int(Decimal(str(limits["traffic_gb"])) * 1000000000)
            consumed = max(rx + tx, int(saved.get("quota_used_bytes", 0)) if saved.get("quota_limit_bytes") == quota else 0)
            lines.append(f"quota {name}_quota {{ over {quota} bytes used {consumed} bytes; }}")
        for direction, counter in (("up", "tx"), ("down", "rx")):
            if limits["speed_mbps"] is not None:
                rate = int(limits["speed_mbps"] * 125000)
                lines.append(f"limit {name}_{direction}_rate {{ rate over {rate} bytes/second burst {max(65536, rate // 10)} bytes; }}")
            lines.append(f"chain {name}_{direction} {{")
            if profile["expires_at"] is not None:
                lines.append(f"meta time >= {int(profile['expires_at'])} drop")
            if limits["speed_mbps"] is not None:
                lines.append(f'limit name "{name}_{direction}_rate" drop')
            if limits["traffic_gb"] is not None:
                lines.append(f'quota name "{name}_quota" drop')
            lines.extend([f'counter name "{name}_{counter}"', "return", "}"])
        mark = profile["mark"]
        # Only authenticated upstream sockets are marked by the proxy cores.
        output.extend([f"meta mark {mark} ct mark set {mark}", f"ct mark {mark} ct direction original jump {name}_up"])
        incoming.append(f"ct mark {mark} ct direction reply jump {name}_down")
        for instance in profile["shadowsocks"]:
            group = groups.get(instance["unit"])
            if group:
                for transport in ("tcp", "udp"):
                    # nft's string parser preserves backslashes. JSON escaping
                    # would turn systemd's literal \x2d into a different path.
                    path = group["path"].lstrip("/")
                    if any(character in path for character in ('"', '\n', '\r', '\x00')):
                        raise ValueError("Invalid subscription cgroup path")
                    group_path = '"' + path + '"'
                    output.insert(0, f"socket cgroupv2 level {group['level']} {group_path} {transport} sport != {instance['port']} ct mark set {mark}")
        for peer in profile["peers"]:
            family = "ip6" if ":" in peer["ip"] else "ip"
            interface = json.dumps(peer["interface"])
            forward.extend([f"iifname {interface} {family} saddr {peer['ip']} jump {name}_up",
                            f"oifname {interface} {family} daddr {peer['ip']} jump {name}_down"])
            incoming.append(f"iifname {interface} {family} saddr {peer['ip']} jump {name}_up")
            output.append(f"oifname {interface} {family} daddr {peer['ip']} jump {name}_down")
    for name, hook, rules in (("profile_output", "output", output), ("profile_input", "input", incoming), ("profile_forward", "forward", forward)):
        lines.extend([f"chain {name} {{ type filter hook {hook} priority 10; policy accept;", *rules, "}"])
    return "\n".join([*lines, "}", ""])


def nft_parts(policy, state, groups):
    """Parse only our own generated declarations, not arbitrary nft syntax."""
    objects, chains, current = {}, {}, None
    for line in nft_rules(policy, state, groups).splitlines()[1:-1]:
        if current is None and line.startswith(("counter ", "quota ", "limit ")):
            kind, name, body = line.split(" ", 2)
            objects[(kind, name)] = body
        elif line.startswith("chain "):
            current = line.split()[1]
            chains[current] = {"header": line.split("{", 1)[1].strip(), "rules": []}
        elif line == "}":
            current = None
        elif current is not None:
            chains[current]["rules"].append(line)
    return objects, chains


def nft_update_rules(previous, policy, state, old_groups, groups, *, force=False):
    old_objects, old_chains = nft_parts(previous, state, old_groups)
    objects, chains = nft_parts(policy, state, groups)
    if not force and objects == old_objects and chains == old_chains:
        return ""
    # Device registration and cgroup restarts must not reset a shared quota,
    # token bucket or counter. Keep unchanged stateful objects in the kernel.
    lines = [f"flush chain inet {TABLE} {name}" for name in old_chains]
    lines += [f"delete chain inet {TABLE} {name}" for name in old_chains if name not in chains]
    changed = {key for key, body in objects.items() if key not in old_objects or body != old_objects[key]}
    for key in old_objects:
        if key not in objects or key in changed:
            kind, name = key
            lines.append(f"delete {kind} inet {TABLE} {name}")
    for (kind, name), body in objects.items():
        if (kind, name) in changed:
            lines.append(f"add {kind} inet {TABLE} {name} {body}")
    for name, chain in chains.items():
        if name not in old_chains:
            suffix = f" {{ {chain['header']} }}" if chain["header"] else ""
            lines.append(f"add chain inet {TABLE} {name}{suffix}")
        lines.extend(f"add rule inet {TABLE} {name} {rule}" for rule in chain["rules"])
    return "\n".join([*lines, ""])


def kernel_state(policy):
    result = subprocess.run(["nft", "-j", "list", "table", "inet", TABLE], capture_output=True, text=True, timeout=15)
    if result.returncode:
        # The caller distinguishes an absent table from an unreadable ruleset.
        tables = json.loads(command("nft", "-j", "list", "tables"))
        if any(row.get("table", {}).get("name") == TABLE and row.get("table", {}).get("family") == "inet" for row in tables.get("nftables", [])):
            raise RuntimeError("Cannot read subscription counters")
        return {}, False
    payload = json.loads(result.stdout)
    counters = {row["counter"]["name"]: row["counter"]["bytes"] for row in payload.get("nftables", []) if "counter" in row}
    quotas = {row["quota"]["name"]: row["quota"] for row in payload.get("nftables", []) if "quota" in row}
    values = {}
    for profile in policy["profiles"]:
        row = {"rx_bytes": int(counters.get(f"p{profile['mark']}_rx", 0)), "tx_bytes": int(counters.get(f"p{profile['mark']}_tx", 0))}
        quota = quotas.get(f"p{profile['mark']}_quota")
        if quota:
            row.update(quota_limit_bytes=int(quota["bytes"]), quota_used_bytes=min(int(quota["used"]), int(quota["bytes"])))
        values[profile["id"]] = row
    return values, True


def merge_usage(saved, current):
    result = deepcopy(saved)
    for identity, values in current.items():
        previous = result.setdefault(identity, {})
        for field in ("rx_bytes", "tx_bytes"):
            previous[field] = max(int(previous.get(field, 0)), int(values.get(field, 0)))
        if "quota_limit_bytes" in values:
            consumed = previous.get("quota_used_bytes", 0) if previous.get("quota_limit_bytes") == values["quota_limit_bytes"] else 0
            previous["quota_limit_bytes"] = values["quota_limit_bytes"]
            previous["quota_used_bytes"] = max(int(consumed), int(values["quota_used_bytes"]))
    return result


def update(policy, root=ROOT, *, force=False, fresh_groups=False):
    with locked(root):
        previous = read(root / "applied.json", read(root / "policy.json", {"schema": 1, "profiles": []}))
        saved = read(root / "usage.json", {})
        current, exists = kernel_state(previous)
        state = merge_usage(saved, current)
        group_state = read(root / "cgroups.json", {})
        groups = cgroups(policy, group_state, fresh=fresh_groups)
        changed = force or not exists or policy != previous or groups != group_state
        if changed:
            rules = nft_update_rules(previous, policy, state, group_state, groups, force=force) if exists else nft_rules(policy, state, groups)
            if rules:
                command("nft", "-c", "-f", "-", input_text=rules)
            # Persist accounting before any replacement of kernel objects.
            atomic(root / "usage.json", state)
            if rules:
                command("nft", "-f", "-", input_text=rules)
            atomic(root / "policy.json", policy)
            atomic(root / "applied.json", policy)
            atomic(root / "cgroups.json", groups)
        atomic(root / "usage.json", state)
        atomic(root / "health.json", {"updated_at": time.time(), "ready": True})


def status(profile, root=ROOT, now=None):
    now = time.time() if now is None else now
    values = normalized(profile.get("limits"))
    state = read(root / "usage.json", {}).get(profile["id"], {})
    used = int(state.get("rx_bytes", 0)) + int(state.get("tx_bytes", 0))
    start = profile.get("limits_started_at")
    expires = start + values["days"] * 86400 if values["days"] is not None and start else None
    health = read(root / "health.json", {})
    enforced = profile.get("limits_controlled", False) and health.get("ready") is True and 0 <= now - health.get("updated_at", 0) < 15
    cap = values["traffic_gb"] * 1000000000 if values["traffic_gb"] is not None else None
    exhausted = cap is not None and (used >= cap or state.get("quota_limit_bytes") == cap and state.get("quota_used_bytes", 0) >= cap)
    outcome = "expired" if expires is not None and now >= expires else "exhausted" if exhausted else "active"
    if limited(values) and not enforced and outcome == "active":
        outcome = "unknown"
    return {"state": outcome, "enforced": bool(enforced), "used_bytes": used,
            "rx_bytes": int(state.get("rx_bytes", 0)), "tx_bytes": int(state.get("tx_bytes", 0)),
            "started_at": start, "expires_at": expires, "checked_at": health.get("updated_at")}


def userinfo(profile, root=ROOT):
    value = status(profile, root)
    total = normalized(profile.get("limits"))["traffic_gb"]
    return f"upload={value['tx_bytes']}; download={value['rx_bytes']}; total={total * 1000000000 if total is not None else 0}; expire={value['expires_at'] or 0}"


def current_policy(api):
    return read(api.DATA_ROOT / "subscription-limits" / "policy.json", {"schema": 1, "profiles": []})


def ensure_runtime(api):
    """Older updaters keep their pre-upgrade shell functions in memory.

    Bootstrap from the new manager too, before declaring its API ready.
    """
    if api.shutil.which("nft") and api.systemctl_active("vps-control-subscription-limits.service"):
        return
    installer = api.MODULE_ROOT / "install-subscription-limits.sh"
    subprocess.run(["bash", str(installer)], check=True, timeout=600,
                   env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    if not api.shutil.which("nft") or not api.systemctl_active("vps-control-subscription-limits.service"):
        raise RuntimeError("Subscription limits runtime is not ready after installation")


def resolve_profiles(api, profiles):
    """Resolve legacy identities from owned configuration, never guess a user."""
    result = deepcopy(profiles)
    configs = {}
    def config(path):
        if path not in configs:
            configs[path] = read(path, {})
        return configs[path]
    for profile in result:
        if not profile.get("limits_controlled"):
            continue
        for connection in [*profile.get("connections", []), *profile.get("retiring_connections", [])]:
            credential = connection.setdefault("credential", {})
            component = connection.get("component")
            if component == "transport-reality" and not credential.get("email"):
                data = config(api.CONFIG_ROOT / "reality" / "config.json")
                users = [user for inbound in data.get("inbounds", []) for user in inbound.get("settings", {}).get("clients", [])]
                matches = {user.get("email") for user in users
                           if (credential.get("uuid") and user.get("id") == credential["uuid"])
                           or (credential.get("password") and user.get("password") == credential["password"])}
                if len(matches) != 1 or not next(iter(matches), None):
                    raise RuntimeError("Не удалось однозначно определить пользователя Xray для контроля подписки")
                credential["email"] = matches.pop()
            elif component in {"transport-hysteria2", "transport-tuic"} and not credential.get("instance_id"):
                data = config(api.CONFIG_ROOT / "quic" / component.removeprefix("transport-") / "config.json")
                matches = {user.get("name") for inbound in data.get("inbounds", []) for user in inbound.get("users", [])
                           if credential.get("password") and user.get("password") == credential["password"]
                           and (component != "transport-tuic" or user.get("uuid") == credential.get("uuid"))}
                if len(matches) != 1 or not next(iter(matches), None):
                    raise RuntimeError("Не удалось однозначно определить пользователя QUIC для контроля подписки")
                credential["instance_id"] = matches.pop()
            elif component == "transport-shadowsocks" and not credential.get("instance_id"):
                matches = [path.stem for path in (api.CONFIG_ROOT / "shadowsocks").glob("*.json")
                           if config(path).get("server_port") == credential.get("port")
                           and credential.get("password") and config(path).get("password") == credential["password"]]
                if len(matches) != 1:
                    raise RuntimeError("Не удалось однозначно определить instance Shadowsocks для контроля подписки")
                credential["instance_id"] = matches[0]
    return result


def sync(api, profiles):
    candidate = plan(profiles)
    root = api.DATA_ROOT / "subscription-limits"
    previous = current_policy(api)
    if not candidate["profiles"] and not previous["profiles"]:
        return
    if candidate == previous:
        return
    # A saved limit must never be a UI-only promise on an older installation.
    if not api.shutil.which("nft") or not api.systemctl_active("vps-control-subscription-limits.service"):
        raise RuntimeError("Серверный контроль подписок не установлен или не запущен. Обновите приложение.")
    candidate = plan(resolve_profiles(api, profiles))
    if candidate == previous:
        return
    before = {}
    try:
        update(candidate, root)
        for module in ("transport-reality", "transport-hysteria2", "transport-tuic"):
            path = api.CONFIG_ROOT / ("reality" if module == "transport-reality" else f"quic/{module.removeprefix('transport-')}") / "config.json"
            if not path.is_file():
                continue
            config = json.loads(path.read_text(encoding="utf-8"))
            next_config = decorate_xray(config, candidate) if module == "transport-reality" else decorate_quic(config, candidate, module.removeprefix("transport-"))
            if next_config == config:
                continue
            active = api.systemctl_active(api.SERVICE_BY_MODULE[module])
            before[path] = (path.read_bytes(), module, active)
            if module == "transport-reality":
                api.apply_reality_config(path, next_config, restart_service=active)
            else:
                staged = path.with_name("subscription.candidate.json")
                api.atomic_json(staged, next_config)
                api.run(str(api.SINGBOX_BIN), "check", "-c", str(staged), check=True)
                os.replace(staged, path)
                if active:
                    api.run("systemctl", "restart", api.SERVICE_BY_MODULE[module], check=True)
            if active and not api.service_stably_active(api.SERVICE_BY_MODULE[module]):
                raise RuntimeError("Protocol did not remain active after subscription policy update")
    except Exception:
        for path, (content, module, active) in before.items():
            path.write_bytes(content)
            if active:
                api.run("systemctl", "restart", api.SERVICE_BY_MODULE[module], check=True)
        update(previous, root, force=True)
        raise


def restore(api, policy):
    root = api.DATA_ROOT / "subscription-limits"
    if read(root / "applied.json", current_policy(api)) != policy:
        update(policy, root, force=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("watch", "refresh"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        policy = read(args.root / "policy.json", {"schema": 1, "profiles": []})
        if policy["profiles"] or (args.root / "policy.json").exists() or (args.root / "applied.json").exists():
            update(policy, args.root, fresh_groups=args.action == "refresh")
        if args.action != "watch" or stopped:
            break
        time.sleep(1)


if __name__ == "__main__":
    main()
