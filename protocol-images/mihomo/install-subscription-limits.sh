#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/opt/vps-control/protocol-images/mihomo
PYTHON=/opt/vps-control/venv/bin/python

refresh_limits_service() {
  local unit="$1" previous_pid="$2" current_pid
  systemctl enable "$unit"
  # Protocol units use Wants= plus a validating ExecStartPre below. Restarting
  # accounting must load new code without restarting authenticated connections;
  # nftables continues enforcing their policy while this process is replaced.
  systemctl restart "$unit"
  for _ in $(seq 1 60); do
    current_pid="$(systemctl show "$unit" --property=MainPID --value)"
    if [[ "$current_pid" =~ ^[1-9][0-9]*$ && "$current_pid" != "$previous_pid" ]] \
      && systemctl is-active --quiet "$unit"; then
      return 0
    fi
    sleep .25
  done
  echo "Subscription limits service did not load the updated runtime" >&2
  return 1
}

previous_pid="$(systemctl show vps-control-subscription-limits.service --property=MainPID --value 2>/dev/null || true)"
if ! command -v nft >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends nftables
fi
install -d -m 0700 /var/lib/vps-control/mihomo/subscription-limits
cat >/etc/systemd/system/vps-control-subscription-limits.service <<EOF
[Unit]
Description=312node.net subscription limits and accounting
After=local-fs.target
Before=vps-control-mihomo-manager.service
[Service]
Type=simple
ExecStart=${PYTHON} ${ROOT}/subscription_limits.py watch
ExecStopPost=${PYTHON} ${ROOT}/subscription_limits.py refresh
Restart=on-failure
RestartSec=1
User=root
UMask=0077
NoNewPrivileges=true
ProtectHome=true
[Install]
WantedBy=multi-user.target
EOF

for unit in vps-control-mihomo-manager vps-control-mihomo-reality vps-control-mihomo-tuic vps-control-mihomo-hysteria2 vps-control-mihomo-ss@ wg-quick@mh-wg0 awg-quick@mh-awg0; do
  directory="/etc/systemd/system/${unit}.service.d"
  install -d -m 0755 "$directory"
  cat >"${directory}/20-subscription-limits.conf" <<EOF
[Unit]
Wants=vps-control-subscription-limits.service
After=vps-control-subscription-limits.service
[Service]
ExecStartPre=+${PYTHON} ${ROOT}/subscription_limits.py refresh
EOF
done
# Xray runs under DynamicUser. SO_MARK requires this capability on all
# supported kernels, including Ubuntu 22.04's 5.15 kernel.
cat >>/etc/systemd/system/vps-control-mihomo-reality.service.d/20-subscription-limits.conf <<'EOF'
AmbientCapabilities=CAP_NET_ADMIN
EOF
systemctl daemon-reload
refresh_limits_service vps-control-subscription-limits.service "$previous_pid"
