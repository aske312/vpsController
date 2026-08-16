#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/vps-control"
MODULE_DIR="${APP_ROOT}/protocol-images/mihomo"
DATA_DIR="/var/lib/vps-control/mihomo"
CONFIG_DIR="/etc/vps-control/mihomo"

systemctl disable --now vps-control-mihomo-manager.service >/dev/null 2>&1 || true

for module in transport-reality transport-shadowsocks transport-awg transport-wg routing-policy dns-private; do
  script="${MODULE_DIR}/modules/${module}/uninstall.sh"
  [[ ! -f "${script}" ]] || bash "${script}" || true
done

rm -f /etc/systemd/system/vps-control-mihomo-manager.service
systemctl daemon-reload
rm -rf -- "${CONFIG_DIR}" "${DATA_DIR}"

echo "Mihomo Manager и его независимые профили/подмодули удалены. Direct-модули GATE.312 не изменялись."
