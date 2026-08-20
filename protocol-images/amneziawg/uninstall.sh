#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
AWG_INTERFACE="${AWG_INTERFACE:-awg0}"
AWG_PORT="${AWG_PORT:-51822}"
env_value() {
  local value
  value="$(sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | tr -d '\r')"
  [[ "${value}" == \"*\" && "${value}" == *\" ]] && value="${value:1:${#value}-2}"
  printf '%s\n' "${value}"
}
AWG_SUBNET="$(env_value AWG_SUBNET)"
AWG_SUBNET="${AWG_SUBNET:-10.73.0.0/24}"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"
CONFIGURED_AWG_CONFIG="$(env_value AWG_CONFIG)"
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
# The amneziawg package owns awg-quick@.service, a template shared with any
# other AmneziaWG instance (e.g. Mihomo's isolated mh-awg0 channel). Purging
# it deletes that template out from under the other instance, which then
# can't be stopped or torn down cleanly anymore. Only purge when this was
# the last configured instance.
other_instance="$(find /etc/amnezia/amneziawg -mindepth 1 -maxdepth 1 -name '*.conf' ! -name "${AWG_INTERFACE}.conf" -print -quit 2>/dev/null)"
if [[ -z "${other_instance}" ]]; then
  apt-get -o DPkg::Lock::Timeout=300 purge -y amneziawg amneziawg-tools amneziawg-dkms
  add-apt-repository --remove -y ppa:amnezia/ppa >/dev/null 2>&1 || true
  rm -f -- /etc/apt/sources.list.d/amnezia-ppa.list /usr/share/keyrings/amnezia-ppa.gpg
else
  echo "amneziawg package kept: still used by $(basename -- "${other_instance}" .conf)"
fi
sysctl --system >/dev/null 2>&1 || true
