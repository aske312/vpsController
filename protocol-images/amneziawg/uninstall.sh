#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
AWG_INTERFACE="${AWG_INTERFACE:-awg0}"
AWG_PORT="${AWG_PORT:-51822}"
AWG_SUBNET="$(sed -n 's/^AWG_SUBNET=//p' "${ENV_FILE}" 2>/dev/null | tail -n 1)"
AWG_SUBNET="${AWG_SUBNET:-10.73.0.0/24}"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"
CONFIGURED_AWG_CONFIG="$(sed -n 's/^AWG_CONFIG=//p' "${ENV_FILE}" 2>/dev/null | tail -n 1)"
CONFIGURED_AWG_CONFIG="${CONFIGURED_AWG_CONFIG:-/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf}"
PACKAGE_CONFIG="/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf"
QUICK_CONFIG="/etc/amnezia/${AWG_INTERFACE}.conf"

systemctl disable --now "awg-quick@${AWG_INTERFACE}.service" 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
  ufw --force delete allow "${AWG_PORT}/udp" >/dev/null 2>&1 || true
  if [[ -n "${UPLINK_INTERFACE}" ]]; then
    while ufw --force delete route allow in on "${AWG_INTERFACE}" out on "${UPLINK_INTERFACE}" from "${AWG_SUBNET}" >/dev/null 2>&1; do :; done
  fi
fi
rm -f -- "${PACKAGE_CONFIG}" "${CONFIGURED_AWG_CONFIG}" "${QUICK_CONFIG}" \
  /etc/sysctl.d/99-vps-control-amneziawg.conf \
  /var/lib/vps-control/monitor/awg.csv /var/lib/vps-control/monitor/awg.state
python3 - <<'PY'
import json
from pathlib import Path
path = Path("/var/lib/vps-control/clients.json")
if path.exists():
    items = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps([item for item in items if item.get("protocol") != "awg"], ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
PY
apt-get -o DPkg::Lock::Timeout=300 purge -y amneziawg amneziawg-tools amneziawg-dkms
add-apt-repository --remove -y ppa:amnezia/ppa >/dev/null 2>&1 || true
rm -f -- /etc/apt/sources.list.d/amnezia-ppa.list /usr/share/keyrings/amnezia-ppa.gpg
sysctl --system >/dev/null 2>&1 || true
