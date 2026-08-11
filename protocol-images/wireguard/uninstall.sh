#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
WG_INTERFACE="${WG_INTERFACE:-wg0}"
WG_PORT="${WG_PORT:-51820}"
env_value() {
  local value
  value="$(sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ "${value}" == \"*\" && "${value}" == *\" ]] && value="${value:1:${#value}-2}"
  printf '%s\n' "${value}"
}
WG_SUBNET="$(env_value WG_SUBNET)"
WG_SUBNET="${WG_SUBNET:-10.72.0.0/24}"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"
WG_CONFIG="$(env_value WG_CONFIG)"
WG_CONFIG="${WG_CONFIG:-/etc/wireguard/${WG_INTERFACE}.conf}"

systemctl disable --now "wg-quick@${WG_INTERFACE}.service" 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
  ufw --force delete allow "${WG_PORT}/udp" >/dev/null 2>&1 || true
  if [[ -n "${UPLINK_INTERFACE}" ]]; then
    while ufw --force delete route allow in on "${WG_INTERFACE}" out on "${UPLINK_INTERFACE}" from "${WG_SUBNET}" >/dev/null 2>&1; do :; done
  fi
fi
rm -f -- "${WG_CONFIG}" /etc/sysctl.d/99-vps-control-wireguard.conf \
  /var/lib/vps-control/monitor/wg.csv /var/lib/vps-control/monitor/wg.state
python3 - <<'PY'
import json
from pathlib import Path
path = Path("/var/lib/vps-control/clients.json")
if path.exists():
    items = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps([item for item in items if item.get("protocol") != "wg"], ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
PY
apt-get -o DPkg::Lock::Timeout=300 purge -y wireguard-tools
sysctl --system >/dev/null 2>&1 || true
