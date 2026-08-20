#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR="/etc/vps-control/mihomo/shadowsocks"

# Remove firewall rules for every Mihomo-owned Shadowsocks profile before configs disappear.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  shopt -s nullglob
  for config in "${CONFIG_DIR}"/*.json; do
    port="$(python3 - "${config}" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get("server_port", "")
except (OSError, ValueError, AttributeError):
    value = ""
print(value)
PY
)"
    [[ "${port}" =~ ^[0-9]+$ ]] || continue
    ufw delete allow "${port}/tcp" >/dev/null 2>&1 || true
    ufw delete allow "${port}/udp" >/dev/null 2>&1 || true
  done
  shopt -u nullglob
fi

# Stop every instantiated profile unit, not only the template unit.
while read -r unit; do
  [[ -n "${unit}" ]] || continue
  systemctl disable --now "${unit}" >/dev/null 2>&1 || true
done < <(
  systemctl list-units --all --plain --no-legend 'vps-control-mihomo-ss@*.service' 2>/dev/null \
    | awk '{print $1}'
)

systemctl disable --now vps-control-mihomo-ss.target >/dev/null 2>&1 || true
rm -f /etc/systemd/system/vps-control-mihomo-ss.target \
      /etc/systemd/system/vps-control-mihomo-ss@.service
rm -rf -- "${CONFIG_DIR}"
systemctl daemon-reload

echo "Mihomo/Shadowsocks и все его профильные instances удалены; direct Shadowsocks не изменялся."
