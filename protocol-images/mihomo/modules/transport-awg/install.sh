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

INTERFACE="mh-awg0"
CONFIG="/etc/amnezia/amneziawg/${INTERFACE}.conf"
QUICK_CONFIG="/etc/amnezia/${INTERFACE}.conf"
PORT="$(setting port 51831)"
SUBNET="$(setting subnet 10.83.0.0/24)"
MTU="$(setting mtu 1280)"
JC="$(setting jc 6)"
JMIN="$(setting jmin 8)"
JMAX="$(setting jmax 80)"
S1="$(setting s1 64)"
S2="$(setting s2 112)"
H1="$(setting h1 150000000)"
H2="$(setting h2 600000000)"
H3="$(setting h3 1000000000)"
H4="$(setting h4 1400000000)"
UPLINK="$(ip -o -4 route show default | awk '{print $5; exit}')"

[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1024 && "${PORT}" -le 65535 ]] || { echo "Некорректный порт" >&2; exit 1; }
[[ -n "${UPLINK}" ]] || { echo "Не найден uplink" >&2; exit 1; }

source /etc/os-release
export DEBIAN_FRONTEND=noninteractive
if ! command -v awg >/dev/null 2>&1 || ! command -v awg-quick >/dev/null 2>&1 || ! modinfo amneziawg >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  if [[ "${ID:-}" == "ubuntu" ]]; then
    apt-get -o DPkg::Lock::Timeout=300 install -y software-properties-common python3-launchpadlib gnupg2 "linux-headers-$(uname -r)" iptables
    grep -Rqs 'ppa.launchpadcontent.net/amnezia/ppa' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null       || add-apt-repository -y ppa:amnezia/ppa
  else
    apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl dirmngr dkms gnupg iptables "linux-headers-$(uname -r)"
    if [[ ! -s /usr/share/keyrings/amnezia-ppa.gpg || ! -s /etc/apt/sources.list.d/amnezia-ppa.list ]]; then
      key_home="$(mktemp -d)"
      chmod 0700 "${key_home}"
      gpg --homedir "${key_home}" --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys 75C9DD72C799870E310542E24166F2C257290828
      fingerprint="$(gpg --homedir "${key_home}" --batch --with-colons --fingerprint 75C9DD72C799870E310542E24166F2C257290828 | grep '^fpr:' | head -n1 | cut -d: -f10)"
      [[ "${fingerprint}" == "75C9DD72C799870E310542E24166F2C257290828" ]] || { rm -rf "${key_home}"; echo "Amnezia signing key mismatch" >&2; exit 1; }
      gpg --homedir "${key_home}" --batch --export 75C9DD72C799870E310542E24166F2C257290828 >/usr/share/keyrings/amnezia-ppa.gpg
      rm -rf "${key_home}"
      printf '%s\n' 'deb [signed-by=/usr/share/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main' >/etc/apt/sources.list.d/amnezia-ppa.list
    fi
  fi
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y amneziawg
fi

modprobe amneziawg
install -d -m 0700 /etc/amnezia/amneziawg /etc/amnezia
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
[[ -n "${PRIVATE}" ]] || PRIVATE="$(awg genkey)"

umask 077
cat >"${CONFIG}" <<EOF
[Interface]
Address = ${SERVER_ADDRESS}
ListenPort = ${PORT}
PrivateKey = ${PRIVATE}
MTU = ${MTU}
Jc = ${JC}
Jmin = ${JMIN}
Jmax = ${JMAX}
S1 = ${S1}
S2 = ${S2}
H1 = ${H1}
H2 = ${H2}
H3 = ${H3}
H4 = ${H4}
PostUp = iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE
PostDown = while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE; done
EOF
[[ -z "${PEERS}" ]] || printf '\n%s\n' "${PEERS}" >>"${CONFIG}"
chmod 0600 "${CONFIG}"
ln -sfn "${CONFIG}" "${QUICK_CONFIG}"

cat >/etc/sysctl.d/99-vps-control-mihomo-awg.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=2
EOF
sysctl --system >/dev/null

if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow "${PORT}/udp" comment 'GATE.312 Mihomo AWG' >/dev/null || true
fi

systemctl enable "awg-quick@${INTERFACE}.service" >/dev/null
systemctl restart "awg-quick@${INTERFACE}.service"
systemctl is-active --quiet "awg-quick@${INTERFACE}.service"
echo "Mihomo/AmneziaWG: ${INTERFACE}, UDP ${PORT}, ${SUBNET}"
