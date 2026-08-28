#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
AWG_INTERFACE="${AWG_INTERFACE:-awg0}"
AWG_PORT="${AWG_PORT:-51822}"

env_value() {
  local value
  value="$(sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | tr -d '\r')"
  if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s\n' "${value}"
}

setting() {
  local value
  value="$(env_value "$1")"
  printf '%s' "${value:-$2}"
}

AWG_SUBNET="$(setting AWG_SUBNET 10.73.0.0/24)"
CONFIGURED_AWG_CONFIG="$(setting AWG_CONFIG "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf")"
AWG_CONFIG="/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf"
QUICK_CONFIG="/etc/amnezia/${AWG_INTERFACE}.conf"
AWG_MTU="$(setting AWG_MTU 1280)"
AWG_JC="$(setting AWG_JC 6)"
AWG_JMIN="$(setting AWG_JMIN 8)"
AWG_JMAX="$(setting AWG_JMAX 80)"
AWG_S1="$(setting AWG_S1 64)"
AWG_S2="$(setting AWG_S2 112)"
AWG_H1="$(setting AWG_H1 150000000)"
AWG_H2="$(setting AWG_H2 600000000)"
AWG_H3="$(setting AWG_H3 1000000000)"
AWG_H4="$(setting AWG_H4 1400000000)"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"
WAS_ACTIVE=0
systemctl is-active --quiet "awg-quick@${AWG_INTERFACE}.service" 2>/dev/null && WAS_ACTIVE=1

[[ "${AWG_INTERFACE}" =~ ^[a-zA-Z0-9_.-]{1,15}$ ]] || { echo "Некорректное имя интерфейса" >&2; exit 1; }
[[ "${AWG_PORT}" =~ ^[0-9]+$ && "${AWG_PORT}" -ge 1 && "${AWG_PORT}" -le 65535 ]] || { echo "Некорректный UDP-порт" >&2; exit 1; }
[[ "${AWG_MTU}" =~ ^[0-9]+$ && "${AWG_MTU}" -ge 1280 && "${AWG_MTU}" -le 1420 ]] || { echo "AWG_MTU должен быть от 1280 до 1420" >&2; exit 1; }
[[ -n "${UPLINK_INTERFACE}" ]] || { echo "Не найден внешний сетевой интерфейс" >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == "ubuntu" || "${ID:-}" == "debian" ]] || { echo "AmneziaWG supports Ubuntu and Debian" >&2; exit 1; }

install_running_kernel_headers() {
  local running_kernel header_package next_kernel image_meta headers_meta architecture
  running_kernel="$(uname -r)"
  header_package="linux-headers-${running_kernel}"
  if [[ -d "/lib/modules/${running_kernel}/build" ]]; then
    return
  fi
  if apt-cache show "${header_package}" 2>/dev/null | grep -q '^Package:'; then
    apt-get -o DPkg::Lock::Timeout=300 install -y "${header_package}"
    return
  fi

  echo "Headers for the running kernel ${running_kernel} are no longer available." >&2
  next_kernel="$(basename "$(readlink -f /vmlinuz 2>/dev/null || true)" | sed 's/^vmlinuz-//')"
  if [[ -n "${next_kernel}" && "${next_kernel}" != "${running_kernel}" && -d "/lib/modules/${next_kernel}/build" ]]; then
    echo "Kernel ${next_kernel} and its headers are ready. Reboot the VPS once, then retry AmneziaWG installation." >&2
    exit 75
  fi
  if [[ "${ID}" == "ubuntu" ]]; then
    apt-get -o DPkg::Lock::Timeout=300 install -y linux-generic linux-headers-generic
  else
    architecture="$(dpkg --print-architecture)"
    case "${running_kernel}" in
      *-cloud-${architecture})
        image_meta="linux-image-cloud-${architecture}"
        headers_meta="linux-headers-cloud-${architecture}"
        ;;
      *)
        image_meta="linux-image-${architecture}"
        headers_meta="linux-headers-${architecture}"
        ;;
    esac
    apt-get -o DPkg::Lock::Timeout=300 install -y "${image_meta}" "${headers_meta}"
  fi
  next_kernel="$(basename "$(readlink -f /vmlinuz 2>/dev/null || true)" | sed 's/^vmlinuz-//')"
  echo "Kernel ${next_kernel:-new} and its headers are installed. Reboot the VPS once, then retry AmneziaWG installation." >&2
  exit 75
}

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
if [[ "${ID}" == "ubuntu" ]]; then
  apt-get -o DPkg::Lock::Timeout=300 install -y software-properties-common python3-launchpadlib gnupg2 iptables
  install_running_kernel_headers
  grep -Rqs 'ppa.launchpadcontent.net/amnezia/ppa' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null \
    || add-apt-repository -y ppa:amnezia/ppa
