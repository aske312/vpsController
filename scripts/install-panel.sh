#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${VPS_CONTROL_REPOSITORY:-https://github.com/aske312/vpsController}"
BRANCH="${VPS_CONTROL_BRANCH:-stabl}"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR="$(pwd)"
[[ -z "${SCRIPT_PATH}" ]] || SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
BOOTSTRAP_DIR=""
BOOTSTRAP_LOG="/tmp/vps-control-bootstrap.log"

cyan='\033[1;36m'
green='\033[1;32m'
yellow='\033[1;33m'
magenta='\033[1;35m'
red='\033[1;31m'
reset='\033[0m'

banner() {
  printf "${cyan}╭──────────────────────────────────────────────────────────╮${reset}\n"
  printf "${cyan}│${reset}  ${magenta}◆ 312.net${reset}  ${yellow}УСТАНОВКА НА НОВЫЙ СЕРВЕР${reset}               ${cyan}│${reset}\n"
  printf "${cyan}│${reset}  Подготовим систему и запустим панель управления   ${cyan}│${reset}\n"
  printf "${cyan}╰──────────────────────────────────────────────────────────╯${reset}\n"
}

run_stage() {
  local color="$1" label="$2"
  shift 2
  printf "\n${color}●${reset} %s... " "${label}"
  if "$@" >>"${BOOTSTRAP_LOG}" 2>&1; then
    printf "${green}ГОТОВО ✓${reset}\n"
  else
    printf "${red}ОШИБКА ✕${reset}\n" >&2
    printf "${red}Последние сообщения:${reset}\n" >&2
    tail -n 20 "${BOOTSTRAP_LOG}" >&2 || true
    exit 1
  fi
}

cleanup() {
  [[ -z "${BOOTSTRAP_DIR}" || ! -d "${BOOTSTRAP_DIR}" ]] || rm -rf -- "${BOOTSTRAP_DIR}"
}
trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Ошибка: установщик необходимо запустить через sudo или от root.\n' >&2
  exit 1
fi

# В полном клоне этот же файл сразу запускает локальный мастер.
if [[ -x "${SCRIPT_DIR}/vps-control.sh" ]]; then
  exec "${SCRIPT_DIR}/vps-control.sh" install "$@"
fi

# Загруженный отдельно файл служит bootstrap и получает полный stabl-архив.
banner
if [[ ! -r /etc/os-release ]] || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
  printf 'Ошибка: установщик поддерживает Ubuntu Server и Debian.\n' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
: >"${BOOTSTRAP_LOG}"
run_stage "${yellow}" "Проверяем системные репозитории" apt-get -o DPkg::Lock::Timeout=300 update
run_stage "${magenta}" "Добавляем инструменты загрузки" apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl tar

BOOTSTRAP_DIR="$(mktemp -d /tmp/vps-control-bootstrap.XXXXXX)"
archive="${BOOTSTRAP_DIR}/source.tar.gz"
run_stage "${cyan}" "Загружаем 312.net · ${BRANCH}" curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
  --connect-timeout 15 --max-time 300 \
  --output "${archive}" "${REPOSITORY}/archive/refs/heads/${BRANCH}.tar.gz"
run_stage "${yellow}" "Распаковываем приложение" tar -xzf "${archive}" -C "${BOOTSTRAP_DIR}"
source_dir="$(find "${BOOTSTRAP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'vpsController-*' -print -quit)"
[[ -n "${source_dir}" && -x "${source_dir}/scripts/install-panel.sh" ]] \
  || { printf 'Ошибка: загруженный архив не содержит установщик 312.net.\n' >&2; exit 1; }

printf "\n${green}◆ Базовая подготовка завершена.${reset} Запускаем мастер приложения.\n\n"
"${source_dir}/scripts/install-panel.sh" "$@"
