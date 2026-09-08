"""Shared Caddy policy for every CDN writer. No Cloudflare account secrets needed."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent / "resources"
STATE = Path("/etc/vps-control/cdn-security.json")
ENV = Path("/etc/vps-control/environment")
DIRECT_ENV = Path("/etc/vps-control/vless-reality-xhttp/reality.env")
ROUTES = Path("/etc/vps-control/mihomo/reality/caddy-routes")
SNIPPET = Path("/etc/caddy/vps-control.d/vless-cdn.caddy")
CADDY = Path("/etc/caddy/Caddyfile")


def read_env(path: Path) -> dict[str, str]:
    try:
        return {k: v.strip().strip('"') for line in path.read_text(encoding="utf-8").splitlines() if "=" in line for k, v in [line.split("=", 1)]}
    except FileNotFoundError:
        return {}


def settings() -> dict:
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or type(value.get("authenticated_origin_pulls")) is not bool:
            raise ValueError("Invalid CDN security settings")
        return value
    except FileNotFoundError:
        return {"authenticated_origin_pulls": False}


def networks() -> list[str]:
    values = []
    for version in (4, 6):
        rows = (RESOURCES / f"cloudflare-ips-v{version}.txt").read_text().splitlines()
        if not rows:
            raise ValueError("Empty Cloudflare network list")
        for row in rows:
            network = ipaddress.ip_network(row)
            if network.version != version or network.prefixlen == 0 or not network.is_global:
                raise ValueError("Invalid Cloudflare network list")
            values.append(str(network))
    return values


def read_routes() -> list[dict]:
    values = read_env(DIRECT_ENV)
    routes = []
    for prefix in ("CDN", "TLS"):
        if values.get(f"{prefix}_ENABLED") == "yes" and values.get(f"{prefix}_DOMAIN"):
            routes.append({"domain": values[f"{prefix}_DOMAIN"], "path": values.get(f"{prefix}_PATH", values.get("WS_PATH", "/")), "port": int(values.get(f"{prefix}_PORT", "10087")), "transport": values.get(f"{prefix}_TRANSPORT", "websocket"), "cloudflare": prefix == "CDN"})
    for path in sorted(ROUTES.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        # Old descriptors used the tls- filename prefix, before an explicit flag.
        value.setdefault("cloudflare", not path.stem.startswith("tls-"))
        routes.append(value)
    return routes


def render_routes(routes: list[dict], policy: dict | None = None, protected: bool | None = None, probe: str = "") -> str:
    policy = settings() if policy is None else policy
    protected = read_env(ENV).get("ACCESS_MODE") == "vpn" if protected is None else protected
    aop = bool(policy.get("authenticated_origin_pulls"))
    ranges = " ".join(networks())
    grouped: dict[str, list[dict]] = {}
    for route in routes:
        domain = str(route["domain"]).lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or ".." in domain:
            raise ValueError("Invalid CDN domain")
        grouped.setdefault(domain, []).append(route)
    lines = []
    for domain, items in grouped.items():
        cf_only = all(item.get("cloudflare", True) for item in items)
        if aop and not cf_only and any(item.get("cloudflare", True) for item in items):
            raise ValueError("Для CDN и прямого TLS нужны разные домены перед включением проверки CF")
        lines.append(f"{domain} {{")
        if aop and cf_only:
            # Debian's Caddy 2.6 and newer releases both accept this directive.
            lines += ["    tls {", "        client_auth {", "            mode require_and_verify", f'            trusted_ca_cert_file {json.dumps(str(RESOURCES / "cloudflare-origin-pull-ca.pem"))}', "        }", "    }"]
        # A route preserves ordering: reject before any reverse_proxy handler.
        lines.append("    route {")
        if probe and cf_only:
            # The unguessable probe validates Cloudflare mTLS itself. Keep it
            # ahead of source filtering so a stale range snapshot cannot make
            # certificate verification report a misleading HTTP 403.
            lines += [f"        handle /__cf_check_{probe} {{", f'            respond "{probe}" 200', "        }"]
        if cf_only and (protected or aop):
            lines += [f"        @outsideCF not remote_ip {ranges}", "        respond @outsideCF 403"]
        for index, item in enumerate(items):
            path = str(item["path"])
            if not re.fullmatch(r"/[A-Za-z0-9_./-]*", path):
                raise ValueError("Invalid transport path")
            port = int(item["port"])
            if not 1 <= port <= 65535:
                raise ValueError("Invalid transport port")
            matcher = path + "*" if item.get("transport") in {"xhttp", "grpc"} else path
            upstream = ("h2c://" if item.get("transport") == "grpc" else "") + f"127.0.0.1:{port}"
            lines += [f"        handle {matcher} {{", "            route {"]
            if not cf_only and item.get("cloudflare", True) and protected:
                lines += [f"                @outsideCF{index} not remote_ip {ranges}", f"                respond @outsideCF{index} 403"]
            lines += [f"                reverse_proxy {upstream}", "            }", "        }"]
        lines += ["        respond 404", "    }", "}"]
    return "\n".join(lines) + ("\n" if lines else "")


def write_snippet(text: str) -> None:
    SNIPPET.parent.mkdir(parents=True, exist_ok=True)
    candidate = SNIPPET.with_suffix(".tmp")
    candidate.write_text(text, encoding="utf-8")
    os.chmod(candidate, 0o644)
    candidate.replace(SNIPPET)


def reload_caddy() -> None:
    subprocess.run(["caddy", "validate", "--config", str(CADDY)], check=True, capture_output=True, timeout=10)
    subprocess.run(["systemctl", "reload", "caddy.service"], check=True, capture_output=True, timeout=15)


def configure_firewall() -> None:
    """Retain ACME HTTP and explicit direct TLS; close the old blanket CDN rule."""
    routes = read_routes()
    protected = read_env(ENV).get("ACCESS_MODE") == "vpn"
    if protected and not any(not route.get("cloudflare", True) for route in routes):
        # Add replacement rules first. VPN-interface rules belong to the panel manager.
        if routes or read_env(ENV).get("PUBLIC_DOMAIN"):
            for network in networks():
                subprocess.run(["ufw", "allow", "from", network, "to", "any", "port", "443", "proto", "tcp", "comment", "GATE.312 Cloudflare HTTPS"], check=True, capture_output=True)
        for rule in ("443/tcp", "https", "WWW Secure", "WWW Full", "Nginx Full", "Nginx HTTPS"):
            subprocess.run(["ufw", "--force", "delete", "allow", rule], capture_output=True)
    elif routes:
        subprocess.run(["ufw", "allow", "443/tcp", "comment", "GATE.312 configured HTTPS transport"], check=True, capture_output=True)
    # HTTP remains reachable for certificate issuance, never an origin proxy.
    if routes or read_env(ENV).get("PUBLIC_DOMAIN"):
        subprocess.run(["ufw", "allow", "80/tcp", "comment", "GATE.312 ACME HTTP"], check=True, capture_output=True)


def configure_aop(enabled: bool) -> None:
    """Rollback if validation, reload or end-to-end Cloudflare verification fails."""
    SNIPPET.parent.mkdir(parents=True, exist_ok=True)
    with SNIPPET.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        routes = read_routes()
        domains = sorted({r["domain"] for r in routes if r.get("cloudflare", True)})
        if enabled and not domains:
            raise ValueError("Сначала настройте CDN-подключение Cloudflare")
        previous = SNIPPET.read_bytes() if SNIPPET.exists() else None
        previous_state = STATE.read_bytes() if STATE.exists() else None
        policy = {"authenticated_origin_pulls": enabled}
        probe = secrets.token_hex(24) if enabled else ""
        try:
            write_snippet(render_routes(routes, policy, probe=probe))
            reload_caddy()
            deadline = time.monotonic() + 60
            for domain in domains if enabled else []:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Cloudflare verification deadline exceeded")
                # Random, uncached response proves this origin was reached using mTLS.
                request = urllib.request.Request(f"https://{domain}/__cf_check_{probe}", headers={"Cache-Control": "no-cache"})
                with urllib.request.urlopen(request, timeout=min(12, remaining)) as response:
                    if response.read(256).decode() != probe:
                        raise ValueError("Cloudflare origin verification failed")
            write_snippet(render_routes(routes, policy))
            reload_caddy()
            STATE.parent.mkdir(parents=True, exist_ok=True)
            candidate = STATE.with_suffix(".tmp")
            candidate.write_text(json.dumps(policy) + "\n", encoding="utf-8")
            os.chmod(candidate, 0o644)
            candidate.replace(STATE)
        except Exception as exc:
            if previous is None:
                SNIPPET.unlink(missing_ok=True)
            else:
                SNIPPET.write_bytes(previous)
            if previous_state is None:
                STATE.unlink(missing_ok=True)
            else:
                STATE.write_bytes(previous_state)
            reload_caddy()
            raise RuntimeError("Настройка CF не применена; восстановлена предыдущая конфигурация. Проверьте Full (strict), Authenticated Origin Pulls и доступность CDN.") from exc


if __name__ == "__main__":
    if sys.argv[1:] == ["rebuild"]:
        SNIPPET.parent.mkdir(parents=True, exist_ok=True)
        with SNIPPET.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            write_snippet(render_routes(read_routes()))
    elif sys.argv[1:] == ["networks"]:
        print(" ".join(networks()))
    elif sys.argv[1:] == ["firewall"]:
        configure_firewall()
    elif len(sys.argv) == 3 and sys.argv[1] == "aop" and sys.argv[2] in {"enable", "disable"}:
        configure_aop(sys.argv[2] == "enable")
    else:
        raise SystemExit("Usage: cdn_security.py rebuild | aop enable|disable")
