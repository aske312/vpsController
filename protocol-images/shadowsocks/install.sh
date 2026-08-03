#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"

setting() {
  local key="$1" fallback="$2" value=""
  value="$(sed -n "s/^${key}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')"
  printf '%s' "${value:-${fallback}}"
}

PORT_START="$(setting SHADOWSOCKS_PORT_START 30000)"
[[ "${PORT_START}" =~ ^[0-9]+$ && "${PORT_START}" -ge 1024 && "${PORT_START}" -le 60000 ]] \
  || { echo "SHADOWSOCKS_PORT_START должен быть портом от 1024 до 60000" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y shadowsocks-libev
command -v ss-server >/dev/null 2>&1 || { echo "ss-server не установлен" >&2; exit 1; }

install -d -m 0700 /etc/vps-control/shadowsocks/clients
cat >/etc/systemd/system/vps-control-shadowsocks.target <<'EOF'
[Unit]
Description=312.net managed Shadowsocks instances
Wants=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/vps-control-shadowsocks@.service <<'EOF'
[Unit]
Description=312.net Shadowsocks connection %i
PartOf=vps-control-shadowsocks.target
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ss-server -c /etc/vps-control/shadowsocks/clients/%i.json
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true

[Install]
WantedBy=vps-control-shadowsocks.target
EOF

systemctl daemon-reload
systemctl enable --now vps-control-shadowsocks.target
systemctl is-active --quiet vps-control-shadowsocks.target
echo "Shadowsocks установлен. Подключения и порты создаются панелью индивидуально."
