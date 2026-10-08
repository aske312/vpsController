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

while read -r alias_unit; do
  [[ "${alias_unit}" =~ ^vps-control-awg-port@([0-9]+)\.service$ ]] || continue
  alias_port="${BASH_REMATCH[1]}"
  systemctl disable --now "${alias_unit}"
  rm -f -- "/var/lib/vps-control/awg-ports/${alias_port}.json"
done < <(systemctl list-unit-files 'vps-control-awg-port@*.service' --no-legend | awk '{print $1}')
systemctl disable --now "awg-quick@${AWG_INTERFACE}.service" 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
  ufw --force delete allow "${AWG_PORT}/udp" >/dev/null 2>&1 || true
  if [[ -n "${UPLINK_INTERFACE}" ]]; then
    while ufw status | grep -Fq "${AWG_SUBNET} on ${AWG_INTERFACE}"; do
      ufw --force route delete allow in on "${AWG_INTERFACE}" out on "${UPLINK_INTERFACE}" from "${AWG_SUBNET}" >/dev/null 2>&1 || break
    done
  fi
fi
rm -f -- "${PACKAGE_CONFIG}" "${CONFIGURED_AWG_CONFIG}" \
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
rm -f -- /etc/apt/sources.list.d/vps-control-amneziawg.list /etc/apt/keyrings/vps-control-amneziawg.gpg
add-apt-repository --remove -y ppa:amnezia/ppa >/dev/null 2>&1 || true
sysctl --system >/dev/null 2>&1 || true
