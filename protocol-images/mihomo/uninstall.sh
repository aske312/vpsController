#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/vps-control"
MODULE_DIR="${APP_ROOT}/protocol-images/mihomo"
DATA_DIR="/var/lib/vps-control/mihomo"
CONFIG_DIR="/etc/vps-control/mihomo"
PROFILE_FILE="${DATA_DIR}/profiles.json"
MANAGER_SERVICE="vps-control-mihomo-manager.service"

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
    if isinstance(profile, dict) and isinstance(profile.get("credentials"), dict):
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
for module in transport-reality transport-shadowsocks transport-awg transport-wg; do
  script="${MODULE_DIR}/modules/${module}/uninstall.sh"
  [[ -f "${script}" ]] || continue
  echo "Removing Mihomo dependency: ${module}"
  if ! bash "${script}"; then
    echo "Warning: ${module} cleanup returned an error; checking residual runtime state." >&2
  fi
done

# Verify that no Mihomo-owned runtime survived the dependency cascade.
for unit in   wg-quick@mh-wg0.service   awg-quick@mh-awg0.service   vps-control-mihomo-reality.service   vps-control-mihomo-ss.target; do
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
rm -f /etc/systemd/system/vps-control-mihomo-manager.service
systemctl daemon-reload

# Profiles, generated credentials, sub-module settings and all Mihomo-only configs.
rm -rf -- "${CONFIG_DIR}" "${DATA_DIR}"

echo "Mihomo удалён каскадно: profiles=${PROFILE_COUNT:-0}, credentials=${CREDENTIAL_COUNT:-0}."
echo "Direct-модули и общие системные пакеты GATE.312 не изменялись."
