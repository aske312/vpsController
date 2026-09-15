"""Shared TCP/UDP reservations for panel installers and connection managers."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading

CONFIG_ROOT = Path("/etc/vps-control")
DATA_ROOT = Path("/var/lib/vps-control")
ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/vps-control/environment"))
_lock = threading.RLock()

# module: (environment key, protocol, default port, settings key)
LISTENERS = {
    "wg": [("WG_PORT", "udp", 51820, "port")],
    "awg": [("AWG_PORT", "udp", 51822, "port")],
    "hysteria2": [("HYSTERIA2_PORT", "udp", 8443, "port"),
                  ("HYSTERIA2_AUTH_PORT", "tcp", 18081, "auth_port"),
                  ("HYSTERIA2_STATS_PORT", "tcp", 18082, "stats_port")],
    "tuic": [("TUIC_PORT", "udp", 8444, "port")],
    "trojan": [("TROJAN_PORT", "tcp", 8445, "port")],
    "openvpn": [("OPENVPN_PORT", "udp", 1194, "port")],
    "vless-reality-xhttp": [("VLESS_REALITY_PORT", "tcp", 8443, "PORT"),
                            ("VLESS_CDN_PORT", "tcp", 10087, "CDN_PORT"),
                            ("VLESS_TLS_PORT", "tcp", 10088, "TLS_PORT"),
                            ("VLESS_API_PORT", "tcp", 10085, "API_PORT")],
    "ikev2": [("IKE_PORT", "udp", 500, "port"), ("IKE_NAT_PORT", "udp", 4500, "nat_port")],
}
MIHOMO_DEFAULTS = {"transport-wg": 51830, "transport-awg": 51831,
                   "transport-hysteria2": 18443, "transport-tuic": 10443,
                   "transport-reality": 10086}


class PortConflict(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError("Некорректный файл настроек портов")
    return value


def read_env(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    result = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            parts = shlex.split(value, comments=True)
            if parts:
                result[key.strip()] = parts[0]
    return result


def read_records(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(value, list):
        raise RuntimeError("Некорректный список сохранённых подключений")
    return [item for item in value if isinstance(item, dict)]


def module_ports(module: str) -> list[tuple[str, str, int, bool]]:
    env = read_env(ENV_FILE)
    values = read_json(CONFIG_ROOT / module / "settings.json")
    configured = set(values)
    if module == "ikev2" and (CONFIG_ROOT / module / "settings.json").exists():
        values.update(port=500, nat_port=4500)
        configured.update(("port", "nat_port"))
    if module in {"wg", "awg"}:
        prefix = module.upper()
        interface = env.get(f"{prefix}_INTERFACE", f"{module}0")
        default = CONFIG_ROOT.parent / ("wireguard" if module == "wg" else "amnezia/amneziawg") / f"{interface}.conf"
        path = Path(env.get(f"{prefix}_CONFIG", default))
        if path.exists():
            match = re.search(r"(?m)^ListenPort\s*=\s*(\d+)", path.read_text())
            if match:
                values["port"] = int(match[1]); configured.add("port")
    if module == "vless-reality-xhttp":
        values = read_env(CONFIG_ROOT / module / "reality.env")
        configured = set(values)
        config = read_json(CONFIG_ROOT / module / "config.json")
        if config.get("api", {}).get("listen"):
            values["API_PORT"] = int(config["api"]["listen"].rsplit(":", 1)[1]); configured.add("API_PORT")
    if module == "hysteria2" and (CONFIG_ROOT / module / "config.yaml").exists():
        # Legacy installations used fixed local ports before storing them.
        for key, default in (("auth_port", 18081), ("stats_port", 18082)):
            values.setdefault(key, default); configured.add(key)
    result = []
    for key, protocol, default, setting in LISTENERS.get(module, []):
        if module == "openvpn":
            protocol = "tcp" if str(values.get("protocol", "udp")).startswith("tcp") else "udp"
        port = int(values.get(setting, env.get(key, os.getenv(key, default))))
        result.append((key, protocol, port, setting in configured))
    return result


def socket_ports() -> set[tuple[str, int]]:
    result = subprocess.run(["ss", "-Hanut"], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("Не удалось проверить занятые TCP/UDP-порты")
    ports = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        try:
            if len(fields) < 5 or fields[0] not in {"tcp", "udp"}:
                raise ValueError()
            ports.add((fields[0], int(fields[4].rsplit(":", 1)[1])))
        except ValueError as exc:
            raise RuntimeError("Не удалось прочитать список TCP/UDP-портов") from exc
    return ports


def reservations() -> dict[str, set[tuple[str, int]]]:
    claims = read_json(DATA_ROOT / "port-reservations.json")
    result = {owner: {(protocol, int(value["port"])) for protocol in value["protocols"]}
              for owner, value in claims.items()}
    for module in LISTENERS:
        for key, protocol, port, configured in module_ports(module):
            owner = f"panel:{module}:{key}"
            if configured or owner not in result:
                result.setdefault(owner, set()).add((protocol, port))
    for module, default in MIHOMO_DEFAULTS.items():
        key = "api_port" if module == "transport-reality" else "port"
        protocol = "tcp" if key == "api_port" else "udp"
        values = read_json(DATA_ROOT / "mihomo/settings" / f"{module}.json")
        owner = f"mihomo:{module}"
        if key in values or owner not in result:
            result.setdefault(owner, set()).add((protocol, int(values.get(key, default))))
        if module in {"transport-hysteria2", "transport-tuic"}:
            config = read_json(CONFIG_ROOT / "mihomo/quic" / module.removeprefix("transport-") / "config.json")
            result.setdefault(owner, set()).update(("udp", int(row["listen_port"])) for row in config.get("inbounds", []) if row.get("listen_port"))
        if module in {"transport-wg", "transport-awg"}:
            name = module.removeprefix("transport-")
            path = CONFIG_ROOT.parent / ("wireguard" if name == "wg" else "amnezia/amneziawg") / f"mh-{name}0.conf"
            if path.exists():
                match = re.search(r"(?m)^ListenPort\s*=\s*(\d+)", path.read_text())
                if match:
                    result.setdefault(owner, set()).add(("udp", int(match[1])))
    for client in read_records(DATA_ROOT / "clients.json"):
        if client.get("protocol") == "shadowsocks" and client.get("port"):
            port = int(client["port"])
            result[f"client:panel:{client.get('id')}"] = {("tcp", port), ("udp", port)}
    for profile in read_records(DATA_ROOT / "mihomo/profiles.json"):
        for index, connection in enumerate([*profile.get("connections", []), *profile.get("retiring_connections", [])]):
            component = connection.get("component")
            credential = connection.get("credential", {})
            if component == "transport-reality":
                entries = {("tcp", int(credential[key])) for key in ("port", "cdn_port", "tls_port") if credential.get(key)}
            elif component == "transport-shadowsocks":
                entries = {(protocol, int(credential["port"])) for protocol in ("tcp", "udp") if credential.get("port")}
            elif component in MIHOMO_DEFAULTS:
                entries = {("udp", int(credential["port"]))} if credential.get("port") else set()
                result.setdefault(f"mihomo:{component}", set()).update(entries)
                continue
            else:
                continue
            result[f"client:mihomo:{profile.get('id')}:{index}"] = entries
    for base in (CONFIG_ROOT / "shadowsocks/clients", CONFIG_ROOT / "mihomo/shadowsocks"):
        for path in base.glob("*.json"):
            try:
                port = int(read_json(path).get("server_port", 0))
            except (TypeError, ValueError):
                continue  # The installer can repair a legacy invalid port.
            if 1 <= port <= 65535:
                result[f"config:{path}"] = {("tcp", port), ("udp", port)}
    for module, base in (("vless-reality-xhttp", CONFIG_ROOT), ("transport-reality", CONFIG_ROOT / "mihomo")):
        path = base / ("reality" if module == "transport-reality" else module) / "config.json"
        for inbound in read_json(path).get("inbounds", []):
            if inbound.get("port"):
                owner = "mihomo:transport-reality" if module == "transport-reality" and inbound.get("tag") == "api" else f"config:{path}:{inbound.get('tag')}"
                direct_key = {"vless-reality-xhttp": "VLESS_REALITY_PORT", "vless-cdn": "VLESS_CDN_PORT", "vless-tls": "VLESS_TLS_PORT"}.get(inbound.get("tag"))
                if module == "vless-reality-xhttp" and direct_key:
                    owner = f"panel:{module}:{direct_key}"
                result.setdefault(owner, set()).add(("tcp", int(inbound["port"])))
    return result


def reserved_ports(protocols: set[str], exclude: str | None = None) -> set[int]:
    return {port for owner, entries in reservations().items() if owner != exclude
            for protocol, port in entries if protocol in protocols}


@contextmanager
def allocation_lock():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with _lock, (DATA_ROOT / "port-reservations.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def release(owner: str) -> None:
    if not (DATA_ROOT / "port-reservations.json").exists():
        return
    with allocation_lock():
        claims = read_json(DATA_ROOT / "port-reservations.json")
        if owner in claims:
            del claims[owner]
            write_claims(claims)


def release_prefix(prefix: str) -> None:
    if not (DATA_ROOT / "port-reservations.json").exists():
        return
    with allocation_lock():
        claims = read_json(DATA_ROOT / "port-reservations.json")
        updated = {key: value for key, value in claims.items() if not key.startswith(prefix)}
        if updated != claims:
            write_claims(updated)


def release_module(module: str) -> None:
    """Release installer reservations, including per-client Shadowsocks ports."""
    prefix = "panel:ss:" if module == "shadowsocks" else f"panel:{module}:"
    release_prefix(prefix)


def release_on_error(prefix):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception:
                owner = prefix(*args, **kwargs)
                if owner.endswith(":"):
                    release_prefix(owner)
                else:
                    release(owner)
                raise
        return wrapped
    return decorate


def save_claim(owner: str, port: int, protocols: set[str]) -> None:
    claims = read_json(DATA_ROOT / "port-reservations.json")
    claims[owner] = {"port": port, "protocols": sorted(protocols)}
    write_claims(claims)


def write_claims(claims: dict) -> None:
    fd, path = tempfile.mkstemp(dir=DATA_ROOT, prefix=".port-reservations-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(claims, target)
        os.replace(path, DATA_ROOT / "port-reservations.json")
    finally:
        Path(path).unlink(missing_ok=True)


def allocate(owner: str, start: int, protocols: set[str], *, used: set[int] | None = None,
             count: int = 65536, fixed: bool = False, allow_live: bool = False,
             snapshot=None) -> int:
    if not 1 <= start <= 65535:
        raise PortConflict("Некорректный начальный порт")
    with allocation_lock():
        occupied = reserved_ports(protocols, exclude=owner) | (used or set())
        live = snapshot() if snapshot else {port for protocol, port in socket_ports() if protocol in protocols}
        occupied |= live - ({start} if allow_live else set())
        ranges = [(start, start + 1)] if fixed else [(start, min(65536, start + count))]
        if not fixed and count >= 65536:
            ranges.append((1024, start))
        port = next((port for low, high in ranges for port in range(low, high) if port not in occupied), None)
        if port is None:
            raise PortConflict(f"Порт {start} занят или диапазон исчерпан; действующие подключения не изменены.")
        save_claim(owner, port, protocols)
        return port


@release_on_error(lambda module: f"panel:{module}:")
def prepare(module: str) -> dict[str, int]:
    env = read_env(ENV_FILE)
    service = f"vps-control-{module}.service"
    if module in {"wg", "awg"}:
        service = f"{module}-quick@{env.get(module.upper() + '_INTERFACE', module + '0')}.service"
    active = subprocess.run(["systemctl", "is-active", "--quiet", service]).returncode == 0 if module in LISTENERS else False
    result = {}
    for key, protocol, port, configured in module_ports(module):
        # Standard IKE ports are part of native client interoperability.
        result[key] = allocate(f"panel:{module}:{key}", port, {protocol}, fixed=configured or module == "ikev2", allow_live=configured and active)
    return result


if __name__ == "__main__":
    import sys
    try:
        if len(sys.argv) > 2 and sys.argv[2] == "--release":
            release_module(sys.argv[1])
        else:
            for key, port in prepare(sys.argv[1]).items():
                print(f"{key}={port}")
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