else
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl dirmngr dkms gnupg iptables
  install_running_kernel_headers
  if [[ ! -s /usr/share/keyrings/amnezia-ppa.gpg || ! -s /etc/apt/sources.list.d/amnezia-ppa.list ]]; then
    key_home="$(mktemp -d)"
    chmod 0700 "${key_home}"
    gpg --homedir "${key_home}" --batch --keyserver hkps://keyserver.ubuntu.com \
      --recv-keys 75C9DD72C799870E310542E24166F2C257290828
    fingerprint="$(gpg --homedir "${key_home}" --batch --with-colons --fingerprint \
      75C9DD72C799870E310542E24166F2C257290828 | grep '^fpr:' | head -n 1 | cut -d: -f10)"
    [[ "${fingerprint}" == "75C9DD72C799870E310542E24166F2C257290828" ]] \
      || { rm -rf -- "${key_home}"; echo "Amnezia PPA signing key fingerprint mismatch" >&2; exit 1; }
    gpg --homedir "${key_home}" --batch --export 75C9DD72C799870E310542E24166F2C257290828 \
      >/usr/share/keyrings/amnezia-ppa.gpg
    rm -rf -- "${key_home}"
    printf '%s\n' 'deb [signed-by=/usr/share/keyrings/amnezia-ppa.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main' \
      >/etc/apt/sources.list.d/amnezia-ppa.list
  fi
fi

apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y --only-upgrade amneziawg amneziawg-tools amneziawg-dkms
for awg_package in amneziawg amneziawg-tools amneziawg-dkms; do
  awg_installed_package="$(dpkg-query -W -f='${Version}' "${awg_package}" 2>/dev/null || true)"
  awg_candidate_package="$(LC_ALL=C apt-cache policy "${awg_package}" | awk '/Candidate:/ {print $2; exit}')"
  [[ -n "${awg_installed_package}" && "${awg_installed_package}" == "${awg_candidate_package}" ]] \
    || { echo "${awg_package} не обновлён до актуальной версии репозитория" >&2; exit 1; }
done

command -v awg >/dev/null || { echo "Пакет не установил awg" >&2; exit 1; }
command -v awg-quick >/dev/null || { echo "Пакет не установил awg-quick" >&2; exit 1; }
modprobe amneziawg
modinfo amneziawg >/dev/null

install -d -m 0700 "$(dirname -- "${AWG_CONFIG}")" "$(dirname -- "${CONFIGURED_AWG_CONFIG}")"
if [[ "${CONFIGURED_AWG_CONFIG}" != "${AWG_CONFIG}" && -s "${CONFIGURED_AWG_CONFIG}" && ! -e "${AWG_CONFIG}" ]]; then
  mv -- "${CONFIGURED_AWG_CONFIG}" "${AWG_CONFIG}"
fi
if [[ ! -s "${AWG_CONFIG}" ]]; then
  SERVER_ADDRESS="$(python3 - "${AWG_SUBNET}" <<'PY'
import ipaddress
import sys
network = ipaddress.ip_network(sys.argv[1])
print(f"{next(network.hosts())}/{network.prefixlen}")
PY
)"
  SERVER_PRIVATE_KEY="$(awg genkey)"
  umask 077
  cat >"${AWG_CONFIG}" <<EOF
[Interface]
Address = ${SERVER_ADDRESS}
ListenPort = ${AWG_PORT}
PrivateKey = ${SERVER_PRIVATE_KEY}
MTU = ${AWG_MTU}
Jc = ${AWG_JC}
Jmin = ${AWG_JMIN}
Jmax = ${AWG_JMAX}
S1 = ${AWG_S1}
S2 = ${AWG_S2}
H1 = ${AWG_H1}
H2 = ${AWG_H2}
H3 = ${AWG_H3}
H4 = ${AWG_H4}
PostUp = iptables -C INPUT -p udp --dport ${AWG_PORT} -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport ${AWG_PORT} -j ACCEPT; iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE
PostDown = while iptables -C INPUT -p udp --dport ${AWG_PORT} -j ACCEPT 2>/dev/null; do iptables -D INPUT -p udp --dport ${AWG_PORT} -j ACCEPT; done; while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE; done
EOF
  chmod 0600 "${AWG_CONFIG}"
else
  echo "Существующая конфигурация ${AWG_CONFIG} сохранена."
fi
if [[ "${CONFIGURED_AWG_CONFIG}" != "${AWG_CONFIG}" ]]; then
  ln -sfn -- "${AWG_CONFIG}" "${CONFIGURED_AWG_CONFIG}"
fi
if [[ "${QUICK_CONFIG}" != "${AWG_CONFIG}" && "${QUICK_CONFIG}" != "${CONFIGURED_AWG_CONFIG}" ]]; then
  ln -sfn -- "${AWG_CONFIG}" "${QUICK_CONFIG}"
fi

cat >/etc/sysctl.d/99-vps-control-amneziawg.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=2
net.ipv4.conf.default.rp_filter=2
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.conf.all.accept_source_route=0
net.ipv4.conf.default.accept_source_route=0
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.core.netdev_max_backlog=250000
EOF
sysctl --system >/dev/null

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow "${AWG_PORT}/udp"
  ufw route allow in on "${AWG_INTERFACE}" out on "${UPLINK_INTERFACE}" from "${AWG_SUBNET}"
fi

systemctl enable --now "awg-quick@${AWG_INTERFACE}.service"
systemctl is-active --quiet "awg-quick@${AWG_INTERFACE}.service"
if [[ "${WAS_ACTIVE}" -eq 0 ]]; then
  rm -f -- /var/lib/vps-control/monitor/awg.csv /var/lib/vps-control/monitor/awg.state
fi
awg show "${AWG_INTERFACE}"
