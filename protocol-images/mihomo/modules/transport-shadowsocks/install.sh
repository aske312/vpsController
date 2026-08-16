#!/usr/bin/env bash
set -Eeuo pipefail

setting() {
  local key="$1" fallback="$2"
  python3 - "${MIHOMO_SETTINGS_FILE:-}" "${key}" "${fallback}" <<'PY'
import json, sys
path, key, fallback = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, ValueError):
    data = {}
value = data.get(key, fallback) if isinstance(data, dict) else fallback
print(value)
PY
}

PORT_START="$(setting port_start 31000)"
METHOD="$(setting method chacha20-ietf-poly1305)"
[[ "${PORT_START}" =~ ^[0-9]+$ && "${PORT_START}" -ge 1024 && "${PORT_START}" -le 64000 ]] || { echo "Некорректный диапазон портов" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
if ! command -v ss-server >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y shadowsocks-libev
fi
install -d -m 0700 /etc/vps-control/mihomo/shadowsocks

cat >/etc/systemd/system/vps-control-mihomo-ss.target <<'EOF'
[Unit]
Description=GATE.312 Mihomo Shadowsocks instances
Wants=network-online.target
After=network-online.target
[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/vps-control-mihomo-ss@.service <<'EOF'
[Unit]
Description=GATE.312 Mihomo Shadowsocks profile %i
PartOf=vps-control-mihomo-ss.target
After=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/ss-server -c /etc/vps-control/mihomo/shadowsocks/%i.json
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
[Install]
WantedBy=vps-control-mihomo-ss.target
EOF

systemctl daemon-reload
systemctl enable --now vps-control-mihomo-ss.target
echo "Mihomo/Shadowsocks готов. Профили получат отдельные порты от ${PORT_START}; cipher ${METHOD}."
