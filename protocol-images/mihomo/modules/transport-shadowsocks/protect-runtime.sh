#!/usr/bin/env bash
set -Eeuo pipefail

# The updater, manager maintenance and component installer may overlap.
exec 9>/run/lock/vps-control-mihomo-ss-protection.lock
flock -x 9

# Shared by component installation and application updates; never rewrite keys,
# ports, profile JSON, or firewall rules during this migration.
MODULE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unit_dir="/etc/systemd/system/vps-control-mihomo-ss@.service.d"
guard="/usr/local/lib/vps-control-mihomo-ss/guard.py"
changed=0
for source in guard.py empty_connections.py; do
  destination="$(dirname -- "${guard}")/${source}"
  if ! cmp -s "${MODULE_DIR}/${source}" "${destination}"; then
    install -D -m 0755 "${MODULE_DIR}/${source}" "${destination}"
    changed=1
  fi
done
install -d -m 0755 "${unit_dir}"
draft="$(mktemp)"
trap 'rm -f -- "${draft}"' EXIT
cat >"${draft}" <<'EOF'
[Unit]
StartLimitIntervalSec=120
StartLimitBurst=5
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-mihomo-ss/guard.py /usr/bin/ss-server -c /etc/vps-control/mihomo/shadowsocks/%i.json
LimitNOFILE=16384:524288
Restart=on-failure
RestartSec=30
KillMode=control-group
TimeoutStopSec=8
Environment=LC_ALL=C
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100
EOF
if ! cmp -s "${draft}" "${unit_dir}/resource-guard.conf"; then
  install -m 0644 "${draft}" "${unit_dir}/resource-guard.conf"
  changed=1
fi
if (( changed )); then
  systemctl daemon-reload
fi
# Installation starts its target afterwards. Updates migrate only running
# instances, leaving intentionally stopped/disabled profiles stopped.
if [[ "${1:-}" == "--restart-active" ]]; then
  units="$(systemctl list-units --type=service --state=running --plain --no-legend 'vps-control-mihomo-ss@*.service')"
  while read -r unit _; do
    [[ -n "${unit}" ]] || continue
    pid="$(systemctl show "${unit}" --property=MainPID --value)"
    # Retry an interrupted first migration even when the files already match.
    if (( changed )) || ! tr '\0' '\n' <"/proc/${pid}/cmdline" | grep -Fxq "${guard}"; then
      systemctl try-restart "${unit}"
    fi
  done <<<"${units}"
fi
