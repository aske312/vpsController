#!/usr/bin/env bash
set -Eeuo pipefail
systemctl list-unit-files 'vps-control-mihomo-ss@*.service' --no-legend 2>/dev/null \
  | awk '{print $1}' | while read -r unit; do systemctl disable --now "${unit}" >/dev/null 2>&1 || true; done
systemctl disable --now vps-control-mihomo-ss.target >/dev/null 2>&1 || true
rm -f /etc/systemd/system/vps-control-mihomo-ss.target /etc/systemd/system/vps-control-mihomo-ss@.service
rm -rf /etc/vps-control/mihomo/shadowsocks
systemctl daemon-reload
echo "Mihomo/Shadowsocks удалён; direct Shadowsocks не изменялся."
