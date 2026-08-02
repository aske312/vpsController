#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
WG_INTERFACE="${WG_INTERFACE:-wg0}"
WG_PORT="${WG_PORT:-51820}"

env_value() {
  sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1
}

WG_SUBNET="$(env_value WG_SUBNET)"
WG_SUBNET="${WG_SUBNET:-10.72.0.0/24}"
WG_CONFIG="$(env_value WG_CONFIG)"
WG_CONFIG="${WG_CONFIG:-/etc/wireguard/${WG_INTERFACE}.conf}"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"

[[ "${WG_INTERFACE}" =~ ^[a-zA-Z0-9_.-]{1,15}$ ]] || { echo "Некорректное имя интерфейса" >&2; exit 1; }
[[ "${WG_PORT}" =~ ^[0-9]+$ ]] || { echo "Некорректный порт" >&2; exit 1; }
[[ -n "${UPLINK_INTERFACE}" ]] || { echo "Не найден внешний сетевой интерфейс" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y iptables wireguard-tools
fi

install -d -m 0700 "$(dirname -- "${WG_CONFIG}")"
if [[ ! -s "${WG_CONFIG}" ]]; then
  SERVER_ADDRESS="$(python3 - "${WG_SUBNET}" <<'PY'
import ipaddress
import sys
network = ipaddress.ip_network(sys.argv[1])
print(f"{next(network.hosts())}/{network.prefixlen}")
PY
)"
  SERVER_PRIVATE_KEY="$(wg genkey)"
  umask 077
  cat >"${WG_CONFIG}" <<EOF
[Interface]
Address = ${SERVER_ADDRESS}
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIVATE_KEY}
PostUp = iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${WG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${WG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE
PostDown = while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${WG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${WG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE; done
EOF
  chmod 0600 "${WG_CONFIG}"
else
  echo "Существующая конфигурация ${WG_CONFIG} сохранена."
fi

cat >/etc/sysctl.d/99-vps-control-wireguard.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=2
net.ipv4.conf.default.rp_filter=2
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.conf.all.accept_source_route=0
net.ipv4.conf.default.accept_source_route=0
EOF
sysctl --system >/dev/null

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow "${WG_PORT}/udp"
  ufw route allow in on "${WG_INTERFACE}" out on "${UPLINK_INTERFACE}" from "${WG_SUBNET}"
fi

systemctl enable --now "wg-quick@${WG_INTERFACE}.service"
wg show "${WG_INTERFACE}"
