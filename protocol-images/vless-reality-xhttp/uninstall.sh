#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR="/etc/vps-control/vless-reality-xhttp"
port="443"
if [[ -s "${CONFIG_DIR}/reality.env" ]]; then
  port="$(sed -n 's/^PORT=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
fi
systemctl disable --now vps-control-vless-reality-xhttp.service 2>/dev/null || true
rm -f -- /etc/systemd/system/vps-control-vless-reality-xhttp.service
rm -rf -- /etc/vps-control/vless-reality-xhttp /usr/local/lib/vps-control-vless-reality-xhttp
python3 - <<'PY'
import fcntl, json, os
from pathlib import Path
snippet = Path("/etc/caddy/vps-control.d/vless-cdn.caddy")
root = Path("/etc/vps-control/mihomo/reality/caddy-routes")
routes = []
if root.exists():
    for descriptor in root.glob("*.json"):
        try:
            route = json.loads(descriptor.read_text(encoding="utf-8"))
            if route.get("domain") and route.get("path") and route.get("port"):
                routes.append(route)
        except (OSError, ValueError, TypeError):
            continue
grouped = {}
for route in routes:
    grouped.setdefault(str(route["domain"]), []).append(route)
snippet.parent.mkdir(parents=True, exist_ok=True)
with snippet.with_suffix(".lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not grouped:
        snippet.unlink(missing_ok=True)
    else:
        lines = []
        for domain, items in grouped.items():
            lines.append(f"{domain} {{")
            for item in items:
                matcher = f"{item['path']}*" if item.get("transport") in {"xhttp", "grpc"} else str(item["path"])
                upstream = f"h2c://127.0.0.1:{int(item['port'])}" if item.get("transport") == "grpc" else f"127.0.0.1:{int(item['port'])}"
                lines += [f"    handle {matcher} {{", f"        reverse_proxy {upstream}", "    }"]
            lines += ["    respond 404", "}"]
        temporary = snippet.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, snippet)
PY
python3 - <<'PY'
import json, os
path = "/var/lib/vps-control/clients.json"
try:
    with open(path, encoding="utf-8") as source:
        items = json.load(source)
except (OSError, ValueError):
    items = []
items = [item for item in items if item.get("protocol") != "vless-reality-xhttp"]
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as target:
    json.dump(items, target, ensure_ascii=False, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
if [[ "${port}" =~ ^[0-9]+$ ]] && command -v ufw >/dev/null 2>&1; then
  ufw --force delete allow "${port}/tcp" >/dev/null 2>&1 || true
fi
systemctl daemon-reload
systemctl reload caddy.service 2>/dev/null || true
systemctl reset-failed >/dev/null 2>&1 || true
echo "Модуль VLESS + REALITY + XHTTP удалён независимо от остальных протоколов."
