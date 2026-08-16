#!/usr/bin/env bash
set -Eeuo pipefail
ENV="/etc/vps-control/mihomo/reality/reality.env"
PORT="$(sed -n 's/^PORT=//p' "${ENV}" 2>/dev/null | tail -n1 || true)"
systemctl disable --now vps-control-mihomo-reality.service >/dev/null 2>&1 || true
[[ -z "${PORT}" ]] || { command -v ufw >/dev/null && ufw delete allow "${PORT}/tcp" >/dev/null 2>&1 || true; }
rm -f /etc/systemd/system/vps-control-mihomo-reality.service
rm -rf /etc/vps-control/mihomo/reality /usr/local/lib/vps-control-mihomo-reality
systemctl daemon-reload
echo "Mihomo/Reality удалён; direct Xray/Reality не изменялся."
