#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
AWG_INTERFACE="${AWG_INTERFACE:-awg0}"
AWG_PORT="${AWG_PORT:-51822}"

env_value() {
  sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1
}

setting() {
  local value
  value="$(env_value "$1")"
  printf '%s' "${value:-$2}"
}

AWG_SUBNET="$(setting AWG_SUBNET 10.73.0.0/24)"
CONFIGURED_AWG_CONFIG="$(setting AWG_CONFIG "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf")"
AWG_CONFIG="/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf"
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
AWG_S3="$(setting AWG_S3 '')"; AWG_S4="$(setting AWG_S4 '')"
AWG_I1="$(setting AWG_I1 '')"; AWG_I2="$(setting AWG_I2 '')"; AWG_I3="$(setting AWG_I3 '')"; AWG_I4="$(setting AWG_I4 '')"; AWG_I5="$(setting AWG_I5 '')"
AWG_HEADER_PROTECTION_KEY="$(setting AWG_HEADER_PROTECTION_KEY '')"
AWG_CONTENT_PADDING_ADDITION="$(setting AWG_CONTENT_PADDING_ADDITION '')"
AWG_REKEY_AFTER_TIME="$(setting AWG_REKEY_AFTER_TIME '')"; AWG_REKEY_TIMEOUT="$(setting AWG_REKEY_TIMEOUT '')"
AWG_REJECT_AFTER_TIME="$(setting AWG_REJECT_AFTER_TIME '')"; AWG_KEEPALIVE_TIMEOUT="$(setting AWG_KEEPALIVE_TIMEOUT '')"
AWG_MAX_HANDSHAKE_ATTEMPTS="$(setting AWG_MAX_HANDSHAKE_ATTEMPTS '')"
AWG_RANDOM_TRAILERS="$(setting AWG_RANDOM_TRAILERS '')"; AWG_DISABLE_COOKIES="$(setting AWG_DISABLE_COOKIES '')"
UPLINK_INTERFACE="$(ip -o -4 route show default | awk '{print $5; exit}')"
WAS_ACTIVE=0
systemctl is-active --quiet "awg-quick@${AWG_INTERFACE}.service" 2>/dev/null && WAS_ACTIVE=1

