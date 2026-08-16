#!/usr/bin/env bash
set -Eeuo pipefail
INTERFACE="mh-wg0"
CONFIG="/etc/wireguard/${INTERFACE}.conf"
PORT="$(sed -n 's/^ListenPort[[:space:]]*=[[:space:]]*//p' "${CONFIG}" 2>/dev/null | head -n1 || true)"
systemctl disable --now "wg-quick@${INTERFACE}.service" >/dev/null 2>&1 || true
[[ -z "${PORT}" ]] || { command -v ufw >/dev/null && ufw delete allow "${PORT}/udp" >/dev/null 2>&1 || true; }
rm -f -- "${CONFIG}" /etc/sysctl.d/99-vps-control-mihomo-wg.conf
systemctl daemon-reload
echo "Mihomo/WireGuard удалён; direct wg0 не изменялся."
