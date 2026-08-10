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
systemctl reset-failed >/dev/null 2>&1 || true
echo "Модуль VLESS + REALITY + XHTTP удалён независимо от остальных протоколов."