[[ "${AWG_INTERFACE}" =~ ^[a-zA-Z0-9_.-]{1,15}$ ]] || { echo "Некорректное имя интерфейса" >&2; exit 1; }
[[ "${AWG_PORT}" =~ ^[0-9]+$ && "${AWG_PORT}" -ge 1 && "${AWG_PORT}" -le 65535 ]] || { echo "Некорректный UDP-порт" >&2; exit 1; }
[[ "${AWG_MTU}" =~ ^[0-9]+$ && "${AWG_MTU}" -ge 1280 && "${AWG_MTU}" -le 1420 ]] || { echo "AWG_MTU должен быть от 1280 до 1420" >&2; exit 1; }
validate_range() {
  local value="$1" maximum="$2" label="$3" first second
  [[ -z "${value}" ]] && return 0
  [[ "${value}" =~ ^[0-9]+(-[0-9]+)?$ ]] || { echo "${label} должен иметь формат N или N-M" >&2; exit 1; }
  IFS=- read -r first second <<<"${value}"; second="${second:-${first}}"
  (( 10#${first} <= 10#${second} && 10#${second} <= maximum )) || { echo "${label} содержит недопустимый или обратный диапазон" >&2; exit 1; }
}
for value in "${AWG_S3}" "${AWG_S4}"; do validate_range "${value}" 65535 "AWG S3/S4"; done
for value in "${AWG_CONTENT_PADDING_ADDITION}" "${AWG_REKEY_AFTER_TIME}" "${AWG_REKEY_TIMEOUT}" "${AWG_REJECT_AFTER_TIME}" "${AWG_KEEPALIVE_TIMEOUT}" "${AWG_MAX_HANDSHAKE_ATTEMPTS}"; do validate_range "${value}" 65535 "Диапазон AWG 3.1"; done
for value in "${AWG_RANDOM_TRAILERS}" "${AWG_DISABLE_COOKIES}"; do [[ -z "${value}" || "${value}" == on || "${value}" == off ]] || { echo "Переключатель AWG 3.1 должен быть on или off" >&2; exit 1; }; done
[[ -z "${AWG_HEADER_PROTECTION_KEY}" || "${AWG_HEADER_PROTECTION_KEY}" =~ ^[A-Za-z0-9_+/=-]{43,44}$ ]] || { echo "AWG_HEADER_PROTECTION_KEY должен содержать 32-байтовый base64-ключ" >&2; exit 1; }
if [[ -n "${AWG_HEADER_PROTECTION_KEY}" ]]; then
  [[ -n "${AWG_S3}" && -n "${AWG_S4}" && "${AWG_S1}" -ge 12 && "${AWG_S2}" -ge 12 && "${AWG_S3}" -ge 12 && "${AWG_S4}" -ge 12 ]] || { echo "Header Protection требует S1-S4 не меньше 12" >&2; exit 1; }
fi
for value in "${AWG_I1}" "${AWG_I2}" "${AWG_I3}" "${AWG_I4}" "${AWG_I5}"; do [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || { echo "AWG I1-I5 не должны содержать перевод строки" >&2; exit 1; }; done
[[ -n "${UPLINK_INTERFACE}" ]] || { echo "Не найден внешний сетевой интерфейс" >&2; exit 1; }
ID="" VERSION_ID=""
[[ ! -r /etc/os-release ]] || source /etc/os-release
case "${ID}:${VERSION_ID}" in
  ubuntu:22.04|ubuntu:24.04|debian:13) ;;
  *) echo "Предупреждение: ОС не проверена с AmneziaWG; продолжаем с проверкой зависимостей." >&2 ;;
esac
command -v apt-get >/dev/null && command -v dpkg >/dev/null \
  || { echo "Для установки AmneziaWG необходимы apt-get и dpkg." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
if ! command -v awg >/dev/null 2>&1 || ! command -v awg-quick >/dev/null 2>&1 || ! modinfo amneziawg >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  headers="linux-headers-$(uname -r)"
  if [[ ! -e "/lib/modules/$(uname -r)/build/Makefile" ]] && ! apt-cache show "${headers}" >/dev/null 2>&1; then
    echo "Не найдены ${headers}. Установите заголовки именно запущенного ядра либо обновите ядро и перезагрузите сервер перед повторной установкой." >&2
    exit 1
  fi
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl gnupg kmod "${headers}" iptables
  if [[ "${ID}" == ubuntu ]]; then
    apt-get -o DPkg::Lock::Timeout=300 install -y software-properties-common python3-launchpadlib
    if ! grep -Rqs 'ppa.launchpadcontent.net/amnezia/ppa' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
      add-apt-repository -y ppa:amnezia/ppa
    fi
  else
    # Official Debian installation uses the focal PPA; apt-key is absent on Debian 13.
    key_fingerprint="75C9DD72C799870E310542E24166F2C257290828"
    key_file="$(mktemp)"
    trap 'rm -f -- "${key_file}"' EXIT
    curl --fail --silent --show-error --retry 3 --connect-timeout 15 --max-time 60 \
      "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${key_fingerprint}" >"${key_file}"
    actual_fingerprint="$(gpg --batch --show-keys --with-colons "${key_file}" | awk -F: '$1 == "fpr" {print $10; exit}')"
    [[ "${actual_fingerprint}" == "${key_fingerprint}" ]] || { echo "Не совпадает отпечаток ключа репозитория AmneziaWG." >&2; exit 1; }
    install -d -m 0755 /etc/apt/keyrings
    gpg --batch --yes --dearmor --output /etc/apt/keyrings/vps-control-amneziawg.gpg "${key_file}"
    chmod 0644 /etc/apt/keyrings/vps-control-amneziawg.gpg
    printf '%s\n' \
      'deb [signed-by=/etc/apt/keyrings/vps-control-amneziawg.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main' \
      'deb-src [signed-by=/etc/apt/keyrings/vps-control-amneziawg.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main' \
      >/etc/apt/sources.list.d/vps-control-amneziawg.list
    rm -f -- "${key_file}"
    trap - EXIT
  fi
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y amneziawg
fi

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
  advanced_profile=""
  while IFS='|' read -r name value; do
    [[ -n "${value}" ]] && advanced_profile+="${name} = ${value}"$'\n'
  done <<EOF
S3|${AWG_S3}
S4|${AWG_S4}
I1|${AWG_I1}
I2|${AWG_I2}
I3|${AWG_I3}
I4|${AWG_I4}
I5|${AWG_I5}
HeaderProtectionKey|${AWG_HEADER_PROTECTION_KEY}
ContentPaddingAddition|${AWG_CONTENT_PADDING_ADDITION}
RekeyAfterTime|${AWG_REKEY_AFTER_TIME}
RekeyTimeout|${AWG_REKEY_TIMEOUT}
RejectAfterTime|${AWG_REJECT_AFTER_TIME}
KeepaliveTimeout|${AWG_KEEPALIVE_TIMEOUT}
MaxHandshakeAttempts|${AWG_MAX_HANDSHAKE_ATTEMPTS}
RandomTrailers|${AWG_RANDOM_TRAILERS}
DisableCookies|${AWG_DISABLE_COOKIES}
EOF
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
${advanced_profile}PostUp = iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE
PostDown = while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${AWG_SUBNET} -o ${UPLINK_INTERFACE} -j MASQUERADE; done
EOF
  chmod 0600 "${AWG_CONFIG}"
else
  echo "Существующая конфигурация ${AWG_CONFIG} сохранена."
fi
if [[ "${CONFIGURED_AWG_CONFIG}" != "${AWG_CONFIG}" ]]; then
  ln -sfn -- "${AWG_CONFIG}" "${CONFIGURED_AWG_CONFIG}"
fi
awg-quick strip "${AWG_CONFIG}" >/dev/null || { echo "Установленная версия AmneziaWG не принимает выбранные параметры профиля" >&2; exit 1; }

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
