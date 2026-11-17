#!/usr/bin/env bash
set -Eeuo pipefail
python3 /opt/vps-control/protocol-images/mihomo/ss_runtime.py capture --snell
systemctl disable --now vps-control-mihomo-snell.target
while read -r unit _; do
  [[ "$unit" =~ ^vps-control-mihomo-snell@[A-Za-z0-9_-]+\.service$ ]] || continue
  systemctl disable --now "$unit"
done < <(systemctl list-unit-files --no-legend 'vps-control-mihomo-snell@*.service')
# Retain private listener files and their enablement for a later reinstall.
systemctl daemon-reload
