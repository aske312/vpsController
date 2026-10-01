#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/vps-control"
MODULE_DIR="${APP_ROOT}/protocol-images/mihomo"
DATA_DIR="/var/lib/vps-control/mihomo"
CONFIG_DIR="/etc/vps-control/mihomo"
PROFILE_FILE="${DATA_DIR}/profiles.json"
MANAGER_SERVICE="vps-control-mihomo-manager.service"
CAPABILITIES_SERVICE="vps-control-mihomo-capabilities.service"
CAPABILITIES_TIMER="vps-control-mihomo-capabilities.timer"
CAPABILITIES_HELPER_DIR="/usr/local/lib/vps-control-mihomo"
CAPABILITIES_DATA="/var/lib/vps-control/mihomo-capabilities.json"

usage_summary="$(python3 - "${PROFILE_FILE}" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        profiles = json.load(handle)
except (OSError, ValueError):
    profiles = []
if not isinstance(profiles, list):
    profiles = []
credentials = 0
for profile in profiles:
    if not isinstance(profile, dict):
        continue
    if isinstance(profile.get("connections"), list):
        credentials += len(profile["connections"])
    elif isinstance(profile.get("credentials"), dict):
        credentials += len(profile["credentials"])
print(f"{len(profiles)} {credentials}")
PY
)"
read -r PROFILE_COUNT CREDENTIAL_COUNT <<<"${usage_summary:-0 0}"

echo "Mihomo cascade removal: profiles=${PROFILE_COUNT:-0}, credentials=${CREDENTIAL_COUNT:-0}"

# Freeze the manager first so profile/config state cannot change during cascade.
systemctl stop "${MANAGER_SERVICE}" >/dev/null 2>&1 || true

failures=()
# Remove config consumers first, then all transport instances.
for module_dir in "${MODULE_DIR}"/modules/transport-*; do
  [[ -d "${module_dir}" ]] || continue
  module="$(basename "${module_dir}")"
  script="${module_dir}/uninstall.sh"
  [[ -f "${script}" ]] || continue
  echo "Removing Mihomo dependency: ${module}"
  if ! bash "${script}"; then
    echo "Warning: ${module} cleanup returned an error; checking residual runtime state." >&2
  fi
done

# The capability probe is part of the Mihomo runtime, but is not a transport
# module, so stop it explicitly before checking for residual Mihomo services.
systemctl disable --now "${CAPABILITIES_TIMER}" "${CAPABILITIES_SERVICE}" >/dev/null 2>&1 || true

# Verify that no Mihomo-owned runtime survived the dependency cascade.
for unit in   wg-quick@mh-wg0.service   awg-quick@mh-awg0.service   vps-control-mihomo-reality.service   vps-control-mihomo-ss.target   vps-control-mihomo-hysteria2.service   vps-control-mihomo-tuic.service   "${CAPABILITIES_SERVICE}"; do
  if systemctl is-active --quiet "${unit}" 2>/dev/null; then
    failures+=("active:${unit}")
  fi
done

for interface in mh-wg0 mh-awg0; do
  if ip link show "${interface}" >/dev/null 2>&1; then
    failures+=("interface:${interface}")
  fi
done

while read -r unit; do
  [[ -n "${unit}" ]] || continue
  if systemctl is-active --quiet "${unit}" 2>/dev/null; then
    failures+=("active:${unit}")
  fi
done < <(
  systemctl list-units --all --plain --no-legend 'vps-control-mihomo-ss@*.service' 2>/dev/null     | awk '{print $1}'
)

if ((${#failures[@]})); then
  echo "Cascade removal incomplete. Residual dependencies: ${failures[*]}" >&2
  # Do not report top-level success. Keep Mihomo registered so cleanup can be retried.
  systemctl start "${MANAGER_SERVICE}" >/dev/null 2>&1 || true
  exit 1
fi

systemctl disable --now "${MANAGER_SERVICE}" >/dev/null 2>&1 || true
systemctl disable --now "${CAPABILITIES_TIMER}" "${CAPABILITIES_SERVICE}" >/dev/null 2>&1 || true
rm -f /etc/systemd/system/vps-control-mihomo-manager.service \
  /etc/systemd/system/"${CAPABILITIES_SERVICE}" \
  /etc/systemd/system/"${CAPABILITIES_TIMER}"
rm -rf -- "${CAPABILITIES_HELPER_DIR}"
rm -f -- "${CAPABILITIES_DATA}"
systemctl daemon-reload
VPS_CONTROL_EXCLUDE_COMPONENT=mihomo python3 "${APP_ROOT}/api/cdn_security.py" rebuild

# Profiles, generated credentials, sub-module settings and all Mihomo-only configs.
if [[ "${PRESERVE_COMPONENT_DATA:-1}" == 1 ]]; then
  rm -rf -- "${DATA_DIR}/bin"
else
  rm -rf -- "${CONFIG_DIR}" "${DATA_DIR}"
fi

echo "Исполняемые компоненты Mihomo удалены; сохранение настроек и подключений: ${PRESERVE_COMPONENT_DATA:-1}."
echo "Direct-модули и общие системные пакеты GATE.312 не изменялись."
