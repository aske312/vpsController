"""Render and preflight the gateway using code shipped with the target release."""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import tempfile
from pathlib import Path

import cdn_security


PRIVATE_SOURCES = "127.0.0.0/8 ::1/128 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"


def render(template: str, mode: str, port: int, values: dict[str, str]) -> str:
    domain = values.get("PUBLIC_DOMAIN", "")
    if domain and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain):
        raise ValueError("Invalid public domain")
    if mode not in {"vpn", "external", "local"} or not 1 <= port <= 65535:
        raise ValueError("Invalid panel access settings")
    site = domain if domain and mode == "external" else f"http://localhost:{port}" if mode == "vpn" else f":{port}"
    substitutions = {
        "{$SITE_ADDRESS}": site, "{$HTTP_PORT}": str(port),
        "{INTERNAL_PANEL_HOST}": "admin.312.net",
        "{WG_PANEL_ADDRESS}": str(next(ipaddress.ip_network(values.get("WG_SUBNET") or "10.72.0.0/24").hosts())),
        "{AWG_PANEL_ADDRESS}": str(next(ipaddress.ip_network(values.get("AWG_SUBNET") or "10.73.0.0/24").hosts())),
        "{PANEL_ACCESS_GUARD}": f"@outsidePanel not remote_ip {PRIVATE_SOURCES}\n    respond @outsidePanel 403" if mode == "vpn" else "",
    }
    for key, value in substitutions.items():
        template = template.replace(key, value)
    if mode == "vpn" and domain:
        template += f"""
# Token-protected subscription refresh through CF or a configured tunnel.
{domain} {{
    route {{
        @outsideProtectedConnection not remote_ip {PRIVATE_SOURCES} {' '.join(cdn_security.networks())}
        respond @outsideProtectedConnection 403
        header {{
            -Server
            -X-Powered-By
        }}
        handle /s/* {{
            reverse_proxy 127.0.0.1:8791
        }}
        handle /api/mihomo/subscriptions/* {{
            reverse_proxy 127.0.0.1:8791
        }}
        respond 404
    }}
}}
"""
    return template


def validate_candidate(template_root: Path, mode: str, port: int, values: dict[str, str], caddy: str = "caddy") -> None:
    """Only temporary files; no reload, firewall changes or production file writes."""
    with tempfile.TemporaryDirectory(prefix="vps-gateway-check-") as temp:
        root = Path(temp)
        snippet = root / "transports.caddy"
        routes = cdn_security.read_routes()
        snippet.write_text(cdn_security.render_routes(routes, protected=mode == "vpn"), encoding="utf-8")
        source = render((template_root / "Caddyfile").read_text(encoding="utf-8"), mode, port, values)
        source = source.replace("import /etc/caddy/vps-control.d/*.caddy", f'import "{snippet.as_posix()}"' if routes else "")
        # Keep third-party snippets in the check, without duplicating the managed file.
        for existing in sorted(cdn_security.SNIPPET.parent.glob("*.caddy")):
            if existing.name != "vless-cdn.caddy" and not existing.name.startswith("mihomo-vless-"):
                source += f'\nimport "{existing.as_posix()}"\n'
        path = root / "Caddyfile"
        path.write_text(source, encoding="utf-8")
        subprocess.run([caddy, "validate", "--config", str(path), "--adapter", "caddyfile"], check=True, capture_output=True, timeout=30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "write"])
    parser.add_argument("--mode", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("/etc/caddy/Caddyfile"))
    args = parser.parse_args()
    values = cdn_security.read_env(cdn_security.ENV)
    if args.action == "check":
        validate_candidate(args.root, args.mode, args.port, values)
    else:
        text = render((args.root / "Caddyfile").read_text(encoding="utf-8"), args.mode, args.port, values)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        candidate = args.output.with_suffix(".candidate")
        candidate.write_text(text, encoding="utf-8")
        os.chmod(candidate, 0o644)
        candidate.replace(args.output)
