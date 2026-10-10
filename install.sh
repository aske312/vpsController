#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_SLUG="${VPS_CONTROL_REPOSITORY_SLUG:-aske312/vpsController}"
INSTALLER_BRANCH="${VPS_CONTROL_INSTALLER_BRANCH:-installer}"
RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY_SLUG}"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
[[ -z "${SCRIPT_PATH}" ]] || SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
WORK_DIR=""
EDITION=""
PASSTHROUGH=()

cleanup() {
  [[ -z "${WORK_DIR}" || ! -d "${WORK_DIR}" ]] || rm -rf -- "${WORK_DIR}"
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
312.net installer

Usage:
  sudo bash install.sh
  sudo bash install.sh --edition light
  sudo bash install.sh --edition pro
  bash install.sh --edition agent

Options:
  --edition <light|pro|agent>  install without an interactive prompt
  -h, --help             show this help
EOF
}

while (($#)); do
  case "$1" in
    --edition)
      [[ $# -ge 2 ]] || { printf 'Ошибка: после --edition укажите light, pro или agent.\n' >&2; exit 2; }
      EDITION="${2,,}"
      shift 2
      ;;
    --edition=*) EDITION="${1#*=}"; EDITION="${EDITION,,}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; PASSTHROUGH+=("$@"); break ;;
    *) PASSTHROUGH+=("$1"); shift ;;
  esac
done

[[ ${EUID} -eq 0 ]] || { printf 'Ошибка: запустите установщик через sudo или от root.\n' >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { printf 'Ошибка: для загрузки установщика требуется curl.\n' >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { printf 'Ошибка: для установки требуется tar.\n' >&2; exit 1; }

ID="" VERSION_ID="" PRETTY_NAME=""
if [[ -r /etc/os-release ]]; then
  # shellcheck source=/dev/null
  source /etc/os-release
fi
case "${ID}:${VERSION_ID}" in
  debian:13|ubuntu:22.04|ubuntu:24.04) ;;
  *) printf 'Предупреждение: %s не входит в основную тестовую матрицу. Основная ОС — Debian 13.\n' "${PRETTY_NAME:-Неизвестная ОС}" >&2 ;;
esac

case "$(dpkg --print-architecture 2>/dev/null || uname -m)" in
  amd64|x86_64) ARCHITECTURE="amd64" ;;
  arm64|aarch64) ARCHITECTURE="arm64" ;;
  *) printf 'Ошибка: поддерживаются только amd64 и arm64.\n' >&2; exit 1 ;;
esac

if [[ -z "${EDITION}" ]]; then
  [[ -r /dev/tty ]] || { printf 'Ошибка: без терминала укажите --edition light, pro или agent.\n' >&2; exit 2; }
  printf '\n312.net — выберите вариант установки:\n  1) Light — WireGuard и AmneziaWG\n  2) PRO   — Mihomo, DNS, relay и расширенное управление\n  3) Agent — Relay для изолированной сети без панели\n\nВыбор [1/2/3]: ' >/dev/tty
  IFS= read -r choice </dev/tty
  case "${choice}" in
    1|light|Light) EDITION="light" ;;
    2|pro|PRO|Pro) EDITION="pro" ;;
    3|agent|Agent|AGENT) EDITION="agent" ;;
    *) printf 'Ошибка: неизвестная редакция.\n' >&2; exit 2 ;;
  esac
fi

[[ "${EDITION}" == "light" || "${EDITION}" == "pro" || "${EDITION}" == "agent" ]] \
  || { printf 'Ошибка: вариант установки должен быть light, pro или agent.\n' >&2; exit 2; }

WORK_DIR="$(mktemp -d /tmp/vps-controller-installer.XXXXXX)"
if [[ -n "${SCRIPT_DIR}" && -r "${SCRIPT_DIR}/editions.json" ]]; then
  cp -- "${SCRIPT_DIR}/editions.json" "${WORK_DIR}/editions.json"
else
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --connect-timeout 15 --max-time 60 \
    --output "${WORK_DIR}/editions.json" \
    "${RAW_BASE}/${INSTALLER_BRANCH}/editions.json"
fi

if command -v python3 >/dev/null 2>&1; then
  readarray -t route < <(python3 - "${WORK_DIR}/editions.json" "${EDITION}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
if config.get("schema") != 1:
    raise SystemExit("unsupported editions schema")
edition = config.get("editions", {}).get(sys.argv[2])
if not isinstance(edition, dict):
    raise SystemExit("unknown edition")
print(edition.get("branch", ""))
print(edition.get("installer", ""))
PY
  )
  BRANCH="${route[0]:-}"
  INSTALLER_PATH="${route[1]:-}"
else
  BRANCH="${EDITION}"
  INSTALLER_PATH="scripts/install-panel.sh"
  if [[ "${EDITION}" == "agent" ]]; then BRANCH="${INSTALLER_BRANCH}"; INSTALLER_PATH="scripts/install-agent.sh"; fi
fi

[[ ( "${EDITION}" == "agent" && "${BRANCH}" == "${INSTALLER_BRANCH}" && "${INSTALLER_PATH}" == "scripts/install-agent.sh" ) ||
   ( "${EDITION}" != "agent" && "${BRANCH}" == "${EDITION}" && "${INSTALLER_PATH}" == "scripts/install-panel.sh" ) ]] \
  || { printf 'Ошибка: editions.json содержит недопустимый маршрут установки.\n' >&2; exit 1; }

edition_installer="${WORK_DIR}/install-panel.sh"
printf '312.net: редакция %s, архитектура %s. Загружаем установщик...\n' "${EDITION^^}" "${ARCHITECTURE}"
curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
  --connect-timeout 15 --max-time 120 \
  --output "${edition_installer}" \
  "${RAW_BASE}/${BRANCH}/${INSTALLER_PATH}"
chmod 0755 "${edition_installer}"

VPS_CONTROL_BRANCH="${BRANCH}" \
VPS_CONTROL_REPOSITORY="https://github.com/${REPOSITORY_SLUG}" \
  "${edition_installer}" "${PASSTHROUGH[@]}"
