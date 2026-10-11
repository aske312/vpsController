#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${VPS_CONTROL_REPOSITORY:-https://github.com/aske312/vpsController}"
BRANCH="${VPS_CONTROL_BRANCH:-agent}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR=""
PUBLIC_IP=""
cleanup() { [[ -z "${WORK_DIR}" ]] || rm -rf -- "${WORK_DIR}"; }
trap cleanup EXIT
usage() {
  printf 'Установка 312node.net Relay Agent без панели: bash install-agent.sh [--public-ip IPv4]\n'
}
while (($#)); do
  case "$1" in
    --public-ip)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      PUBLIC_IP="$2"; shift 2 ;;
    --public-ip=*) PUBLIC_IP="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { printf 'Установщик необходимо запустить от root.\n' >&2; exit 1; }
command -v apt-get >/dev/null || { printf 'Требуется ОС с apt-get (Debian/Ubuntu).\n' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
umask 077
if ! command -v python3 >/dev/null || ! command -v openssl >/dev/null || ! command -v curl >/dev/null || ! command -v tar >/dev/null || [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl tar python3 openssl
fi
if [[ -z "${PUBLIC_IP}" && -f /etc/vps-control-relay-agent/config.json ]]; then
  PUBLIC_IP="$(python3 -c 'import json; print(json.load(open("/etc/vps-control-relay-agent/config.json"))["public_ip"])')"
fi
if [[ -z "${PUBLIC_IP}" ]]; then
  PUBLIC_IP="$(curl -4 --fail --silent --show-error --connect-timeout 10 --max-time 30 https://api.ipify.org)"
fi
python3 - "${PUBLIC_IP}" <<'PY'
import ipaddress, sys
try:
    address = ipaddress.ip_address(sys.argv[1])
    if address.version != 4 or not address.is_global: raise ValueError()
except ValueError:
    raise SystemExit('Требуется публичный IPv4. Укажите его через --public-ip IPv4.')
PY
SOURCE="${SCRIPT_DIR}/protocol-images/relay-agent"
[[ -d "${SOURCE}" ]] || SOURCE="${SCRIPT_DIR}/../protocol-images/relay-agent"
if [[ ! -f "${SOURCE}/install.sh" || ! -f "${SOURCE}/agent.py" || ! -f "${SOURCE}/credentials.py" ]]; then
  WORK_DIR="$(mktemp -d /tmp/vps-relay-installer.XXXXXX)"
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --connect-timeout 15 --max-time 180 --output "${WORK_DIR}/source.tar.gz" \
    "${REPOSITORY}/archive/${BRANCH}.tar.gz"
  tar -xzf "${WORK_DIR}/source.tar.gz" -C "${WORK_DIR}"
  candidates=("${WORK_DIR}"/*/protocol-images/relay-agent)
  [[ ${#candidates[@]} -eq 1 && -f "${candidates[0]}/install.sh" && -f "${candidates[0]}/agent.py" && -f "${candidates[0]}/credentials.py" ]] \
    || { printf 'Архив не содержит Relay Agent.\n' >&2; exit 1; }
  SOURCE="${candidates[0]}"
fi
printf 'Устанавливаем 312node.net Relay Agent для %s…\n' "${PUBLIC_IP}"
RELAY_PUBLIC_IP="${PUBLIC_IP}" bash "${SOURCE}/install.sh"
printf '\nДанные для добавления в изолированную сеть (сохраните токен):\n'
python3 /usr/local/lib/vps-control-relay-agent/credentials.py
printf '\nПовторно показать: python3 /usr/local/lib/vps-control-relay-agent/credentials.py\n'
printf 'Состояние службы: systemctl status vps-control-relay-agent\n'
