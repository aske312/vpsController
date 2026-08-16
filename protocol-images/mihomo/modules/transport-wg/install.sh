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

INTERFACE="mh-wg0"
CONFIG="/etc/wireguard/${INTERFACE}.conf"
PORT="$(setting port 51830)"
SUBNET="$(setting subnet 10.82.0.0/24)"
MTU="$(setting mtu 1280)"
UPLINK="$(ip -o -4 route show default | awk '{print $5; exit}')"

[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1024 && "${PORT}" -le 65535 ]] || { echo "Некорректный порт" >&2; exit 1; }
[[ -n "${UPLINK}" ]] || { echo "Не найден uplink" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
if ! command -v wg >/dev/null || ! command -v wg-quick >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y iptables wireguard-tools
fi

install -d -m 0700 /etc/wireguard
SERVER_ADDRESS="$(python3 - "${SUBNET}" <<'PY'
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=True)
print(f"{next(net.hosts())}/{net.prefixlen}")
PY
)"

PRIVATE=""
PEERS=""
if [[ -s "${CONFIG}" ]]; then
  PRIVATE="$(sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' "${CONFIG}" | head -n1)"
  PEERS="$(awk '/^# mihomo-profile:/ {copy=1} copy {print}' "${CONFIG}" || true)"
fi
[[ -n "${PRIVATE}" ]] || PRIVATE="$(wg genkey)"

umask 077
cat >"${CONFIG}" <<EOF
[Interface]
Address = ${SERVER_ADDRESS}
ListenPort = ${PORT}
PrivateKey = ${PRIVATE}
MTU = ${MTU}
PostUp = iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE
PostDown = while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE; done
EOF
[[ -z "${PEERS}" ]] || printf '\n%s\n' "${PEERS}" >>"${CONFIG}"
chmod 0600 "${CONFIG}"

cat >/etc/sysctl.d/99-vps-control-mihomo-wg.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=2
EOF
sysctl --system >/dev/null

if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow "${PORT}/udp" comment 'GATE.312 Mihomo WG' >/dev/null || true
fi

systemctl enable "wg-quick@${INTERFACE}.service" >/dev/null
systemctl restart "wg-quick@${INTERFACE}.service"
systemctl is-active --quiet "wg-quick@${INTERFACE}.service"
echo "Mihomo/WireGuard: ${INTERFACE}, UDP ${PORT}, ${SUBNET}"
