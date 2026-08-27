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
  apt-get -o DPkg::Lock::Timeout=300 install -y iptables shadowsocks-libev
fi
command -v iptables >/dev/null 2>&1 || apt-get -o DPkg::Lock::Timeout=300 install -y iptables
install -d -m 0700 /etc/vps-control/mihomo/shadowsocks
cat >/usr/local/sbin/vps-control-mihomo-ss-firewall <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"
profile_id="${2:-}"
[[ "${profile_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || exit 2
config="/etc/vps-control/mihomo/shadowsocks/${profile_id}.json"
port="$(python3 - "${config}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    port = int(json.load(source)["server_port"])
if not 1024 <= port <= 65535:
    raise SystemExit(2)
print(port)
PY
)"
for protocol in tcp udp; do
  if [[ "${action}" == "add" ]]; then
    iptables -C INPUT -p "${protocol}" --dport "${port}" -j ACCEPT 2>/dev/null \
      || iptables -I INPUT 1 -p "${protocol}" --dport "${port}" -j ACCEPT
  elif [[ "${action}" == "delete" ]]; then
    while iptables -C INPUT -p "${protocol}" --dport "${port}" -j ACCEPT 2>/dev/null; do
      iptables -D INPUT -p "${protocol}" --dport "${port}" -j ACCEPT
    done
  else
    exit 2
  fi
done
EOF
chmod 0755 /usr/local/sbin/vps-control-mihomo-ss-firewall

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
ExecStartPre=/usr/local/sbin/vps-control-mihomo-ss-firewall add %i
ExecStart=/usr/bin/ss-server -c /etc/vps-control/mihomo/shadowsocks/%i.json
ExecStopPost=/usr/local/sbin/vps-control-mihomo-ss-firewall delete %i
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
[Install]
WantedBy=vps-control-mihomo-ss.target
EOF

systemctl daemon-reload
systemctl enable vps-control-mihomo-ss.target >/dev/null
systemctl restart vps-control-mihomo-ss.target
echo "Mihomo/Shadowsocks готов. Профили получат отдельные порты от ${PORT_START}; cipher ${METHOD}."
