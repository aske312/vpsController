#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${VPS_CONTROL_REPOSITORY:-https://github.com/aske312/vpsController}"
BRANCH="${VPS_CONTROL_BRANCH:-stabl}"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR="$(pwd)"
[[ -z "${SCRIPT_PATH}" ]] || SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
BOOTSTRAP_DIR=""
BOOTSTRAP_LOG="/tmp/vps-control-bootstrap.log"
PACKAGE_MODE="${VPS_CONTROL_PACKAGE_MODE:-auto}"
OS_UPDATE="${VPS_CONTROL_OS_UPDATE:-yes}"

# --manual / --interactive: force dpkg dialogs through /dev/tty.
# --no-apt: never call apt/dpkg; all runtime dependencies must already exist.
# --no-os-update: install the application without upgrading existing OS packages.
while (($#)); do
  case "$1" in
    --manual|--interactive)
      PACKAGE_MODE="interactive"
      ;;
    --no-apt)
      PACKAGE_MODE="skip"
      OS_UPDATE="no"
      ;;
    --no-os-update)
      OS_UPDATE="no"
      ;;
    *)
      break
      ;;
  esac
  shift
done
export VPS_CONTROL_PACKAGE_MODE="${PACKAGE_MODE}"
export VPS_CONTROL_OS_UPDATE="${OS_UPDATE}"

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
  if [[ "${VPS_CONTROL_PREFLIGHT_ONLY:-no}" == "yes" ]]; then
    exec "${SCRIPT_DIR}/vps-control.sh" doctor "$@"
  fi
  exec "${SCRIPT_DIR}/vps-control.sh" install "$@"
fi

# Загруженный отдельно файл служит bootstrap и получает полный stabl-архив.
banner
if [[ ! -r /etc/os-release ]] || ! grep -Eq '^ID=(ubuntu|debian)$' /etc/os-release; then
  printf 'Ошибка: установщик поддерживает Ubuntu Server и Debian.\n' >&2
  exit 1
fi


: >"${BOOTSTRAP_LOG}"

repair_dpkg_interactive() {
  [[ -z "$(dpkg --audit 2>/dev/null)" ]] && return 0
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    printf "${red}Ошибка: dpkg требует ручной настройки, но интерактивный терминал недоступен.${reset}\n" >&2
    printf "Откройте SSH/VNC и повторите установку с параметром --manual.\n" >&2
    return 1
  fi
  printf "\n${yellow}● dpkg содержит незавершённые пакеты.${reset}\n"
  printf "${yellow}  Открываю ручную настройку. Если GRUB спросит диск — выбирайте весь диск (например /dev/vda), не раздел /dev/vda1.${reset}\n\n"
  DEBIAN_FRONTEND=dialog dpkg --configure -a </dev/tty >/dev/tty 2>&1
}

if [[ "${PACKAGE_MODE}" == "interactive" ]]; then
  repair_dpkg_interactive || exit 1
fi

if [[ "${PACKAGE_MODE}" != "skip" ]]; then
  # The curl bootstrap should touch apt only when bootstrap tools are missing.
  # The full OS update is performed by vps-control after the source archive is loaded,
  # where broken dpkg/grub-pc can safely fall back to /dev/tty.
  missing_bootstrap=()
  command -v curl >/dev/null 2>&1 || missing_bootstrap+=(curl ca-certificates)
  command -v tar >/dev/null 2>&1 || missing_bootstrap+=(tar)
  if ((${#missing_bootstrap[@]})); then
    if [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
      repair_dpkg_interactive || exit 1
    fi
    export DEBIAN_FRONTEND=noninteractive
    run_stage "${yellow}" "Проверяем системные репозитории" apt-get -o DPkg::Lock::Timeout=300 update
    run_stage "${magenta}" "Добавляем инструменты загрузки" apt-get -o DPkg::Lock::Timeout=300 install -y "${missing_bootstrap[@]}"
  else
    printf "\n${green}● Инструменты bootstrap уже установлены:${reset} curl, tar.\n"
  fi
else
  for required in curl tar; do
    command -v "${required}" >/dev/null 2>&1 || {
      printf "Ошибка: режим --no-apt требует заранее установленную команду %s.\n" "${required}" >&2
      exit 1
    }
  done
  printf "\n${yellow}● Режим --no-apt:${reset} системные пакеты не изменяются.\n"
fi

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
if [[ -z "${VPS_CONTROL_ADMIN_PASSWORD:-}" ]]; then
  export VPS_CONTROL_RANDOM_ADMIN_PASSWORD=yes
fi
"${source_dir}/scripts/install-panel.sh" "$@"

if command -v vps-control >/dev/null 2>&1; then
  printf "\n${cyan}◆${reset} Проверяем последний подготовленный релиз stabl.\n"
  if ! vps-control update; then
    printf "${yellow}Предупреждение:${reset} stabl-latest пока недоступен; исходная установка продолжает работать. Повторите vps-control update позднее.\n" >&2
  fi
fi
