#!/usr/bin/env bash
set -Eeuo pipefail

shopt -s nullglob
for config in /etc/vps-control/shadowsocks/clients/*.json; do
  client_id="$(basename "${config}" .json)"
  port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("server_port", ""))' "${config}" 2>/dev/null || true)"
  systemctl disable --now "vps-control-shadowsocks@${client_id}.service" 2>/dev/null || true
  if [[ "${port}" =~ ^[0-9]+$ ]] && command -v ufw >/dev/null 2>&1; then
    ufw --force delete allow "${port}/tcp" >/dev/null 2>&1 || true
    ufw --force delete allow "${port}/udp" >/dev/null 2>&1 || true
  fi
done

systemctl disable --now vps-control-shadowsocks.target 2>/dev/null || true
rm -f -- /etc/systemd/system/vps-control-shadowsocks.target \
  /etc/systemd/system/vps-control-shadowsocks@.service
rm -rf -- /etc/vps-control/shadowsocks
python3 - <<'PY'
import json, os
path = "/var/lib/vps-control/clients.json"
try:
    with open(path, encoding="utf-8") as source:
        items = json.load(source)
except (OSError, ValueError):
    items = []
items = [item for item in items if item.get("protocol") != "shadowsocks"]
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as target:
    json.dump(items, target, ensure_ascii=False, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
if find /etc/vps-control/mihomo/shadowsocks -mindepth 1 -maxdepth 1 -name '*.json' -print -quit 2>/dev/null | grep -q .; then
  echo "shadowsocks-libev сохранён: пакет используется каналом Mihomo."
else
  apt-get -o DPkg::Lock::Timeout=300 purge -y shadowsocks-libev
fi
echo "Управляемый модуль Shadowsocks удалён."
