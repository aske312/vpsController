#!/usr/bin/env bash
set -Eeuo pipefail
source /etc/os-release
[[ "${ID:-}" == ubuntu || "${ID:-}" == debian ]] || { echo "Unsupported OS" >&2; exit 1; }
core="${MIHOMO_CORE_BIN:-/opt/vps-control/api/bin/mihomo}"
python3 - "$core" <<'PY'
import re, subprocess, sys
version = subprocess.run([sys.argv[1], '-v'], capture_output=True, text=True, check=True).stdout
match = re.search(r'Mihomo Meta v(\d+)\.(\d+)\.(\d+)', version)
if not match or tuple(map(int, match.groups())) < (1, 19, 34):
    raise SystemExit('Snell v6 требует Mihomo 1.19.34+. Сначала обновите ядро Mihomo.')
PY
install -d -m 0700 /etc/vps-control/mihomo/snell /var/lib/vps-control/mihomo/snell
cat >/etc/systemd/system/vps-control-mihomo-snell.target <<'UNIT'
[Unit]
Description=Managed Mihomo Snell v6 listeners
After=network-online.target
Wants=network-online.target
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/vps-control-mihomo-snell@.service <<UNIT
[Unit]
Description=Managed Mihomo Snell v6 listener %i
After=network-online.target
PartOf=vps-control-mihomo-snell.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/vps-control/protocol-images/mihomo/snell_runtime.py serve %i
Restart=on-failure
RestartSec=2
UMask=0077
RuntimeDirectory=vps-control-mihomo-snell/%i
RuntimeDirectoryMode=0700
StateDirectory=vps-control/mihomo/snell/%i
StateDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
LimitNOFILE=65536
[Install]
WantedBy=vps-control-mihomo-snell.target
UNIT
systemctl daemon-reload
systemctl enable --now vps-control-mihomo-snell.target
python3 /opt/vps-control/protocol-images/mihomo/ss_runtime.py restore --snell
