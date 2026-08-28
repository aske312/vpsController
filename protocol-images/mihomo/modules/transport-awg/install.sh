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
if ! command -v awg >/dev/null 2>&1 || ! command -v awg-quick >/dev/null 2>&1 || ! modinfo amneziawg >/dev/null 2>&1; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y \
    ca-certificates curl dirmngr dkms gnupg iptables
  install_running_kernel_headers

  # Do not use add-apt-repository here. In the GATE installer service /root can
  # be read-only, while launchpadlib tries to create /root/.launchpadlib.
  # Install the verified PPA key and source directly instead.
  AMNEZIA_KEY_FPR="75C9DD72C799870E310542E24166F2C257290828"
  KEYRING="/usr/share/keyrings/amnezia-ppa.gpg"
  SOURCE_LIST="/etc/apt/sources.list.d/amnezia-ppa.list"

  if [[ ! -s "${KEYRING}" ]]; then
    key_home="$(mktemp -d)"
    trap 'rm -rf "${key_home}"' EXIT
    chmod 0700 "${key_home}"
    curl -fsSL --retry 4 --retry-all-errors \
      "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${AMNEZIA_KEY_FPR}" \
      -o "${key_home}/amnezia.asc"
    GNUPGHOME="${key_home}" gpg --batch --import "${key_home}/amnezia.asc" >/dev/null 2>&1
    fingerprint="$(GNUPGHOME="${key_home}" gpg --batch --with-colons --fingerprint "${AMNEZIA_KEY_FPR}" | awk -F: '$1=="fpr"{print $10; exit}')"
    [[ "${fingerprint}" == "${AMNEZIA_KEY_FPR}" ]] || {
      echo "Amnezia signing key fingerprint mismatch" >&2
      exit 1
    }
    GNUPGHOME="${key_home}" gpg --batch --export "${AMNEZIA_KEY_FPR}" >"${KEYRING}"
    chmod 0644 "${KEYRING}"
    rm -rf "${key_home}"
    trap - EXIT
  fi

  if [[ "${ID:-}" == "ubuntu" ]]; then
    repo_suite="${VERSION_CODENAME:-}"
    [[ -n "${repo_suite}" ]] || repo_suite="$(lsb_release -cs 2>/dev/null || true)"
    [[ -n "${repo_suite}" ]] || repo_suite="focal"
  else
    repo_suite="focal"
  fi

  printf 'deb [signed-by=%s] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu %s main\n' \
    "${KEYRING}" "${repo_suite}" >"${SOURCE_LIST}"

  if ! apt-get -o DPkg::Lock::Timeout=300 update; then
    if [[ "${repo_suite}" != "focal" ]]; then
      echo "Amnezia PPA suite ${repo_suite} недоступен, повторяем через focal" >&2
      printf 'deb [signed-by=%s] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main\n' \
        "${KEYRING}" >"${SOURCE_LIST}"
      apt-get -o DPkg::Lock::Timeout=300 update
    else
      exit 1
    fi
  fi

fi

# The meta package can stay unchanged while tools and DKMS receive new builds.
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y --only-upgrade amneziawg amneziawg-tools amneziawg-dkms
for awg_package in amneziawg amneziawg-tools amneziawg-dkms; do
  installed="$(dpkg-query -W -f='${Version}' "${awg_package}" 2>/dev/null || true)"
  candidate="$(LC_ALL=C apt-cache policy "${awg_package}" | awk '/Candidate:/ {print $2; exit}')"
  [[ -n "${installed}" && "${installed}" == "${candidate}" ]] || { echo "${awg_package} не обновлён до актуальной версии репозитория" >&2; exit 1; }
done

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
PostUp = iptables -C INPUT -p udp --dport ${PORT} -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport ${PORT} -j ACCEPT; iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE
PostDown = while iptables -C INPUT -p udp --dport ${PORT} -j ACCEPT 2>/dev/null; do iptables -D INPUT -p udp --dport ${PORT} -j ACCEPT; done; while iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null; do iptables -D FORWARD -i %i -j ACCEPT; done; while iptables -C FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; do iptables -D FORWARD -o %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; done; while iptables -t nat -C POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE 2>/dev/null; do iptables -t nat -D POSTROUTING -s ${SUBNET} -o ${UPLINK} -j MASQUERADE; done
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
sleep 1
systemctl is-active --quiet "awg-quick@${INTERFACE}.service" || {
  systemctl status "awg-quick@${INTERFACE}.service" --no-pager -l >&2 || true
  journalctl -u "awg-quick@${INTERFACE}.service" -n 40 --no-pager >&2 || true
  exit 1
}
awg show "${INTERFACE}" >/dev/null
echo "Mihomo/AmneziaWG: ${INTERFACE}, UDP ${PORT}, ${SUBNET}"
