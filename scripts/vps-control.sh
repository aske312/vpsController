#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="vps-control"
INSTALL_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
TEST_BACKUP_DIR="${DATA_DIR}/test-app-backup"
CONFIG_DIR="/etc/${APP_NAME}"
LEGACY_ENV_FILE="/etc/${APP_NAME}.env"
ENV_FILE="${CONFIG_DIR}/environment"
SSH_ACCESS_DROPIN="/etc/ssh/sshd_config.d/00-vps-control-admin-access.conf"
LEGACY_SSH_ACCESS_DROPIN="/etc/ssh/sshd_config.d/98-vps-control-admin-access.conf"
MANAGER_CONFIG="/etc/${APP_NAME}-manager.conf"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}-api.service"
WEB_SERVICE_FILE="/etc/systemd/system/${APP_NAME}-web.service"
CADDY_CONFIG="/etc/caddy/Caddyfile"
CADDY_SNIPPET_DIR="/etc/caddy/vps-control.d"
COMMAND_PATH="/usr/local/sbin/${APP_NAME}"
INSTALL_CONFIG="/etc/${APP_NAME}-install.conf"
PANEL_URL=""
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR="$(pwd)"
[[ -z "${SCRIPT_PATH}" ]] || SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
if [[ "${SCRIPT_DIR}" == "/usr/local/sbin" && -d "${INSTALL_DIR}" ]]; then
  PROJECT_DIR="${INSTALL_DIR}"
fi

ACCESS_MODE="external"
ADMIN_USER="admin"
ADMIN_PASSWORD="VpsAdmin-2026-7Qm!rK2#"
LOCAL_ADDRESS=""
LOCAL_CIDR=""
HTTP_PORT="8080"
INTERNAL_PANEL_HOST="admin.312.net"
WG_PORT="51820"
AWG_PORT="51822"
SHADOWSOCKS_PORT_START="30000"
VLESS_REALITY_PORT="8443"
VLESS_REALITY_TARGET="www.intel.com:443"
VLESS_CDN_DOMAIN=""
VLESS_CDN_PORT="10087"
VLESS_TLS_DOMAIN=""
VLESS_TLS_PORT="10088"
WG_INTERFACE="wg0"
AWG_INTERFACE="awg0"
WG_MTU="1280"
AWG_MTU="1280"
WG_DNS="1.1.1.1, 1.0.0.1"
AWG_DNS="1.1.1.1, 1.0.0.1"
WG_KEEPALIVE="25"
AWG_KEEPALIVE="25"
AWG_JC="6"
AWG_JMIN="8"
AWG_JMAX="80"
AWG_S1="64"
AWG_S2="112"
AWG_H1="150000000"
AWG_H2="600000000"
AWG_H3="1000000000"
AWG_H4="1400000000"
ENABLE_UFW="yes"
GEOLOCATION_PRIMARY_URL="https://api.2ip.io"
GEOLOCATION_FALLBACK_URL="https://ipwho.is/?fields=success,ip,city,country,country_code,latitude,longitude"
GEOLOCATION_TERTIARY_URL="https://ip.guide"
GEOLOCATION_QUATERNARY_URL="https://ipapi.co"
GEOLOCATION_QUINARY_URL="https://free.freeipapi.com/api/json"
GEOLOCATION_SENARY_URL="https://ipinfo.io"
PUBLIC_IP_DISCOVERY_URL="https://api64.ipify.org"
UPDATE_TEMP_DIR=""
UPDATE_ROLLBACK_DIR=""
UPDATE_SWAP_ACTIVE="no"
SSH_TEMP_STARTED="no"
SSH_TEMP_RULE="no"
APP_VERSION="v1.0.0"
BUILD_COMMIT="unknown"
PRESERVE_MANAGER="no"
PRODUCTION_BRANCH="stabl"
ACTION_FILE="${DATA_DIR}/application-action.json"
AUTOMATION_FILE="${DATA_DIR}/automation.json"
SERVICE_MODE_FILE="${DATA_DIR}/service-mode.json"
CURRENT_ACTION=""
ACTION_STARTED_AT=""
ACTION_PROGRESS=0
REBOOT_AFTER_UPDATE="no"
INSTALL_LOG="/var/log/vps-control-install.log"
MAIN_RELEASE_WAIT_ATTEMPTS=18
STABL_RELEASE_WAIT_ATTEMPTS=30
UPDATE_DOWNLOAD_TIMEOUT=300
DEPENDENCY_INSTALL_TIMEOUT=300
PACKAGE_MODE="${VPS_CONTROL_PACKAGE_MODE:-auto}"
OS_UPDATE="${VPS_CONTROL_OS_UPDATE:-yes}"
case "${OS_UPDATE}" in
  yes|no) ;;
  *) OS_UPDATE="yes" ;;
esac
case "${PACKAGE_MODE}" in
  auto|interactive|skip) ;;
  *) PACKAGE_MODE="auto" ;;
esac

UI_STEP=0
UI_TOTAL=0

ui_rule() {
  printf '\033[1;36m%s\033[0m\n' '┌──────────────────────────────────────────────────────────────┐'
}

ui_header() {
  clear 2>/dev/null || true
  ui_rule
  printf '\033[1;36m│\033[0m  \033[1m%-58s\033[0m \033[1;36m│\033[0m\n' "312.net — установка панели управления сервером"
  printf '\033[1;36m│\033[0m  %-58s \033[1;36m│\033[0m\n' "Безопасное развёртывание и проверка компонентов"
  printf '\033[1;36m%s\033[0m\n' '└──────────────────────────────────────────────────────────────┘'
}

ui_stage() {
  UI_STEP=$((UI_STEP + 1))
  local percent=$(( (UI_STEP - 1) * 100 / UI_TOTAL ))
  local filled=$(( percent / 5 ))
  local empty=$(( 20 - filled ))
  local colors=(36 35 34 33 32) color="${colors[$(( (UI_STEP - 1) % ${#colors[@]} ))]}"
  printf '\n\033[1;%sm● [%02d/%02d | %3d%%]\033[0m \033[1m%s\033[0m\n' "${color}" "${UI_STEP}" "${UI_TOTAL}" "${percent}" "$1"
  printf '\033[1;%sm%s\033[0m\033[2m%s\033[0m\n' "${color}" "$(printf '━%.0s' $(seq 1 "${filled}"))" "$(printf '·%.0s' $(seq 1 "${empty}"))"
}

ui_done() {
  printf '\033[1;32m  ✓ ГОТОВО\033[0m \033[2m%s\033[0m\n' "$1"
}

ui_summary() {
  printf '\n\033[1;32m%s\033[0m\n' '╔══════════════════════════════════════════════════════════════╗'
  printf '\033[1;32m║\033[0m  \033[1m%-58s\033[0m \033[1;32m║\033[0m\n' "ПАНЕЛЬ УСПЕШНО УСТАНОВЛЕНА"
  printf '\033[1;32m%s\033[0m\n' '╚══════════════════════════════════════════════════════════════╝'
}

run_with_status() {
  local label="$1"
  shift
  local started_at="$(date +%s)" pid frame=0 status=0
  "$@" >>"${INSTALL_LOG}" 2>&1 &
  pid="$!"
  while kill -0 "${pid}" 2>/dev/null; do
    frame=$(( (frame + 1) % 4 ))
    local elapsed=$(( $(date +%s) - started_at ))
    printf '\r\033[1;36m│\033[0m  %s %-34s %3ss' "$(printf '⠋⠙⠹⠸' | cut -c $((frame + 1)))" "${label}" "${elapsed}"
    sleep 1
  done
  wait "${pid}" || status=$?
  printf '\r\033[K'
  if (( status != 0 )); then
    printf '\033[1;31m│  ✖ ОШИБКА\033[0m %s\n' "${label}" >&2
    tail -n 25 "${INSTALL_LOG}" >&2
    return "${status}"
  fi
  printf '\033[1;32m│  ✔ ГОТОВО\033[0m %s\n' "${label}"
}

write_action_status() {
  [[ -n "${CURRENT_ACTION}" ]] || return 0
  local state="$1" progress="$2" message="$3"
  install -d -m 0750 "${DATA_DIR}"
  python3 - "${ACTION_FILE}" "${CURRENT_ACTION}" "${state}" "${progress}" "${message}" "${ACTION_STARTED_AT}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
previous = {}
try:
    previous = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    pass
payload = {
    "action": sys.argv[2],
    "state": sys.argv[3],
    "progress": int(sys.argv[4]),
    "message": sys.argv[5],
    "started_at": sys.argv[6],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
if previous.get("action") == payload["action"] and previous.get("unit"):
    payload["unit"] = previous["unit"]
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
os.chmod(tmp, 0o600)
tmp.replace(path)
PY
}

begin_operation() {
  CURRENT_ACTION="$1"
  ACTION_STARTED_AT="$(date --iso-8601=seconds)"
  ACTION_PROGRESS=3
  write_action_status "running" "${ACTION_PROGRESS}" "Команда принята"
}

finish_operation() {
  local exit_code="$1"
  [[ -n "${CURRENT_ACTION}" ]] || return 0
  if (( exit_code == 0 )); then
    case "${CURRENT_ACTION}" in
      reboot) write_action_status "rebooting" 100 "Сервер перезагружается" ;;
      poweroff) write_action_status "powering-off" 100 "Сервер выключается" ;;
      kernel-update)
        if [[ "${REBOOT_AFTER_UPDATE}" == "yes" ]]; then
          write_action_status "rebooting" 100 "Kernel updated; server is rebooting"
          systemctl --no-block --no-wall reboot
        else
          write_action_status "succeeded" 100 "Kernel is already up to date"
        fi
        ;;
      *) write_action_status "succeeded" 100 "Операция завершена" ;;
    esac
  else
    if [[ "${CURRENT_ACTION}" == "test-update" || "${CURRENT_ACTION}" == "update" ]]; then
      write_action_status "failed" "${ACTION_PROGRESS}" "Обновление остановлено по ошибке или таймауту; рабочая версия восстановлена"
    else
      write_action_status "failed" "${ACTION_PROGRESS}" "Операция завершилась с ошибкой"
    fi
  fi
}

handle_exit() {
  local exit_code="$?"
  rollback_interrupted_update || warn "Emergency rollback encountered an additional error."
  cleanup_update_dir
  restore_update_ssh
  finish_operation "${exit_code}"
  return "${exit_code}"
}

info() {
  printf '\033[1;34m==>\033[0m %s\n' "$*"
  if [[ -n "${CURRENT_ACTION}" ]]; then
    ACTION_PROGRESS=$((ACTION_PROGRESS < 88 ? ACTION_PROGRESS + 9 : 92))
    write_action_status "running" "${ACTION_PROGRESS}" "$*"
  fi
}
ok() {
  printf '\033[1;32m✓\033[0m %s\n' "$*"
  if [[ -n "${CURRENT_ACTION}" ]]; then
    ACTION_PROGRESS=$((ACTION_PROGRESS < 88 ? ACTION_PROGRESS + 5 : 92))
    write_action_status "running" "${ACTION_PROGRESS}" "$*"
  fi
}
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

cleanup_update_dir() {
  [[ -n "${UPDATE_TEMP_DIR}" ]] || return 0
  local target base leaf
  target="$(realpath -m -- "${UPDATE_TEMP_DIR}")"
  base="$(realpath -m -- "${DATA_DIR}/tmp")"
  leaf="${target##*/}"
  [[ "${target}" == "${base}"/* && "${leaf}" =~ ^(update|test-update)\.[A-Za-z0-9]+$ ]] \
    || die "отказ от очистки неожиданного пути ${target}."
  rm -rf -- "${target}"
  UPDATE_TEMP_DIR=""
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "запустите команду через sudo."
}

load_manager_config() {
  if [[ -r "${MANAGER_CONFIG}" ]]; then
    # This file is created by this script and contains only quoted Git settings.
    # shellcheck source=/dev/null
    source "${MANAGER_CONFIG}"
    # Мигрируем старый GitHub SSH-origin на HTTPS: обновлениям не нужен Git SSH-ключ.
    if [[ "${REMOTE_URL:-}" =~ ^git@github\.com:(.+)$ ]]; then
      REMOTE_URL="https://github.com/${BASH_REMATCH[1]}"
      printf 'REMOTE_URL=%q\nUPDATE_BRANCH=%q\n' "${REMOTE_URL}" "${UPDATE_BRANCH:-${PRODUCTION_BRANCH}}" >"${MANAGER_CONFIG}"
      chmod 0600 "${MANAGER_CONFIG}"
    elif [[ "${REMOTE_URL:-}" =~ ^ssh://git@github\.com/(.+)$ ]]; then
      REMOTE_URL="https://github.com/${BASH_REMATCH[1]}"
      printf 'REMOTE_URL=%q\nUPDATE_BRANCH=%q\n' "${REMOTE_URL}" "${UPDATE_BRANCH:-${PRODUCTION_BRANCH}}" >"${MANAGER_CONFIG}"
      chmod 0600 "${MANAGER_CONFIG}"
    fi
    if [[ -z "${REMOTE_URL:-}" && -n "${SOURCE_DIR:-}" ]]; then
      REMOTE_URL="$(git -C "${SOURCE_DIR}" remote get-url origin 2>/dev/null || true)"
      UPDATE_BRANCH="$(git -C "${SOURCE_DIR}" branch --show-current 2>/dev/null || true)"
      if [[ -n "${REMOTE_URL}" && -n "${UPDATE_BRANCH}" ]]; then
        {
          printf 'REMOTE_URL=%q\n' "${REMOTE_URL}"
          printf 'UPDATE_BRANCH=%q\n' "${UPDATE_BRANCH}"
        } >"${MANAGER_CONFIG}"
        chmod 0600 "${MANAGER_CONFIG}"
      fi
    fi
  fi
}

load_install_config() {
  local config="${INSTALL_CONFIG}"
  local admin_user_override="${VPS_CONTROL_ADMIN_USER:-}"
  local admin_password_override="${VPS_CONTROL_ADMIN_PASSWORD:-}"
  local domain_override="${VPS_CONTROL_PUBLIC_DOMAIN:-}"
  local access_override="${VPS_CONTROL_ACCESS_MODE:-}"
  local http_port_override="${VPS_CONTROL_HTTP_PORT:-}"
  local vless_port_override="${VPS_CONTROL_VLESS_PORT:-}"
  local server_city_override="${VPS_CONTROL_SERVER_CITY:-}"
  local server_country_override="${VPS_CONTROL_SERVER_COUNTRY:-}"
  local server_country_code_override="${VPS_CONTROL_SERVER_COUNTRY_CODE:-}"
  local fresh_install="no"
  [[ -r "${INSTALL_CONFIG}" ]] || fresh_install="yes"
  [[ -r "${config}" ]] || config="${PROJECT_DIR}/install.conf"
  if [[ -r "${config}" ]]; then
    # Конфиг принадлежит администратору и содержит только shell-переменные.
    # shellcheck source=/dev/null
    source "${config}"
  fi
  # Releases preserve /etc/vps-control-install.conf. Upgrade the legacy ipwho
  # field list in memory so city clustering also works after an application update.
  if [[ "${GEOLOCATION_FALLBACK_URL}" == "https://ipwho.is/?fields=success,ip,city,country,country_code" ]]; then
    GEOLOCATION_FALLBACK_URL="https://ipwho.is/?fields=success,ip,city,country,country_code,latitude,longitude"
  fi
  if [[ "${GEOLOCATION_SENARY_URL}" == "https://iplocation.info" ]]; then
    GEOLOCATION_SENARY_URL="https://ipinfo.io"
  fi
  [[ -z "${admin_user_override}" ]] || ADMIN_USER="${admin_user_override}"
  if [[ -n "${admin_password_override}" ]]; then
    ADMIN_PASSWORD="${admin_password_override}"
  elif [[ "${fresh_install}" == "yes" && "${VPS_CONTROL_RANDOM_ADMIN_PASSWORD:-no}" == "yes" ]]; then
    ADMIN_PASSWORD="$(od -An -N18 -tx1 /dev/urandom | tr -d ' \n')"
  fi
  [[ -z "${domain_override}" ]] || PUBLIC_DOMAIN="${domain_override}"
  [[ -z "${access_override}" ]] || ACCESS_MODE="${access_override}"
  [[ -z "${http_port_override}" ]] || HTTP_PORT="${http_port_override}"
  [[ -z "${vless_port_override}" ]] || VLESS_REALITY_PORT="${vless_port_override}"
  [[ -z "${server_city_override}" ]] || SERVER_CITY_OVERRIDE="${server_city_override}"
  [[ -z "${server_country_override}" ]] || SERVER_COUNTRY_OVERRIDE="${server_country_override}"
  [[ -z "${server_country_code_override}" ]] || SERVER_COUNTRY_CODE_OVERRIDE="${server_country_code_override^^}"
  [[ "${ACCESS_MODE}" == "external" || "${ACCESS_MODE}" == "local" || "${ACCESS_MODE}" == "vpn" ]] \
    || die "ACCESS_MODE должен быть external, local или vpn."
  [[ "${HTTP_PORT}" =~ ^[0-9]+$ ]] || die "HTTP_PORT должен быть числом."
  [[ "${VLESS_REALITY_PORT}" =~ ^[0-9]+$ ]] || die "VLESS_REALITY_PORT must be numeric."
  (( HTTP_PORT >= 1 && HTTP_PORT <= 65535 )) || die "HTTP_PORT must be between 1 and 65535."
  (( VLESS_REALITY_PORT >= 1 && VLESS_REALITY_PORT <= 65535 )) || die "VLESS_REALITY_PORT must be between 1 and 65535."
  [[ "${VLESS_CDN_PORT}" =~ ^[0-9]+$ ]] || die "VLESS_CDN_PORT must be numeric."
  (( VLESS_CDN_PORT >= 1024 && VLESS_CDN_PORT <= 65535 )) || die "VLESS_CDN_PORT must be between 1024 and 65535."
  [[ "${VLESS_REALITY_PORT}" != "443" ]] || die "TCP 443 is reserved for the HTTPS panel; choose another VLESS port."
  [[ "${VLESS_CDN_PORT}" != "${VLESS_REALITY_PORT}" ]] || die "VLESS_CDN_PORT and VLESS_REALITY_PORT must differ."
  [[ -z "${VLESS_CDN_DOMAIN}" || "${VLESS_CDN_DOMAIN}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] \
    || die "VLESS_CDN_DOMAIN must be a hostname without scheme, path or port."
  [[ -z "${VLESS_TLS_DOMAIN}" || "${VLESS_TLS_DOMAIN}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || die "VLESS_TLS_DOMAIN must be a hostname."
  [[ "${VLESS_TLS_PORT}" =~ ^[0-9]+$ && "${VLESS_TLS_PORT}" -ge 1024 && "${VLESS_TLS_PORT}" -le 65535 ]] || die "VLESS_TLS_PORT must be between 1024 and 65535."
  [[ -z "${SERVER_COUNTRY_CODE_OVERRIDE:-}" || "${SERVER_COUNTRY_CODE_OVERRIDE}" =~ ^[A-Z]{2}$ ]] \
    || die "SERVER_COUNTRY_CODE_OVERRIDE должен содержать две латинские буквы."
}

configured_panel_channel_count() {
  python3 - "${DATA_DIR}/clients.json" "${DATA_DIR}/mihomo/profiles.json" <<'PY'
import json, sys
count = 0
for path in sys.argv[1:]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if path.endswith("clients.json") and isinstance(data, list):
        count += sum(1 for item in data if isinstance(item, dict) and item.get("protocol"))
    elif isinstance(data, list):
        count += sum(len(item.get("connections", [])) for item in data if isinstance(item, dict))
print(count)
PY
}

configure_internal_panel_host() {
  local hosts_tmp
  INTERNAL_PANEL_HOST="admin.312.net"
  set_env_value "INTERNAL_PANEL_HOST" "${INTERNAL_PANEL_HOST}"
  hosts_tmp="$(mktemp)"
  awk '$0 !~ /# 312.net internal panel$/ { print }' /etc/hosts >"${hosts_tmp}"
  printf '127.0.0.1 %s # 312.net internal panel\n' "${INTERNAL_PANEL_HOST}" >>"${hosts_tmp}"
  install -m 0644 "${hosts_tmp}" /etc/hosts
  rm -f -- "${hosts_tmp}"
}

detect_local_network() {
  if [[ -z "${LOCAL_ADDRESS}" ]]; then
    LOCAL_ADDRESS="$(ip -o -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')"
  fi
  [[ -n "${LOCAL_ADDRESS}" ]] || die "не удалось определить локальный IPv4; задайте LOCAL_ADDRESS в install.conf."
  if [[ -z "${LOCAL_CIDR}" ]]; then
    LOCAL_CIDR="$(ip -o -4 route show scope link | awk -v ip="${LOCAL_ADDRESS}" '$0 ~ ip {print $1; exit}')"
  fi
  [[ -n "${LOCAL_CIDR}" ]] || die "не удалось определить локальную сеть; задайте LOCAL_CIDR в install.conf."
}

configure_access() {
  local public_ip
  detect_public_endpoints
  public_ip="$(env_value PUBLIC_ENDPOINT)"
  [[ -n "${public_ip}" ]] || public_ip="$(env_value PUBLIC_IP)"
  configure_internal_panel_host
  if [[ "${ACCESS_MODE}" == "local" ]]; then
    detect_local_network
    set_env_value "PANEL_HOST" "${LOCAL_ADDRESS}"
    set_env_value "CORS_ORIGINS" "http://${LOCAL_ADDRESS}:${HTTP_PORT}"
    PANEL_URL="http://${LOCAL_ADDRESS}:${HTTP_PORT}"
  elif [[ "${ACCESS_MODE}" == "vpn" ]]; then
    local wg_address awg_address openvpn_address openvpn_interface ike_pool vpn_origin
    wg_address="$({ ip -o -4 addr show dev "${WG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    awg_address="$({ ip -o -4 addr show dev "${AWG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    openvpn_interface="$(ip -o -4 route show 10.74.0.0/24 2>/dev/null | awk 'NR==1 {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"
    openvpn_address="$({ [[ -z "${openvpn_interface}" ]] || ip -o -4 addr show dev "${openvpn_interface}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    ike_pool="$(python3 -c 'import json; print(json.load(open("/etc/vps-control/ikev2/settings.json")).get("pool",""))' 2>/dev/null || true)"
    [[ "$(configured_panel_channel_count)" -gt 0 ]] || die "Сначала настройте хотя бы одно защищённое подключение."
    local vpn_origins="http://${INTERNAL_PANEL_HOST}"
    for vpn_origin in ${wg_address:+http://${wg_address}:${HTTP_PORT}} ${awg_address:+http://${awg_address}:${HTTP_PORT}} ${openvpn_address:+http://${openvpn_address}:${HTTP_PORT}}; do
      [[ -z "${vpn_origins}" ]] || vpn_origins+=","
      vpn_origins+="${vpn_origin}"
    done
    if [[ -n "${ike_pool}" ]]; then
      [[ -z "${vpn_origins}" ]] || vpn_origins+=","
      [[ -n "$(env_value PUBLIC_DOMAIN)" ]] && vpn_origins+="https://$(env_value PUBLIC_DOMAIN)" || vpn_origins+="http://${public_ip}:${HTTP_PORT}"
    fi
    set_env_value "PANEL_HOST" "0.0.0.0"
    set_env_value "CORS_ORIGINS" "${vpn_origins}"
    PANEL_URL="http://${INTERNAL_PANEL_HOST}"
  else
    local wg_address awg_address origins
    wg_address="$({ ip -o -4 addr show dev "${WG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    awg_address="$({ ip -o -4 addr show dev "${AWG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    if [[ -n "$(env_value PUBLIC_DOMAIN)" ]]; then
      origins="https://${public_ip}"
      PANEL_URL="https://${public_ip}"
    else
      origins="http://${public_ip}:${HTTP_PORT}"
      PANEL_URL="http://${public_ip}:${HTTP_PORT}"
    fi
    [[ -z "${wg_address}" ]] || origins+=",http://${wg_address}:${HTTP_PORT}"
    [[ -z "${awg_address}" ]] || origins+=",http://${awg_address}:${HTTP_PORT}"
    set_env_value "PANEL_HOST" "0.0.0.0"
    set_env_value "CORS_ORIGINS" "${origins}"
  fi
  set_env_value "HTTP_PORT" "${HTTP_PORT}"
  set_env_value "ACCESS_MODE" "${ACCESS_MODE}"
  set_env_value "WG_PORT" "${WG_PORT}"
  set_env_value "AWG_PORT" "${AWG_PORT}"
  set_env_value "SHADOWSOCKS_PORT_START" "${SHADOWSOCKS_PORT_START}"
  set_env_value "VLESS_REALITY_PORT" "${VLESS_REALITY_PORT}"
  set_env_value "VLESS_REALITY_TARGET" "${VLESS_REALITY_TARGET}"
  set_env_value "VLESS_CDN_DOMAIN" "${VLESS_CDN_DOMAIN}"
  set_env_value "VLESS_CDN_PORT" "${VLESS_CDN_PORT}"
  set_env_value "VLESS_TLS_DOMAIN" "${VLESS_TLS_DOMAIN}"
  set_env_value "VLESS_TLS_PORT" "${VLESS_TLS_PORT}"
  set_env_value "WG_INTERFACE" "${WG_INTERFACE}"
  set_env_value "AWG_INTERFACE" "${AWG_INTERFACE}"
  set_env_value "WG_MTU" "${WG_MTU}"
  set_env_value "AWG_MTU" "${AWG_MTU}"
  set_env_value "WG_DNS" "${WG_DNS}"
  set_env_value "AWG_DNS" "${AWG_DNS}"
  set_env_value "WG_KEEPALIVE" "${WG_KEEPALIVE}"
  set_env_value "AWG_KEEPALIVE" "${AWG_KEEPALIVE}"
  set_env_value "AWG_JC" "${AWG_JC}"
  set_env_value "AWG_JMIN" "${AWG_JMIN}"
  set_env_value "AWG_JMAX" "${AWG_JMAX}"
  set_env_value "AWG_S1" "${AWG_S1}"
  set_env_value "AWG_S2" "${AWG_S2}"
  set_env_value "AWG_H1" "${AWG_H1}"
  set_env_value "AWG_H2" "${AWG_H2}"
  set_env_value "AWG_H3" "${AWG_H3}"
  set_env_value "AWG_H4" "${AWG_H4}"
}

detect_public_endpoints() {
  local public_ipv4 public_ipv6 domain configured_domain endpoint ip_endpoint domain_mode
  public_ipv4="$(env_value PUBLIC_IPV4)"
  [[ -n "${public_ipv4}" ]] || public_ipv4="$(env_value PUBLIC_IP)"
  public_ipv6="$(curl -6 --fail --silent --show-error --max-time 8 https://api64.ipify.org 2>/dev/null || true)"
  [[ "${public_ipv6}" == *:* ]] || public_ipv6=""
  configured_domain="${PUBLIC_DOMAIN:-$(env_value PUBLIC_DOMAIN)}"
  domain="${configured_domain}"
  if [[ -z "${domain}" && -n "${public_ipv4}" ]]; then
    domain="$(python3 - "${public_ipv4}" <<'PY' 2>/dev/null || true
import socket, sys
try:
    name = socket.gethostbyaddr(sys.argv[1])[0].rstrip(".")
    if "." in name:
        print(name)
except (OSError, IndexError):
    pass
PY
)"
  fi
  domain_mode="none"
  if [[ -n "${domain}" ]]; then
    domain_mode="$(python3 - "${domain}" "${public_ipv4}" "${public_ipv6}" <<'PY' 2>/dev/null || true
import ipaddress, socket, sys
name, ipv4, ipv6 = sys.argv[1:]
expected = {value for value in (ipv4, ipv6) if value}
try:
    resolved = {str(ipaddress.ip_address(item[4][0])) for item in socket.getaddrinfo(name, None)}
except OSError:
    raise SystemExit(1)
print("direct" if expected & resolved else "cdn")
PY
    )"
    if [[ "${domain_mode}" != "direct" && "${domain_mode}" != "cdn" ]]; then
      warn "domain ${domain} does not resolve yet; keeping the direct IP endpoint."
      domain=""
      domain_mode="none"
    fi
  fi
  if [[ -n "${public_ipv4}" ]]; then
    ip_endpoint="${public_ipv4}"
  elif [[ -n "${public_ipv6}" ]]; then
    ip_endpoint="[${public_ipv6}]"
  else
    die "unable to detect a public IPv4 or IPv6 address."
  fi
  if [[ -n "${domain}" ]]; then
    endpoint="${domain}"
  else
    endpoint="${ip_endpoint}"
  fi
  set_env_value "PUBLIC_IPV4" "${public_ipv4}"
  set_env_value "PUBLIC_IPV6" "${public_ipv6}"
  set_env_value "PUBLIC_DOMAIN" "${domain}"
  set_env_value "PUBLIC_ENDPOINT" "${endpoint}"
  set_env_value "PUBLIC_IP_ENDPOINT" "${ip_endpoint}"
  set_env_value "PUBLIC_DOMAIN_ENDPOINT" "${domain}"
  set_env_value "PUBLIC_ENDPOINTS" "${ip_endpoint}${domain:+,${domain}}"
  set_env_value "DOMAIN_ROUTE_MODE" "${domain_mode}"
  [[ -z "${public_ipv4}" ]] || set_env_value "PUBLIC_IP" "${public_ipv4}"
  ok "endpoints: direct=${ip_endpoint}; domain=${domain:-none}; domain mode=${domain_mode}; IPv4=${public_ipv4:-none}; IPv6=${public_ipv6:-none}."
}

write_caddy_config() {
  local domain internal_panel_host wg_panel_address awg_panel_address
  domain="$(env_value PUBLIC_DOMAIN)"
  internal_panel_host="admin.312.net"
  wg_panel_address="$(python3 - "$(env_value WG_SUBNET)" <<'PY'
import ipaddress
import sys
print(next(ipaddress.ip_network(sys.argv[1] or "10.72.0.0/24").hosts()))
PY
)"
  awg_panel_address="$(python3 - "$(env_value AWG_SUBNET)" <<'PY'
import ipaddress
import sys
print(next(ipaddress.ip_network(sys.argv[1] or "10.73.0.0/24").hosts()))
PY
)"
  install -d -m 0755 "${CADDY_SNIPPET_DIR}"
  if [[ -n "${domain}" && "${ACCESS_MODE}" == "external" ]]; then
    sed -e "s|{\$SITE_ADDRESS}|${domain}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  elif [[ "${ACCESS_MODE}" == "vpn" ]]; then
    # Keep TCP 80/443 available to protocol-specific Caddy hosts (for example
    # VLESS CDN), but never attach the panel to a public catch-all listener.
    sed -e "s|{\$SITE_ADDRESS}|http://localhost:${HTTP_PORT}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  else
    sed -e "s|{\$SITE_ADDRESS}|:${HTTP_PORT}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  fi
  if [[ "${ACCESS_MODE}" == "vpn" && -n "${domain}" ]]; then
    cat >>"${CADDY_CONFIG}" <<EOF

# Public, token-protected Mihomo subscription refresh. No panel UI or
# administrative API is exposed on this host while VPN-only mode is active.
${domain} {
    handle /api/mihomo/subscriptions/* {
        reverse_proxy 127.0.0.1:8791
    }
    respond 404
}
EOF
  fi
}

env_value() {
  local value
  value="$(sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | tr -d '\r')"
  if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s\n' "${value}"
}

set_env_value() {
  local key="$1" value="$2" encoded escaped
  # The file is sourced by vpn-monitor and also consumed as a systemd
  # EnvironmentFile. Always quote and escape shell metacharacters: passwords
  # commonly contain '$', '&' or backticks and must never be expanded by bash.
  encoded="${value}"
  encoded="${encoded//\\/\\\\}"
  encoded="${encoded//\"/\\\"}"
  encoded="${encoded//\$/\\\$}"
  encoded="${encoded//\`/\\\`}"
  encoded="\"${encoded}\""
  escaped="${encoded//\\/\\\\}"
  escaped="${escaped//&/\\&}"
  escaped="${escaped//|/\\|}"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${encoded}" >>"${ENV_FILE}"
  fi
}

set_config_value() {
  local file="$1" key="$2" value="$3"
  [[ -f "${file}" ]] || die "не найден файл настроек ${file}."
  if grep -q "^${key}=" "${file}"; then
    sed -i "s|^${key}=.*|${key}=\"${value}\"|" "${file}"
  else
    printf '%s="%s"\n' "${key}" "${value}" >>"${file}"
  fi
}

refresh_server_identity() {
  local geo_file="${DATA_DIR}/tmp/geolocation"
  local public_ip city country country_code override_city override_country override_country_code
  info "Определение публичного IP и локации"
  install -d -m 0750 "${DATA_DIR}/tmp"
  rm -f -- "${geo_file}.primary.json" "${geo_file}.fallback.json" "${geo_file}.tertiary.json" \
    "${geo_file}.quaternary.json" "${geo_file}.quinary.json" "${geo_file}.senary.json"
  curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 \
    "${GEOLOCATION_PRIMARY_URL}" >"${geo_file}.primary.json" || rm -f -- "${geo_file}.primary.json"
  curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 \
    "${GEOLOCATION_FALLBACK_URL}" >"${geo_file}.fallback.json" || rm -f -- "${geo_file}.fallback.json"
  public_ip="$(python3 - "${geo_file}.primary.json" "${geo_file}.fallback.json" <<'PY'
import json
import sys
for path in sys.argv[1:]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    value = str(data.get("ip") or "")
    if value:
        print(value)
        break
PY
)"
  if [[ ! "${public_ip}" =~ ^[0-9a-fA-F:.]+$ ]]; then
    public_ip="$(curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 \
      "${PUBLIC_IP_DISCOVERY_URL}" 2>/dev/null || true)"
  fi
  if [[ -n "${public_ip}" ]]; then
    curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 \
      "${GEOLOCATION_TERTIARY_URL}/${public_ip}" >"${geo_file}.tertiary.json" \
      || rm -f -- "${geo_file}.tertiary.json"
    curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_QUATERNARY_URL}/${public_ip}/json/" >"${geo_file}.quaternary.json" \
      || rm -f -- "${geo_file}.quaternary.json"
    curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_QUINARY_URL}/${public_ip}" >"${geo_file}.quinary.json" \
      || rm -f -- "${geo_file}.quinary.json"
    curl -4 --fail --silent --show-error --retry 2 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_SENARY_URL}/${public_ip}/json" >"${geo_file}.senary.json" \
      || rm -f -- "${geo_file}.senary.json"
  fi
  if python3 - /run/cloud-init/instance-data.json \
    "${geo_file}.primary.json" "${geo_file}.fallback.json" "${geo_file}.tertiary.json" \
    "${geo_file}.quaternary.json" "${geo_file}.quinary.json" "${geo_file}.senary.json" \
    >"${geo_file}.result" <<'PY'
from collections import Counter
import json
import math
import re
import sys

MAX_CITY_CLUSTER_KM = 50.0
MAJOR_CITY_ANCHORS = (
    ("NL", "Amsterdam", 52.3676, 4.9041, 55),
    ("DE", "Frankfurt", 50.1109, 8.6821, 65), ("DE", "Berlin", 52.5200, 13.4050, 55),
    ("FR", "Paris", 48.8566, 2.3522, 65), ("GB", "London", 51.5074, -0.1278, 70),
    ("FI", "Helsinki", 60.1699, 24.9384, 55), ("SE", "Stockholm", 59.3293, 18.0686, 55),
    ("NO", "Oslo", 59.9139, 10.7522, 55), ("DK", "Copenhagen", 55.6761, 12.5683, 55),
    ("PL", "Warsaw", 52.2297, 21.0122, 55), ("CZ", "Prague", 50.0755, 14.4378, 50),
    ("AT", "Vienna", 48.2082, 16.3738, 55), ("CH", "Zurich", 47.3769, 8.5417, 50),
    ("LV", "Riga", 56.9496, 24.1052, 50), ("LT", "Vilnius", 54.6872, 25.2797, 50),
    ("EE", "Tallinn", 59.4370, 24.7536, 50), ("RO", "Bucharest", 44.4268, 26.1025, 55),
    ("BG", "Sofia", 42.6977, 23.3219, 50), ("ES", "Madrid", 40.4168, -3.7038, 65),
    ("ES", "Barcelona", 41.3874, 2.1686, 55), ("IT", "Milan", 45.4642, 9.1900, 60),
    ("US", "Ashburn", 39.0438, -77.4874, 70), ("US", "New York", 40.7128, -74.0060, 70),
    ("US", "Chicago", 41.8781, -87.6298, 70), ("US", "Dallas", 32.7767, -96.7970, 80),
    ("US", "Los Angeles", 34.0522, -118.2437, 80), ("US", "Miami", 25.7617, -80.1918, 70),
    ("CA", "Toronto", 43.6532, -79.3832, 70), ("CA", "Montreal", 45.5019, -73.5674, 65),
    ("CA", "Vancouver", 49.2827, -123.1207, 65), ("SG", "Singapore", 1.3521, 103.8198, 55),
    ("JP", "Tokyo", 35.6762, 139.6503, 70), ("HK", "Hong Kong", 22.3193, 114.1694, 55),
)

def coordinates(location):
    try:
        if location.get("loc"):
            latitude, longitude = str(location["loc"]).split(",", 1)
            return float(latitude), float(longitude)
        return float(location.get("latitude") or location.get("lat")), float(location.get("longitude") or location.get("lon"))
    except (TypeError, ValueError):
        return None

def distance_km(first, second):
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))

def nearest_major_city(code, points):
    if not points:
        return "Unknown"
    center = (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
    candidates = []
    for anchor_code, city, latitude, longitude, radius in MAJOR_CITY_ANCHORS:
        if anchor_code != code:
            continue
        distance = distance_km(center, (latitude, longitude))
        if distance <= radius:
            candidates.append((distance, city))
    return min(candidates)[1] if candidates else "Unknown"

def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def metadata_location(value):
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        city = lowered.get("city")
        country = lowered.get("country")
        code = lowered.get("country_code") or lowered.get("countrycode")
        if city and country:
            return str(city), str(country), str(code or "").upper()
        for child in value.values():
            found = metadata_location(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = metadata_location(child)
            if found:
                return found
    return None

metadata = load(sys.argv[1])
confirmed = metadata_location(metadata) if metadata else None
records = []
for path in sys.argv[2:]:
    data = load(path)
    if not data or data.get("success") is False:
        continue
    location = data.get("location") if isinstance(data.get("location"), dict) else data
    network = data.get("network") if isinstance(data.get("network"), dict) else {}
    autonomous = network.get("autonomous_system") if isinstance(network.get("autonomous_system"), dict) else {}
    ip = str(data.get("ip") or data.get("ipAddress") or data.get("ip_address") or data.get("query") or "")
    city = str(location.get("city") or location.get("cityName") or location.get("city_name") or "").strip()
    country = str(location.get("country_name") or location.get("countryName") or location.get("country") or "").strip()
    raw_country = str(location.get("country") or "").strip()
    code = str(location.get("country_code") or location.get("countryCode") or location.get("country_code2") or data.get("country_code") or data.get("code") or autonomous.get("country") or (raw_country if re.fullmatch(r"[A-Za-z]{2}", raw_country) else "")).upper().strip()
    if ip and re.fullmatch(r"[A-Z]{2}", code):
        records.append((ip, city, country, code, coordinates(location)))

if not records:
    raise SystemExit(1)
ip = Counter(item[0] for item in records).most_common(1)[0][0]
if confirmed:
    city, country, code = confirmed
else:
    code_votes = Counter(item[3] for item in records if re.fullmatch(r"[A-Z]{2}", item[3]))
    code, votes = code_votes.most_common(1)[0] if code_votes else ("", 0)
    country_quorum = max(2, len(records) // 2 + 1)
    if votes < country_quorum:
        city, country, code = "Unknown", "Unknown", ""
    else:
        matching = [item for item in records if item[3] == code]
        country = next((item[2] for item in matching if item[2]), code)
        city_votes = Counter(item[1].casefold() for item in matching if item[1])
        city_key, city_count = city_votes.most_common(1)[0] if city_votes else ("", 0)
        city_quorum = max(2, len(matching) // 2 + 1)
        if city_count >= city_quorum:
            city = next((item[1] for item in matching if item[1].casefold() == city_key), "Unknown")
        else:
            clustered = set()
            for left in range(len(matching)):
                for right in range(left + 1, len(matching)):
                    if matching[left][4] and matching[right][4] and distance_km(matching[left][4], matching[right][4]) <= MAX_CITY_CLUSTER_KM:
                        clustered.update((left, right))
            cluster_points = [item[4] for index, item in enumerate(matching) if index in clustered and item[4]]
            city = nearest_major_city(code, cluster_points)

for value in (ip, city, country, code):
    print(value)
PY
  then
    readarray -t geo <"${geo_file}.result"
    public_ip="${geo[0]:-}"
    city="${geo[1]:-Unknown}"
    country="${geo[2]:-Unknown}"
    country_code="${geo[3]:-}"
    override_city="$(env_value SERVER_CITY_OVERRIDE)"
    override_country="$(env_value SERVER_COUNTRY_OVERRIDE)"
    override_country_code="$(env_value SERVER_COUNTRY_CODE_OVERRIDE)"
    [[ -n "${override_city}" ]] && city="${override_city}"
    [[ -n "${override_country}" ]] && country="${override_country}"
    [[ -n "${override_country_code}" ]] && country_code="${override_country_code}"
    if [[ -n "${public_ip}" ]]; then
      set_env_value "PUBLIC_IP" "${public_ip}"
      set_env_value "SERVER_CITY" "${city}"
      set_env_value "SERVER_COUNTRY" "${country}"
      set_env_value "SERVER_COUNTRY_CODE" "${country_code^^}"
      set_env_value "SERVER_NAME" "${city}, ${country}"
      if [[ -n "${override_city}${override_country}${override_country_code}" ]]; then
        ok "применена подтверждённая локация: ${city}, ${country} (${public_ip})."
      elif [[ "${city}" == "Unknown" ]]; then
        warn "страна определена по согласованным сетевым источникам, но физический город не подтверждён: ${country} (${public_ip})."
      else
        ok "локация подтверждена metadata или несколькими источниками: ${city}, ${country} (${public_ip})."
      fi
    fi
  else
    warn "геолокация недоступна или источники не согласованы; сохранены предыдущие значения."
  fi
  rm -f -- "${geo_file}.primary.json" "${geo_file}.fallback.json" "${geo_file}.tertiary.json" \
    "${geo_file}.quaternary.json" "${geo_file}.quinary.json" "${geo_file}.senary.json" "${geo_file}.result"
  public_ip="$(env_value PUBLIC_IP)"
  [[ -n "${public_ip}" ]] || die "не удалось определить PUBLIC_IP; задайте его в ${ENV_FILE}."
  configure_access
}

build_web() {
  info "Сборка веб-интерфейса"
  install -d -m 0750 "$(dirname -- "${INSTALL_LOG}")"
  local lock_hash lock_marker="${INSTALL_DIR}/node_modules/.package-lock.sha256"
  lock_hash="$(sha256sum "${INSTALL_DIR}/package-lock.json" | awk '{print $1}')"
  (
    cd "${INSTALL_DIR}"
    if [[ ! -x node_modules/.bin/vinext || ! -r "${lock_marker}" || "$(<"${lock_marker}")" != "${lock_hash}" ]]; then
      npm ci --include=dev --include=optional --ignore-scripts
      printf '%s\n' "${lock_hash}" >"${lock_marker}"
    fi
    npm run build
  ) >"${INSTALL_LOG}" 2>&1 &
  local build_pid="$!"
  local dots=0
  while kill -0 "${build_pid}" 2>/dev/null; do
    dots=$(( (dots + 1) % 4 ))
    printf '\r  Сборка интерфейса %-3s' "$(printf '.%.0s' $(seq 1 "${dots}"))"
    sleep 1
    if [[ -n "${CURRENT_ACTION}" ]]; then
      ACTION_PROGRESS=$((ACTION_PROGRESS < 88 ? ACTION_PROGRESS + 2 : 92))
      write_action_status "running" "${ACTION_PROGRESS}" "Сборка веб-интерфейса"
    fi
  done
  local build_status=0
  wait "${build_pid}" || build_status=$?
  printf '\r\033[K'
  if (( build_status != 0 )); then
    tail -n 30 "${INSTALL_LOG}" >&2
    return 1
  fi
  ok "веб-интерфейс собран"
}

check_supported_os() {
  [[ -r /etc/os-release ]] || die "не удалось определить операционную систему."
  # shellcheck source=/dev/null
  source /etc/os-release
  [[ ${ID:-} == "ubuntu" || ${ID:-} == "debian" ]] \
    || die "поддерживаются Ubuntu Server и Debian; обнаружена ${PRETTY_NAME:-неизвестная ОС}."
  [[ -n "${VERSION_ID:-}" ]] || die "не удалось определить версию операционной системы."
  case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|debian:12|debian:13) ;;
    *) warn "${PRETTY_NAME:-${ID} ${VERSION_ID}} не проходила расширенную проверку; продолжаем установку с базовыми проверками." ;;
  esac
}

doctor() {
  check_supported_os
  local failed=0 memory_kb disk_kb architecture
  architecture="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "${architecture}" in amd64|arm64|x86_64|aarch64) ok "архитектура: ${architecture}" ;; *) warn "архитектура ${architecture} не проверена"; failed=1 ;; esac
  command -v systemctl >/dev/null && [[ "$(cat /proc/1/comm 2>/dev/null)" == "systemd" ]] \
    && ok "systemd доступен" || { warn "systemd не запущен"; failed=1; }
  memory_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  (( memory_kb >= 900000 )) && ok "оперативная память: $((memory_kb / 1024)) МБ" \
    || { warn "требуется не менее 1 ГБ RAM"; failed=1; }
  # Bootstrap sources may live on a small /tmp tmpfs. Installation itself is
  # written below /opt, so validate the target filesystem rather than /tmp.
  disk_kb="$(df -Pk /opt 2>/dev/null | awk 'NR==2 {print $4}')"
  [[ -n "${disk_kb}" ]] || disk_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
  (( disk_kb >= 5000000 )) && ok "свободное место: $((disk_kb / 1024 / 1024)) ГБ" \
    || { warn "требуется не менее 5 ГБ свободного места"; failed=1; }
  getent hosts github.com >/dev/null 2>&1 && ok "DNS и GitHub доступны" \
    || { warn "не удаётся разрешить github.com"; failed=1; }
  check_source
  (( failed == 0 )) || die "сервер не прошёл предварительную проверку."
  ok "сервер совместим с установкой 312.net."
}

check_manual_dependencies() {
  info "Проверка зависимостей ручной установки (--no-apt)"
  local command_name node_major
  local -a missing=()
  for command_name in caddy curl git ip node npm openssl python3 rsync ss ufw; do
    command -v "${command_name}" >/dev/null 2>&1 || missing+=("${command_name}")
  done
  if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import venv' >/dev/null 2>&1; then
    missing+=("python3-venv")
  fi
  if command -v node >/dev/null 2>&1; then
    node_major="$(node -p 'process.versions.node.split(`.`)[0]' 2>/dev/null || echo 0)"
    [[ "${node_major:-0}" -ge 22 ]] || missing+=("nodejs>=22")
  fi
  if ((${#missing[@]})); then
    printf 'Не хватает зависимостей: %s\n' "${missing[*]}" >&2
    printf 'Установите их вручную и повторите: bash scripts/install-panel.sh --no-apt\n' >&2
    return 1
  fi
  if [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
    warn "dpkg содержит незавершённые пакеты, но режим --no-apt их не изменяет."
  fi
  ok "необходимые зависимости уже установлены; apt/dpkg запускаться не будут."
}

update_platform() {
  if [[ "${PACKAGE_MODE}" == "skip" || "${OS_UPDATE}" != "yes" ]]; then
    [[ "${PACKAGE_MODE}" == "skip" ]] \
      && ok "обновление ОС пропущено: активен режим --no-apt." \
      || ok "обновление ОС пропущено параметром --no-os-update."
    return 0
  fi

  source /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) die "обновление платформы поддерживается только для Ubuntu и Debian." ;;
  esac

  info "Обновление платформы ${PRETTY_NAME:-${ID}}"
  prepare_package_manager
  export DEBIAN_FRONTEND=noninteractive
  run_with_status "Обновление индекса APT" apt-get -o DPkg::Lock::Timeout=300 update
  # Use conservative APT upgrade semantics: update the current release without
  # authorizing removal of installed packages or a distribution-version transition.
  if ! run_with_status "Обновление пакетов ОС" apt-get -o DPkg::Lock::Timeout=300 \
      -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y; then
    if [[ -n "$(dpkg --audit 2>/dev/null)" && -r /dev/tty && -w /dev/tty ]]; then
      warn "обновление остановилось на настройке пакета; открываю системный диалог в текущем терминале."
      printf '\nЗавершите настройку пакетов. Для grub-pc выбирайте диск целиком (например /dev/vda), не /dev/vda1.\n\n' >/dev/tty
      DEBIAN_FRONTEND=dialog dpkg --configure -a </dev/tty >/dev/tty 2>&1 \
        || die "ручная настройка пакетов после обновления не завершена."
      export DEBIAN_FRONTEND=noninteractive
      run_with_status "Повтор обновления пакетов ОС" apt-get -o DPkg::Lock::Timeout=300 \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y
    else
      die "не удалось обновить пакеты ОС; см. ${INSTALL_LOG}."
    fi
  fi
  apt-get -o DPkg::Lock::Timeout=300 check >>"${INSTALL_LOG}" 2>&1 \
    || die "APT сообщает о проблемах после обновления ОС; см. ${INSTALL_LOG}."
  if [[ -e /var/run/reboot-required ]]; then
    REBOOT_AFTER_UPDATE="yes"
    warn "обновлены системные компоненты; после установки панели рекомендуется перезагрузить VPS."
  fi
  ok "${PRETTY_NAME:-${ID}} обновлена до актуальных пакетов текущего релиза."
}

install_packages() {
  info "Установка системных зависимостей"
  if [[ "${PACKAGE_MODE}" == "skip" ]]; then
    check_manual_dependencies
    return
  fi
  export DEBIAN_FRONTEND=noninteractive
  local node_candidate node_major
  local -a distro_node_packages=()
  install -d -m 0750 "$(dirname -- "${INSTALL_LOG}")"
  prepare_package_manager
  run_with_status "Загрузка списка пакетов" apt-get -o DPkg::Lock::Timeout=300 update
  # Не завершаем awk досрочно: с pipefail apt-cache получает SIGPIPE (141).
  node_candidate="$(LC_ALL=C apt-cache policy nodejs 2>/dev/null | awk '/Candidate:/ && !found {print $2; found=1}')"
  node_major="$(sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p' <<<"${node_candidate}")"
  if [[ "${node_major:-0}" -ge 22 ]] && command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    # NodeSource bundles npm in nodejs; asking Debian for its separate npm
    # package on a repeated run creates an unsatisfiable downgrade conflict.
    distro_node_packages=(nodejs)
    ok "Node.js ${node_major} and npm are already installed; keeping the current package source."
  elif [[ "${node_major:-0}" -ge 22 ]] && apt-cache show npm >/dev/null 2>&1; then
    distro_node_packages=(nodejs npm)
    ok "Node.js ${node_major} доступен в репозитории Ubuntu; внешний репозиторий не требуется."
  fi
  run_with_status "Установка системных зависимостей" apt-get -o DPkg::Lock::Timeout=300 install -y auditd build-essential ca-certificates caddy curl fail2ban git iproute2 openssh-server openssl procps python3 python3-venv rsync tar ufw unattended-upgrades "${distro_node_packages[@]}"
  configure_fail2ban
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || [[ "$(node -p 'process.versions.node.split(`.`)[0]' 2>/dev/null || echo 0)" -lt 22 ]]; then
    run_with_status "Подключение Node.js 22" bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
    run_with_status "Установка Node.js 22" apt-get -o DPkg::Lock::Timeout=300 install -y nodejs
  fi
}

configure_fail2ban() {
  install -d -m 0755 /etc/fail2ban/jail.d
  cat >/etc/fail2ban/jail.d/vps-control.local <<'EOF'
[sshd]
enabled = true
backend = systemd
findtime = 10m
maxretry = 5
bantime = 1h
bantime.increment = true
EOF
  fail2ban-client -t >/dev/null
  systemctl enable fail2ban >/dev/null
  systemctl restart fail2ban
}

ssh_access_write_state() {
  local phase="$1" fingerprint="${2:-}" deadline="${3:-}" message="${4:-}"
  install -d -m 0750 "${DATA_DIR}"
  python3 - "${DATA_DIR}/ssh-access.json" "${phase}" "${fingerprint}" "${deadline}" "${message}" <<'PY'
import json, os, sys, tempfile
path, phase, fingerprint, deadline, message = sys.argv[1:]
payload = {"phase": phase, "fingerprint": fingerprint, "rollback_deadline": deadline or None, "message": message}
fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".ssh-access-", text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
}

ssh_access_add_key() {
  local public_key="${1:-}" ssh_dir="/root/.ssh" keys_file="/root/.ssh/authorized_keys" temporary fingerprint
  [[ "${public_key}" != *$'\n'* && "${public_key}" != *$'\r'* ]] || die "публичный SSH-ключ должен занимать одну строку"
  [[ "${public_key}" =~ ^(ssh-ed25519|sk-ssh-ed25519@openssh.com|ecdsa-sha2-nistp256)[[:space:]][A-Za-z0-9+/=]+([[:space:]].*)?$ ]] || die "поддерживается публичный ключ ED25519, FIDO2 ED25519 или ECDSA P-256"
  temporary="$(mktemp)"
  printf '%s\n' "${public_key}" >"${temporary}"
  fingerprint="$(ssh-keygen -lf "${temporary}" -E sha256 2>/dev/null | awk '{print $2}')"
  rm -f -- "${temporary}"
  [[ "${fingerprint}" == SHA256:* ]] || die "OpenSSH отклонил публичный ключ"
  install -d -m 0700 -o root -g root "${ssh_dir}"
  touch "${keys_file}"
  chown root:root "${keys_file}"
  chmod 0600 "${keys_file}"
  if ! ssh-keygen -lf "${keys_file}" -E sha256 2>/dev/null | awk '{print $2}' | grep -Fxq "${fingerprint}"; then
    cp -a "${keys_file}" "${DATA_DIR}/authorized_keys.before-vps-control"
    printf '%s\n' "${public_key}" >>"${keys_file}"
  fi
  sshd -t
  ssh_access_write_state "key-installed" "${fingerprint}" "" "Публичный ключ установлен. Проверьте вход в новой SSH-сессии."
  ok "публичный ключ ${fingerprint} установлен; парольный вход не изменён."
}

ssh_access_begin_hardening() {
  local state_file="${DATA_DIR}/ssh-access.json" phase fingerprint deadline dropin="${SSH_ACCESS_DROPIN}"
  [[ -s /root/.ssh/authorized_keys ]] || die "сначала установите публичный SSH-ключ"
  read -r phase fingerprint <<<"$(python3 - "${state_file}" <<'PY'
import json, sys
try:
    state=json.load(open(sys.argv[1], encoding="utf-8")); print(state.get("phase", ""), state.get("fingerprint", ""))
except (OSError, ValueError): print("", "")
PY
)"
  [[ "${phase}" == "key-installed" || "${phase}" == "rolled-back" ]] || die "безопасное применение уже запущено или ключ ещё не установлен"
  [[ "${fingerprint}" == SHA256:* ]] || die "не найден подтверждённый ключ этого мастера"
  install -d -m 0700 "${DATA_DIR}/ssh-access-backup"
  if [[ -f "${dropin}" ]]; then cp -a "${dropin}" "${DATA_DIR}/ssh-access-backup/dropin"; else rm -f "${DATA_DIR}/ssh-access-backup/dropin"; fi
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' >"${dropin}"
  chmod 0644 "${dropin}"
  if ! sshd -t; then
    rm -f "${dropin}"
    [[ ! -f "${DATA_DIR}/ssh-access-backup/dropin" ]] || cp -a "${DATA_DIR}/ssh-access-backup/dropin" "${dropin}"
    die "новая конфигурация SSH не прошла проверку"
  fi
  systemctl reload ssh.service
  systemctl stop vps-control-ssh-rollback.timer vps-control-ssh-rollback.service >/dev/null 2>&1 || true
  systemd-run --unit=vps-control-ssh-rollback --on-active=5m --timer-property=AccuracySec=1s "${COMMAND_PATH}" ssh-access-rollback >/dev/null
  deadline="$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)"
  ssh_access_write_state "awaiting-confirmation" "${fingerprint}" "${deadline}" "Парольный вход отключён временно. Подтвердите доступ по ключу до автоматического отката."
  ok "парольный вход временно отключён; автоматический откат запланирован на ${deadline}."
}

ssh_access_reset_key() {
  local state_file="${DATA_DIR}/ssh-access.json" phase fingerprint keys_file="/root/.ssh/authorized_keys" temporary
  read -r phase fingerprint <<<"$(python3 - "${state_file}" <<'PY'
import json, sys
try:
    state=json.load(open(sys.argv[1], encoding="utf-8")); print(state.get("phase", ""), state.get("fingerprint", ""))
except (OSError, ValueError): print("", "")
PY
)"
  [[ "${phase}" == "key-installed" || "${phase}" == "rolled-back" ]] || die "ключ можно сбросить только до безопасного применения"
  [[ "${fingerprint}" == SHA256:* && -f "${keys_file}" ]] || die "не найден ключ, установленный этим мастером"
  temporary="$(mktemp)"
  python3 - "${keys_file}" "${temporary}" "${fingerprint}" <<'PY'
import subprocess, sys, tempfile
source, target, expected = sys.argv[1:]
removed = False
with open(source, encoding="utf-8") as handle, open(target, "w", encoding="utf-8") as output:
    for line in handle:
        value = line.strip()
        if not value or value.startswith("#"):
            output.write(line)
            continue
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as key_file:
            key_file.write(value + "\n"); key_file.flush()
            result = subprocess.run(["ssh-keygen", "-lf", key_file.name, "-E", "sha256"], capture_output=True, text=True, check=False)
        fingerprint = result.stdout.split()[1] if result.returncode == 0 and len(result.stdout.split()) > 1 else ""
        if fingerprint == expected:
            removed = True
        else:
            output.write(line)
if not removed:
    raise SystemExit("installed key fingerprint was not found")
PY
  install -m 0600 -o root -g root "${temporary}" "${keys_file}"
  rm -f -- "${temporary}"
  ssh_access_write_state "password" "" "" "Ключ мастера удалён. Можно установить новый публичный ключ."
  ok "ключ ${fingerprint} удалён; остальные ключи не изменены."
}

ssh_access_confirm() {
  local state_file="${DATA_DIR}/ssh-access.json" phase fingerprint
  read -r phase fingerprint <<<"$(python3 - "${state_file}" <<'PY'
import json, sys
try:
    state=json.load(open(sys.argv[1], encoding="utf-8")); print(state.get("phase", ""), state.get("fingerprint", ""))
except (OSError, ValueError): print("", "")
PY
)"
  [[ "${phase}" == "awaiting-confirmation" ]] || die "нет ожидающего подтверждения изменения SSH"
  systemctl stop vps-control-ssh-rollback.timer vps-control-ssh-rollback.service >/dev/null 2>&1 || true
  systemctl reset-failed vps-control-ssh-rollback.service >/dev/null 2>&1 || true
  rm -rf -- "${DATA_DIR}/ssh-access-backup"
  ssh_access_write_state "hardened" "${fingerprint}" "" "Доступ по ключу подтверждён; парольный вход отключён."
  ok "доступ по ключу подтверждён; автоматический откат отменён."
}

ssh_access_rollback() {
  local state_file="${DATA_DIR}/ssh-access.json" phase fingerprint dropin="${SSH_ACCESS_DROPIN}"
  read -r phase fingerprint <<<"$(python3 - "${state_file}" <<'PY'
import json, sys
try:
    state=json.load(open(sys.argv[1], encoding="utf-8")); print(state.get("phase", ""), state.get("fingerprint", ""))
except (OSError, ValueError): print("", "")
PY
)"
  [[ "${phase}" == "awaiting-confirmation" ]] || die "нет ожидающего отката изменения SSH"
  if [[ -f "${DATA_DIR}/ssh-access-backup/dropin" ]]; then cp -a "${DATA_DIR}/ssh-access-backup/dropin" "${dropin}"; else rm -f "${dropin}"; fi
  sshd -t
  systemctl reload ssh.service
  systemctl stop vps-control-ssh-rollback.timer >/dev/null 2>&1 || true
  rm -rf -- "${DATA_DIR}/ssh-access-backup"
  ssh_access_write_state "rolled-back" "${fingerprint}" "" "Предыдущие настройки SSH восстановлены."
  ok "предыдущие настройки SSH восстановлены."
}

secure_server() {
  info "Настройка защиты сервера"
  prepare_package_manager
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y apparmor apparmor-utils auditd fail2ban unattended-upgrades ufw
  configure_fail2ban
  systemctl enable --now unattended-upgrades
  systemctl enable --now auditd
  systemctl enable --now apparmor.service >/dev/null 2>&1 || warn "AppArmor установлен, но для активации может потребоваться перезагрузка."
  install -d -m 0755 /etc/sysctl.d /etc/ssh/sshd_config.d
  cat >/etc/sysctl.d/99-vps-control-routing.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
EOF
  sysctl --system >/dev/null 2>&1 || true
  cat >/etc/ssh/sshd_config.d/99-vps-control-tunnels.conf <<'EOF'
X11Forwarding no
AllowTcpForwarding yes
PermitTunnel yes
EOF
  sshd -t >/dev/null 2>&1 && systemctl reload ssh.service 2>/dev/null || true
  if [[ -f "${ENV_FILE}" ]]; then
    chown root:root "${ENV_FILE}"
    chmod 0600 "${ENV_FILE}"
  fi
  if [[ -f "${COMMAND_PATH}" ]]; then
    chown root:root "${COMMAND_PATH}"
    chmod 0755 "${COMMAND_PATH}"
  fi
  configure_access
  configure_firewall "panel-only"
  install_api
  ensure_api_write_access
  systemctl restart "${APP_NAME}-api.service"
  ok "Firewall, Fail2ban, AppArmor, auditd, sysctl, SSH, API, права и автоматические security-обновления проверены."
}

check_vpn() {
  command -v wg >/dev/null 2>&1 || warn "WireGuard CLI пока не установлен."
  command -v awg >/dev/null 2>&1 || warn "AmneziaWG CLI пока не установлен; панель покажет AWG остановленным."
  [[ -s "/etc/wireguard/${WG_INTERFACE}.conf" ]] || warn "отсутствует /etc/wireguard/${WG_INTERFACE}.conf; WireGuard пока недоступен."
  [[ -s "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf" || -s "/etc/amnezia/${AWG_INTERFACE}.conf" ]] \
    || warn "отсутствует конфигурация ${AWG_INTERFACE}; AmneziaWG пока недоступен."
}

repair_unconfigured_grub_pc() {
  local root_source root_type parent_name boot_disk
  dpkg-query -W -f='${db:Status-Abbrev}' grub-pc 2>/dev/null | grep -q '^iF' || return 1
  [[ ! -d /sys/firmware/efi ]] || return 1
  command -v debconf-set-selections >/dev/null 2>&1 || return 1

  root_source="$(findmnt -nro SOURCE / 2>/dev/null || true)"
  [[ "${root_source}" == /dev/* ]] || return 1
  root_source="$(readlink -f -- "${root_source}" 2>/dev/null || true)"
  root_type="$(lsblk -ndo TYPE "${root_source}" 2>/dev/null || true)"
  if [[ "${root_type}" == "disk" ]]; then
    boot_disk="${root_source}"
  elif [[ "${root_type}" == "part" ]]; then
    parent_name="$(lsblk -ndo PKNAME "${root_source}" 2>/dev/null || true)"
    [[ -n "${parent_name}" && "${parent_name}" != *$'\n'* ]] || return 1
    boot_disk="/dev/${parent_name}"
  else
    return 1
  fi
  [[ -b "${boot_disk}" && "$(lsblk -ndo TYPE "${boot_disk}" 2>/dev/null)" == "disk" ]] || return 1

  warn "grub-pc не настроен; для BIOS-системы однозначно определён загрузочный диск ${boot_disk}."
  printf 'grub-pc grub-pc/install_devices multiselect %s\n' "${boot_disk}" | debconf-set-selections
  printf 'grub-pc grub-pc/install_devices_disks_changed multiselect %s\n' "${boot_disk}" | debconf-set-selections
  DEBIAN_FRONTEND=noninteractive dpkg --configure grub-pc
}

prepare_package_manager() {
  if [[ "${PACKAGE_MODE}" == "skip" ]]; then
    check_manual_dependencies
    return
  fi

  if [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
    if [[ "${PACKAGE_MODE}" == "interactive" ]]; then
      info "Ручное завершение незавершённой пакетной операции"
      if [[ ! -t 0 || ! -r /dev/tty || ! -w /dev/tty ]]; then
        die "dpkg требует ручной настройки, но /dev/tty недоступен. Запустите установку из SSH/VNC: bash scripts/install-panel.sh --manual"
      fi
      printf '\nОткрыта ручная настройка dpkg. Для grub-pc выбирайте загрузочный ДИСК целиком (например /dev/vda), не раздел /dev/vda1.\n\n' >/dev/tty
      DEBIAN_FRONTEND=dialog dpkg --configure -a </dev/tty >/dev/tty 2>&1 \
        || die "ручная настройка dpkg не завершена; исправьте показанную ошибку и повторите установку."
    else
      info "Восстановление незавершённой пакетной операции"
      export DEBIAN_FRONTEND=noninteractive
      if ! apt-get -o DPkg::Lock::Timeout=300 -f install -y; then
        if repair_unconfigured_grub_pc; then
          apt-get -o DPkg::Lock::Timeout=300 -f install -y
        elif [[ -t 0 && -r /dev/tty && -w /dev/tty ]]; then
          warn "автоматическое восстановление dpkg не удалось; переключаюсь на ручную настройку в текущем терминале."
          printf '\nЕсли grub-pc спросит загрузочный диск, выбирайте диск целиком (например /dev/vda), не раздел /dev/vda1.\n\n' >/dev/tty
          DEBIAN_FRONTEND=dialog dpkg --configure -a </dev/tty >/dev/tty 2>&1 \
            || die "ручная настройка dpkg не завершена; исправьте показанную ошибку и повторите установку."
        elif dpkg-query -W grub-pc >/dev/null 2>&1; then
          die "не удалось автоматически настроить grub-pc и нет интерактивного терминала. Повторите установку из SSH/VNC с --manual."
        else
          die "не удалось автоматически восстановить dpkg. Повторите установку из SSH/VNC с --manual."
        fi
      fi
    fi
  fi
  [[ -z "$(dpkg --audit 2>/dev/null)" ]] \
    || die "dpkg остаётся в незавершённом состоянии; выполните ручную настройку и повторите установку."
  export DEBIAN_FRONTEND=noninteractive
}

preflight_protocol_image() {
  local manifest="$1"
  python3 - "${manifest}" <<'PY'
import json
import re
import shutil
import subprocess
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    data = json.load(source)

minimum_free_mb = int(data.get("minimum_free_mb", 128))
free_mb = shutil.disk_usage("/opt").free // (1024 * 1024)
if free_mb < minimum_free_mb:
    raise SystemExit(
        f"Not enough free space for {data.get('name', data.get('id', 'module'))}: "
        f"{free_mb} MB available, {minimum_free_mb} MB required."
    )

for package in data.get("preflight_packages", []):
    if not isinstance(package, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", package):
        raise SystemExit(f"Invalid preflight package in {sys.argv[1]}")
    installed = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}", package],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    ).stdout.strip() == "installed"
    available = bool(subprocess.run(
        ["apt-cache", "show", package],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    ).stdout.strip())
    if not installed and not available:
        raise SystemExit(f"Required package {package} is unavailable in configured APT repositories.")
PY
}

rollback_interrupted_update() {
  [[ "${UPDATE_SWAP_ACTIVE}" == "yes" ]] || return 0
  local rollback target failed
  rollback="$(realpath -m -- "${UPDATE_ROLLBACK_DIR}")"
  target="$(realpath -m -- "${INSTALL_DIR}")"
  [[ "${rollback}" == "${target}.rollback."* ]] || {
    warn "Emergency rollback rejected an unexpected path: ${rollback}"
    return 0
  }
  [[ -d "${rollback}" ]] || return 0

  warn "Update was interrupted after the application swap; restoring the previous release."
  systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
  if [[ -d "${INSTALL_DIR}/venv" && ! -e "${rollback}/venv" ]]; then
    mv -- "${INSTALL_DIR}/venv" "${rollback}/venv"
  fi
  failed="${DATA_DIR}/tmp/interrupted-update.$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 0750 "${DATA_DIR}/tmp"
  [[ ! -e "${failed}" ]] || { warn "Emergency rollback staging path already exists: ${failed}"; return 0; }
  [[ ! -e "${INSTALL_DIR}" ]] || mv -- "${INSTALL_DIR}" "${failed}"
  mv -- "${rollback}" "${INSTALL_DIR}"
  PROJECT_DIR="${INSTALL_DIR}"
  systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service 2>/dev/null || true
  restart_mihomo_manager_if_present || true
  if systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service; then
    [[ ! -d "${failed}" ]] || rm -rf -- "${failed}"
    UPDATE_SWAP_ACTIVE="no"
    UPDATE_ROLLBACK_DIR=""
  else
    warn "Previous release was restored on disk but one or more panel services require attention."
  fi
}

verify_protocol_image_ready() {
  local image_id="$1" port
  case "${image_id}" in
    wg)
      systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service" \
        && ip link show "${WG_INTERFACE}" >/dev/null 2>&1 \
        && wg show "${WG_INTERFACE}" >/dev/null 2>&1 \
        && ss -Hlun | grep -Eq "[:.]${WG_PORT}[[:space:]]"
      ;;
    awg)
      systemctl is-active --quiet "awg-quick@${AWG_INTERFACE}.service" \
        && ip link show "${AWG_INTERFACE}" >/dev/null 2>&1 \
        && awg show "${AWG_INTERFACE}" >/dev/null 2>&1 \
        && ss -Hlun | grep -Eq "[:.]${AWG_PORT}[[:space:]]"
      ;;
    shadowsocks)
      systemctl is-active --quiet vps-control-shadowsocks.target \
        && systemctl is-enabled --quiet vps-control-shadowsocks.target
      ;;
    vless-reality-xhttp)
      port="$(env_value VLESS_REALITY_PORT)"
      port="${port:-${VLESS_REALITY_PORT}}"
      systemctl is-active --quiet vps-control-vless-reality-xhttp.service \
        && ss -Hltn | grep -Eq "[:.]${port}[[:space:]]"
      ;;
    mihomo)
      systemctl is-active --quiet vps-control-mihomo-manager.service \
        && ss -Hltn | grep -Eq '127\.0\.0\.1:8791[[:space:]]'
      ;;
    *)
      return 0
      ;;
  esac
}

wait_protocol_image_ready() {
  local image_id="$1"
  local attempt
  for attempt in {1..20}; do
    verify_protocol_image_ready "${image_id}" && return 0
    sleep 0.5
  done
  verify_protocol_image_ready "${image_id}"
}

install_protocol_image() {
  local image_id="${2:-}"
  [[ "${image_id}" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "некорректный идентификатор образа."
  local image_dir="${INSTALL_DIR}/protocol-images"
  local manifest=""
  while IFS= read -r candidate; do
    if [[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "${candidate}")" == "${image_id}" ]]; then
      manifest="${candidate}"
      break
    fi
  done < <(find "${image_dir}" -mindepth 2 -maxdepth 2 -type f -name manifest.json -print)
  [[ -n "${manifest}" ]] || die "образ ${image_id} не найден."
  local installer uninstaller image_root module_log failure_message installer_status
  image_root="$(dirname -- "${manifest}")"
  installer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("installer",""))' "${manifest}")"
  uninstaller="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("uninstaller",""))' "${manifest}")"
  source /etc/os-release
  if ! python3 - "${manifest}" "${ID:-unknown}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    supported = json.load(source).get("supported_os", [])
raise SystemExit(0 if sys.argv[2] in supported else 1)
PY
  then
    die "образ ${image_id} не поддерживает ОС ${ID:-unknown}."
  fi
  [[ "${installer}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${installer}" ]] \
    || die "образ ${image_id} содержит некорректный installer."
  info "Установка образа ${image_id}"
  prepare_package_manager
  preflight_protocol_image "${manifest}" \
    || die "preflight модуля ${image_id} не пройден; установка не запускалась."
  apt-get -o DPkg::Lock::Timeout=300 check \
    || die "пакетный менеджер не готов к установке ${image_id}; выполните apt-get check."
  module_log="/var/log/${APP_NAME}-protocol-${image_id}.log"
  install -m 0600 /dev/null "${module_log}"
  set +e
  ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
    AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
    PUBLIC_IP="$(env_value PUBLIC_IP)" ENABLE_UFW="${ENABLE_UFW}" \
    bash "${image_root}/${installer}" >"${module_log}" 2>&1
  installer_status=$?
  if [[ "${installer_status}" -eq 0 ]] && ! wait_protocol_image_ready "${image_id}" >>"${module_log}" 2>&1; then
    echo "Post-install health-check failed for ${image_id}" >>"${module_log}"
    installer_status=1
  fi
  set -e
  if [[ "${installer_status}" -ne 0 ]]; then
      tail -n 40 "${module_log}" >&2 || true
      failure_message="$(tail -n 1 "${module_log}" | tr '\n\r' ' ' | cut -c1-240)"
      [[ -n "${failure_message}" ]] || failure_message="установщик завершился с ошибкой"
      if [[ "${uninstaller}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${uninstaller}" ]]; then
        echo "Откат частично установленного образа ${image_id}" >>"${module_log}"
        ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
          AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
          PUBLIC_IP="$(env_value PUBLIC_IP)" ENABLE_UFW="${ENABLE_UFW}" \
          bash "${image_root}/${uninstaller}" >>"${module_log}" 2>&1 \
          || echo "Автоматическая очистка завершилась не полностью" >>"${module_log}"
      fi
      if [[ "${installer_status}" -eq 75 ]]; then
        failure_message="Требуется одна перезагрузка VPS: ${failure_message}"
      fi
      write_action_status "failed" "${ACTION_PROGRESS}" "${failure_message}; журнал: ${module_log}"
      CURRENT_ACTION=""
      die "не удалось установить ${image_id}; журнал: ${module_log}"
  fi
  install -d -m 0700 /etc/wireguard /etc/amnezia /etc/amnezia/amneziawg
  if [[ "${image_id}" == "wg" || "${image_id}" == "awg" ]]; then
    configure_vpn_firewall_policy
  fi
  # Protocol clients persist configs below /etc/vps-control.  Keep the API
  # sandbox in sync even when a module is installed on an older deployment
  # whose service unit predates the writable path.
  ensure_api_write_access
  systemctl restart "${APP_NAME}-api.service"
  sync_protocol_monitor
  ok "Образ ${image_id} установлен."
}

remove_protocol_image() {
  local image_id="${2:-}"
  [[ "${image_id}" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "некорректный идентификатор образа."
  local manifest
  manifest="$(find "${INSTALL_DIR}/protocol-images" -mindepth 2 -maxdepth 2 -type f -name manifest.json -print | while read -r candidate; do
    [[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "${candidate}")" == "${image_id}" ]] && { echo "${candidate}"; break; }
  done)"
  [[ -n "${manifest}" ]] || die "образ ${image_id} не найден."
  local uninstaller image_root
  image_root="$(dirname -- "${manifest}")"
  uninstaller="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("uninstaller",""))' "${manifest}")"
  [[ "${uninstaller}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${uninstaller}" ]] \
    || die "образ ${image_id} не поддерживает удаление."
  info "Удаление установленного протокола ${image_id}"
  ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
    AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
    PUBLIC_IP="$(env_value PUBLIC_IP)" ENABLE_UFW="${ENABLE_UFW}" \
    bash "${image_root}/${uninstaller}"
  install -d -m 0700 /etc/wireguard /etc/amnezia /etc/amnezia/amneziawg
  ensure_api_write_access
  systemctl restart "${APP_NAME}-api.service"
  sync_protocol_monitor
  ok "Протокол ${image_id} удалён; образ сохранён."
}

update_protocol_image() {
  local image_id="${2:-}"
  [[ "${image_id}" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || die "некорректный идентификатор образа."
  local image_dir="${INSTALL_DIR}/protocol-images"
  local manifest=""
  while IFS= read -r candidate; do
    if [[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "${candidate}")" == "${image_id}" ]]; then
      manifest="${candidate}"
      break
    fi
  done < <(find "${image_dir}" -mindepth 2 -maxdepth 2 -type f -name manifest.json -print)
  [[ -n "${manifest}" ]] || die "образ ${image_id} не найден."
  local image_root package installer module_log failure_message
  image_root="$(dirname -- "${manifest}")"
  package="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("package",""))' "${manifest}")"
  installer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("installer",""))' "${manifest}")"
  info "Обновление образа ${image_id}"
  module_log="/var/log/${APP_NAME}-protocol-${image_id}.log"
  install -m 0600 /dev/null "${module_log}"
  if [[ -n "${package}" ]]; then
    # apt-tracked tool (wg/awg/shadowsocks): upgrade the package only. The
    # running tunnel/service is deliberately left untouched here — restarting
    # it would drop live connections without the admin explicitly asking for
    # that; the panel surfaces whether a restart is actually needed.
    prepare_package_manager
    {
      apt-get -o DPkg::Lock::Timeout=300 update &&
      apt-get -o DPkg::Lock::Timeout=300 install --only-upgrade -y "${package}"
    } >"${module_log}" 2>&1 || {
      tail -n 40 "${module_log}" >&2 || true
      failure_message="$(tail -n 1 "${module_log}" | tr '\n\r' ' ' | cut -c1-240)"
      [[ -n "${failure_message}" ]] || failure_message="обновление пакета завершилось с ошибкой"
      write_action_status "failed" "${ACTION_PROGRESS}" "${failure_message}; журнал: ${module_log}"
      CURRENT_ACTION=""
      die "не удалось обновить ${image_id}; журнал: ${module_log}"
    }
  else
    # Self-fetching image (e.g. VLESS Reality's Xray binary): its own
    # installer already downloads and verifies the latest official release.
    # XRAY_UPDATE_ONLY makes it stop right after swapping the binary in,
    # skipping the TLS probe/config/systemd-restart steps so the running
    # service (and its live connections) is not touched by this update.
    [[ "${installer}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${installer}" ]] \
      || die "образ ${image_id} не поддерживает обновление."
    ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
      AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
      PUBLIC_IP="$(env_value PUBLIC_IP)" ENABLE_UFW="${ENABLE_UFW}" \
      XRAY_UPDATE_ONLY=1 MIHOMO_UPDATE_ONLY=1 \
      bash "${image_root}/${installer}" >"${module_log}" 2>&1 || {
        tail -n 40 "${module_log}" >&2 || true
        failure_message="$(tail -n 1 "${module_log}" | tr '\n\r' ' ' | cut -c1-240)"
        [[ -n "${failure_message}" ]] || failure_message="установщик завершился с ошибкой"
        write_action_status "failed" "${ACTION_PROGRESS}" "${failure_message}; журнал: ${module_log}"
        CURRENT_ACTION=""
        die "не удалось обновить ${image_id}; журнал: ${module_log}"
      }
  fi
  if [[ "${image_id}" == "mihomo" ]]; then
    systemctl restart vps-control-mihomo-manager.service
  fi
  ensure_api_write_access
  systemctl restart "${APP_NAME}-api.service"
  sync_protocol_monitor
  ok "Образ ${image_id} обновлён."
}

client_firewall() {
  local action="${2:-}" port="${3:-}" port_end=$((SHADOWSOCKS_PORT_START + 9999))
  (( port_end <= 65535 )) || port_end=65535
  [[ "${action}" == "add" || "${action}" == "delete" ]] || die "client-firewall: ожидается add или delete."
  [[ "${port}" =~ ^[0-9]+$ ]] || die "client-firewall: порт должен быть числом."
  (( port >= SHADOWSOCKS_PORT_START && port <= port_end )) || die "client-firewall: порт вне диапазона Shadowsocks."
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status | grep -q '^Status: active' || return 0
  if [[ "${action}" == "add" ]]; then
    ufw allow "${port}/tcp" comment '312.net Shadowsocks client'
    ufw allow "${port}/udp" comment '312.net Shadowsocks client'
  else
    ufw --force delete allow "${port}/tcp" >/dev/null 2>&1 || true
    ufw --force delete allow "${port}/udp" >/dev/null 2>&1 || true
  fi
}

configure_firewall() {
  info "Настройка firewall"
  [[ "${ENABLE_UFW}" == "yes" ]] || { warn "настройка UFW отключена в install.conf."; return; }
  [[ "${1:-}" == "panel-only" ]] || ufw allow OpenSSH
  if [[ "${ACCESS_MODE}" == "local" ]]; then
    detect_local_network
    ufw allow from "${LOCAL_CIDR}" to any port "${HTTP_PORT}" proto tcp
    ufw delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
  elif [[ "${ACCESS_MODE}" == "vpn" ]]; then
    local vpn_interface_available="no" openvpn_interface ike_pool
    if ip link show "${WG_INTERFACE}" >/dev/null 2>&1; then
      ufw allow in on "${WG_INTERFACE}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via WG'
      ufw allow in on "${WG_INTERFACE}" to any port 80 proto tcp comment '312.net admin host via WG'
      [[ -z "$(env_value PUBLIC_DOMAIN)" ]] \
        || ufw allow in on "${WG_INTERFACE}" to any port 443 proto tcp comment '312.net HTTPS panel via WG'
      vpn_interface_available="yes"
    fi
    if ip link show "${AWG_INTERFACE}" >/dev/null 2>&1; then
      ufw allow in on "${AWG_INTERFACE}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via AWG'
      ufw allow in on "${AWG_INTERFACE}" to any port 80 proto tcp comment '312.net admin host via AWG'
      [[ -z "$(env_value PUBLIC_DOMAIN)" ]] \
        || ufw allow in on "${AWG_INTERFACE}" to any port 443 proto tcp comment '312.net HTTPS panel via AWG'
      vpn_interface_available="yes"
    fi
    openvpn_interface="$(ip -o -4 route show 10.74.0.0/24 2>/dev/null | awk 'NR==1 {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"
    if [[ -n "${openvpn_interface}" ]] && systemctl is-active --quiet vps-control-openvpn.service; then
      ufw allow in on "${openvpn_interface}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via OpenVPN'
      ufw allow in on "${openvpn_interface}" to any port 80 proto tcp comment '312.net admin host via OpenVPN'
      [[ -z "$(env_value PUBLIC_DOMAIN)" ]] || ufw allow in on "${openvpn_interface}" to any port 443 proto tcp comment '312.net HTTPS panel via OpenVPN'
      vpn_interface_available="yes"
    fi
    ike_pool="$(python3 -c 'import json; print(json.load(open("/etc/vps-control/ikev2/settings.json")).get("pool",""))' 2>/dev/null || true)"
    if [[ -n "${ike_pool}" ]] && systemctl is-active --quiet vps-control-ikev2.service; then
      ufw allow from "${ike_pool}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via IKEv2'
      ufw allow from "${ike_pool}" to any port 80 proto tcp comment '312.net admin host via IKEv2'
      [[ -z "$(env_value PUBLIC_DOMAIN)" ]] || ufw allow from "${ike_pool}" to any port 443 proto tcp comment '312.net HTTPS panel via IKEv2'
      vpn_interface_available="yes"
    fi
    [[ "${vpn_interface_available}" == "yes" || "$(configured_panel_channel_count)" -gt 0 ]] || die "Сначала настройте хотя бы одно защищённое подключение."
    ufw delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
    ufw delete allow 443/tcp >/dev/null 2>&1 || true
  else
    if [[ -n "$(env_value PUBLIC_DOMAIN)" || -n "$(env_value VLESS_CDN_DOMAIN)" ]]; then
      ufw --force delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
      ufw allow 80/tcp comment '312.net HTTPS redirect'
      ufw allow 443/tcp comment '312.net HTTPS panel'
    else
      ufw allow "${HTTP_PORT}/tcp"
    fi
  fi
  if [[ -n "$(env_value VLESS_CDN_DOMAIN)" ]]; then
    ufw allow 80/tcp comment '312.net VLESS CDN certificate'
    ufw allow 443/tcp comment '312.net VLESS CDN HTTPS'
  fi
  ufw --force enable
}

configure_vless_cdn_firewall() {
  command -v ufw >/dev/null 2>&1 || return 0
  [[ "${ENABLE_UFW}" == "yes" ]] || return 0
  ufw allow 80/tcp comment '312.net VLESS CDN certificate'
  ufw allow 443/tcp comment '312.net VLESS CDN HTTPS'
}

configure_vpn_firewall_policy() {
  local uplink policy_script policy_service interface subnet port installed=0
  uplink="$(ip -4 route show default | awk 'NR == 1 {print $5}')"
  [[ -n "${uplink}" ]] || die "не найден основной сетевой интерфейс сервера."
  policy_script="/usr/local/sbin/vps-control-vpn-firewall"
  policy_service="/etc/systemd/system/vps-control-vpn-firewall.service"

  install -d -m 0755 /etc/sysctl.d
  printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/99-vps-control-forwarding.conf
  sysctl -w net.ipv4.ip_forward=1 >/dev/null

  {
    printf '%s\n' '#!/usr/bin/env bash' 'set -Eeuo pipefail'
    printf 'uplink=%q\n' "${uplink}"
    for interface in "${WG_INTERFACE}" "${AWG_INTERFACE}"; do
      [[ -e "/sys/class/net/${interface}" ]] || continue
      subnet="$(ip -4 route show dev "${interface}" proto kernel scope link | awk 'NR == 1 {print $1}')"
      [[ -n "${subnet}" ]] || continue
      if [[ "${interface}" == "${WG_INTERFACE}" ]]; then
        port="${WG_PORT}"
      else
        port="${AWG_PORT}"
      fi
      installed=$((installed + 1))
      printf 'iptables -C INPUT -p udp --dport %q -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport %q -j ACCEPT\n' "${port}" "${port}"
      printf 'iptables -C FORWARD -i %q -o "$uplink" -s %q -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i %q -o "$uplink" -s %q -j ACCEPT\n' "${interface}" "${subnet}" "${interface}" "${subnet}"
      printf 'iptables -C FORWARD -o %q -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o %q -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT\n' "${interface}" "${interface}"
      printf 'iptables -t nat -C POSTROUTING -s %q -o "$uplink" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s %q -o "$uplink" -j MASQUERADE\n' "${subnet}" "${subnet}"
    done
  } >"${policy_script}"
  (( installed > 0 )) || { rm -f "${policy_script}"; die "нет активных интерфейсов WG/AWG для настройки."; }
  chmod 0755 "${policy_script}"

  cat >"${policy_service}" <<EOF
[Unit]
Description=312.net VPN firewall policy
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${policy_script}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$(basename "${policy_service}")" >/dev/null
  "${policy_script}"
  systemctl restart "${APP_NAME}-api.service"
  ok "маршрутизация, stateful return и NAT для WG/AWG восстановлены."
}

check_source() {
  [[ -f "${PROJECT_DIR}/package.json" && -f "${PROJECT_DIR}/package-lock.json" ]] || die "не найдено веб-приложение в ${PROJECT_DIR}."
  [[ -f "${PROJECT_DIR}/api/requirements.txt" ]] || die "не найден API в ${PROJECT_DIR}."
  [[ -f "${PROJECT_DIR}/.env.example" ]] || die "не найден .env.example в ${PROJECT_DIR}."
  [[ -f "${PROJECT_DIR}/install.conf" ]] || die "не найден install.conf в ${PROJECT_DIR}."
}

save_source_path() {
  local remote
  remote="$(git -C "${PROJECT_DIR}" remote get-url origin 2>/dev/null || true)"
  # Архивная ручная установка может не содержать .git; используем официальный HTTPS-origin.
  [[ -n "${remote}" ]] || remote="https://github.com/aske312/vpsController.git"
  # Всегда сохраняем публичный GitHub-origin по HTTPS, чтобы серверу не требовался SSH-ключ.
  if [[ "${remote}" =~ ^git@github\.com:(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  elif [[ "${remote}" =~ ^ssh://git@github\.com/(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  fi
  {
    printf 'REMOTE_URL=%q\n' "${remote}"
    printf 'UPDATE_BRANCH=%q\n' "${PRODUCTION_BRANCH}"
  } >"${MANAGER_CONFIG}"
  chmod 0600 "${MANAGER_CONFIG}"
  install -m 0600 "${PROJECT_DIR}/install.conf" "${INSTALL_CONFIG}"
}

sync_release() {
  info "Копирование приложения в ${INSTALL_DIR}"
  install -d -m 0755 "${INSTALL_DIR}"
  chmod 0755 "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.idea/' \
    --exclude '.openai/' \
    --include '.env.example' \
    --exclude '.env*' \
    --exclude 'node_modules/' \
    --exclude 'venv/' \
    --exclude '.vinext/' \
    --exclude '.wrangler/' \
    --exclude '.runtime/' \
    --exclude 'dist/' \
    --exclude 'outputs/' \
    --exclude '.package-stage-*/' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"
  chmod 0755 "${INSTALL_DIR}/scripts/vps-control.sh"
  if [[ "${PRESERVE_MANAGER}" != "yes" ]]; then
    install -m 0755 "${PROJECT_DIR}/scripts/vps-control.sh" "${COMMAND_PATH}"
  fi
}

write_integrity_manifest() {
  local manifest="${DATA_DIR}/release.sha256"
  install -d -m 0750 "${DATA_DIR}"
  find \
    "${INSTALL_DIR}/api" \
    "${INSTALL_DIR}/app" \
    "${INSTALL_DIR}/public" \
    "${INSTALL_DIR}/protocol-images" \
    "${INSTALL_DIR}/scripts" \
    -type f \
    ! -path '*/__pycache__/*' \
    ! -name '*.pyc' \
    -print0 \
    | sort -z \
    | xargs -0 sha256sum >"${manifest}"
  for file in Caddyfile package.json package-lock.json install.conf; do
    [[ ! -f "${INSTALL_DIR}/${file}" ]] || sha256sum "${INSTALL_DIR}/${file}" >>"${manifest}"
  done
  chmod 0600 "${manifest}"
}

ensure_environment() {
  install -d -m 0750 "${DATA_DIR}" "${DATA_DIR}/tmp" "${DATA_DIR}/logs" /etc/wireguard /etc/amnezia
  install -d -m 0750 -o root -g nogroup "${CONFIG_DIR}"
  # ProtectSystem=strict cannot reliably make one file below /etc writable on
  # every supported systemd version. Keep the public path compatible while the
  # real file lives in the already allow-listed application directory.
  if [[ ! -s "${ENV_FILE}" && -f "${LEGACY_ENV_FILE}" ]]; then
    mv "${LEGACY_ENV_FILE}" "${ENV_FILE}"
  fi
  ln -sfn "${ENV_FILE}" "${LEGACY_ENV_FILE}"
  # sshd keeps the first value it reads. Migrate the old late-sorting drop-in
  # so cloud-init cannot keep PasswordAuthentication enabled ahead of it.
  if [[ -f "${LEGACY_SSH_ACCESS_DROPIN}" ]] && grep -q '"phase"[[:space:]]*:[[:space:]]*"hardened"' "${DATA_DIR}/ssh-access.json" 2>/dev/null; then
    mv "${LEGACY_SSH_ACCESS_DROPIN}" "${SSH_ACCESS_DROPIN}"
    sshd -t
    systemctl reload ssh.service 2>/dev/null || true
  fi
  rm -f -- "${DATA_DIR}/personalization.json"
  if [[ ! -s "${ENV_FILE}" ]]; then
    install -m 0600 "${PROJECT_DIR}/.env.example" "${ENV_FILE}"
    ln -sfn "${ENV_FILE}" "${LEGACY_ENV_FILE}"
    # Архив мог быть подготовлен на Windows. CR в EnvironmentFile становится
    # частью адресов и ломает Caddy/PANEL_URL, поэтому нормализуем шаблон.
    sed -i 's/\r$//' "${ENV_FILE}"
    set_env_value "ADMIN_USER" "${ADMIN_USER}"
    set_env_value "ADMIN_PASSWORD" "${ADMIN_PASSWORD}"
    chmod 0600 "${ENV_FILE}"
    ok "создан ${ENV_FILE}; постоянные учётные данные администратора подготовлены."
  else
    ok "существующий ${ENV_FILE} сохранён."
    [[ -n "$(env_value ADMIN_USER)" ]] || set_env_value "ADMIN_USER" "${ADMIN_USER}"
    if [[ -n "$(env_value ADMIN_PASSWORD)" ]]; then
      # Re-serialize passwords written by older builds without sourcing an
      # unsafe '$' or backtick from the existing env file.
      local existing_password
      existing_password="$(python3 - "${ENV_FILE}" <<'PY'
import ast
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("ADMIN_PASSWORD="):
        value = line.rstrip("\n").split("=", 1)[1]
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            pass
        print(value, end="")
        break
PY
)"
      [[ -n "${existing_password}" ]] && set_env_value "ADMIN_PASSWORD" "${existing_password}"
    else
      set_env_value "ADMIN_PASSWORD" "${ADMIN_PASSWORD}"
    fi
  fi
  [[ -z "${SERVER_CITY_OVERRIDE:-}" ]] || set_env_value "SERVER_CITY_OVERRIDE" "${SERVER_CITY_OVERRIDE}"
  [[ -z "${SERVER_COUNTRY_OVERRIDE:-}" ]] || set_env_value "SERVER_COUNTRY_OVERRIDE" "${SERVER_COUNTRY_OVERRIDE}"
  [[ -z "${SERVER_COUNTRY_CODE_OVERRIDE:-}" ]] || set_env_value "SERVER_COUNTRY_CODE_OVERRIDE" "${SERVER_COUNTRY_CODE_OVERRIDE^^}"
  # Re-evaluate the hosting location from the current public IP on every deploy.
  # Explicit SERVER_*_OVERRIDE values still take precedence over GeoIP.
  refresh_server_identity
}

ensure_runtime_dependencies() {
  if [[ "${PACKAGE_MODE}" == "skip" ]]; then
    check_manual_dependencies
    return
  fi
  local command_name
  for command_name in caddy curl git node npm python3 rsync; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      install_packages
      return
    fi
  done
  if [[ "$(node -p 'process.versions.node.split(`.`)[0]' 2>/dev/null || echo 0)" -lt 22 ]]; then
    install_packages
  fi
}

install_api() {
  local requirements_hash requirements_marker="${INSTALL_DIR}/venv/.requirements.sha256"
  requirements_hash="$(sha256sum "${INSTALL_DIR}/api/requirements.txt" | awk '{print $1}')"
  if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
    run_with_status "Подготовка Python API" python3 -m venv "${INSTALL_DIR}/venv"
  fi
  if [[ ! -x "${INSTALL_DIR}/venv/bin/pip" || ! -r "${requirements_marker}" || "$(<"${requirements_marker}")" != "${requirements_hash}" ]]; then
    run_with_status "Установка Python-зависимостей" \
      "${INSTALL_DIR}/venv/bin/pip" install --disable-pip-version-check \
        -r "${INSTALL_DIR}/api/requirements.txt"
    printf '%s\n' "${requirements_hash}" >"${requirements_marker}"
  else
    ok "Python-зависимости не изменились."
  fi

  cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=312.net Infrastructure API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${INSTALL_DIR}/api
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
KillMode=mixed
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=-/etc/vps-control.env -/etc/vps-control -/etc/swanctl -/etc/caddy/vps-control.d -/etc/wireguard -/etc/amnezia ${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${APP_NAME}-api.service" >>"${INSTALL_LOG}" 2>&1
}

ensure_api_write_access() {
  local expected="ReadWritePaths=-/etc/vps-control.env -/etc/vps-control -/etc/swanctl -/etc/caddy/vps-control.d -/etc/wireguard -/etc/amnezia ${DATA_DIR}"
  install -d -m 0750 -o root -g nogroup "${CONFIG_DIR}"
  if [[ ! -s "${ENV_FILE}" && -f "${LEGACY_ENV_FILE}" ]]; then
    mv "${LEGACY_ENV_FILE}" "${ENV_FILE}"
  fi
  ln -sfn "${ENV_FILE}" "${LEGACY_ENV_FILE}"
  if ! grep -Fxq "EnvironmentFile=${ENV_FILE}" "${SERVICE_FILE}"; then
    sed -i "s|^EnvironmentFile=.*|EnvironmentFile=${ENV_FILE}|" "${SERVICE_FILE}"
    systemctl daemon-reload
  fi
  if ! grep -Fxq "${expected}" "${SERVICE_FILE}"; then
    sed -i "s|^ReadWritePaths=.*|${expected}|" "${SERVICE_FILE}"
    systemctl daemon-reload
  fi
}

install_protocol_monitor() {
  if [[ ! -s "/etc/wireguard/${WG_INTERFACE}.conf" && ! -s "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf" && ! -s "/etc/amnezia/${AWG_INTERFACE}.conf" ]]; then
    systemctl disable --now vpn-monitor.timer vpn-monitor.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/vpn-monitor.service /etc/systemd/system/vpn-monitor.timer /etc/logrotate.d/vps-control-monitor
    systemctl daemon-reload
    return 0
  fi
  install -d -m 0750 "${DATA_DIR}/monitor"
  install -m 0755 "${INSTALL_DIR}/scripts/vpn-monitor-sample" /usr/local/sbin/vpn-monitor-sample
  cat >/etc/systemd/system/vpn-monitor.service <<'EOF'
[Unit]
Description=Collect 312.net VPN protocol metrics
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vpn-monitor-sample
Nice=10
IOSchedulingClass=idle
EOF
  cat >/etc/systemd/system/vpn-monitor.timer <<'EOF'
[Unit]
Description=Collect 312.net VPN metrics every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=5s
Persistent=true

[Install]
WantedBy=timers.target
EOF
  cat >/etc/logrotate.d/vps-control-monitor <<EOF
${DATA_DIR}/monitor/*.csv ${DATA_DIR}/monitor/*.log {
  daily
  rotate 14
  size 20M
  missingok
  notifempty
  compress
  delaycompress
  copytruncate
}
EOF
  systemctl daemon-reload
  systemctl enable --now vpn-monitor.timer >>"${INSTALL_LOG}" 2>&1
  systemctl start vpn-monitor.service >>"${INSTALL_LOG}" 2>&1
}

install_web() {
  install -d -m 0750 "${DATA_DIR}/web"
  cat >"${WEB_SERVICE_FILE}" <<EOF
[Unit]
Description=312.net Web Interface
After=network-online.target ${APP_NAME}-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment=WRANGLER_LOG_PATH=${DATA_DIR}/web/wrangler.log
ExecStart=${INSTALL_DIR}/node_modules/.bin/vinext start --hostname 127.0.0.1
Restart=on-failure
RestartSec=3
User=root
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${DATA_DIR}/web

[Install]
WantedBy=multi-user.target
EOF
  install -d -m 0755 /etc/caddy
  write_caddy_config
  caddy validate --config "${CADDY_CONFIG}" >/dev/null
  systemctl daemon-reload
  systemctl enable "${APP_NAME}-web.service" caddy.service >>"${INSTALL_LOG}" 2>&1
}

stop_legacy_containers() {
  if command -v docker >/dev/null 2>&1; then
    docker stop vps-control-gateway-1 vps-control-web-1 >/dev/null 2>&1 || true
  fi
}

start_legacy_containers() {
  command -v docker >/dev/null 2>&1 || return 0
  docker start vps-control-web-1 vps-control-gateway-1 >/dev/null 2>&1 || true
}

cleanup_legacy_runtime() {
  rm -f -- "${INSTALL_DIR}/Dockerfile" "${INSTALL_DIR}/docker-compose.yml"
  command -v docker >/dev/null 2>&1 || return 0

  local other_containers docker_packages=()
  other_containers="$(docker ps -a --format '{{.Names}}' 2>/dev/null \
    | grep -Ev '^(vps-control-web-1|vps-control-gateway-1)$' || true)"
  docker volume rm vps-control_app_runtime vps-control_caddy_data vps-control_caddy_config >/dev/null 2>&1 || true
  docker image rm vps-control-web >/dev/null 2>&1 || true

  if [[ -n "${other_containers}" ]]; then
    warn "Docker используется посторонними контейнерами; пакеты Docker сохранены."
    return 0
  fi

  docker system prune -af --volumes >/dev/null 2>&1 || true
  systemctl disable --now docker.service docker.socket >/dev/null 2>&1 || true
  for package in docker.io docker-compose-v2 docker-compose-plugin; do
    dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q 'install ok installed' \
      && docker_packages+=("${package}")
  done
  if (( ${#docker_packages[@]} > 0 )); then
    apt-get -o DPkg::Lock::Timeout=300 purge -y "${docker_packages[@]}"
    apt-get -o DPkg::Lock::Timeout=300 autoremove --purge -y
  fi
  ok "устаревшие Docker-компоненты 312.net удалены."
}

sync_protocol_monitor() {
  install_protocol_monitor
}

# Mihomo Manager runs protocol-images/mihomo/manager.py as its own long-lived
# uvicorn process (vps-control-mihomo-manager.service), separate from
# vps-control-api.service. A release swap replaces that file on disk like
# everything else, but restarting only api/web/caddy leaves it running the
# old code in memory indefinitely - restart it too whenever it's installed.
restart_mihomo_manager_if_present() {
  systemctl list-unit-files --no-legend 'vps-control-mihomo-manager.service' 2>/dev/null | grep -q . \
    && systemctl restart vps-control-mihomo-manager.service 2>/dev/null || true
}

# Profiles survive application releases, while their package-backed transport
# may have been removed by an older uninstaller or an administrator. Repair the
# runtime before restarting preserved instances; otherwise systemd enters an
# unbounded restart loop because the generated units still reference ss-server.
ensure_mihomo_profile_runtimes() {
  local config_dir="/etc/vps-control/mihomo/shadowsocks"
  find "${config_dir}" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q . || return 0
  command -v ss-server >/dev/null 2>&1 && return 0

  info "Восстановление runtime Mihomo/Shadowsocks для сохранённых профилей"
  if ! apt-get -o DPkg::Lock::Timeout=300 install -y shadowsocks-libev; then
    apt-get -o DPkg::Lock::Timeout=300 update
    apt-get -o DPkg::Lock::Timeout=300 install -y shadowsocks-libev
  fi
  command -v ss-server >/dev/null 2>&1 || die "не удалось восстановить ss-server для профилей Mihomo."
  systemctl reset-failed 'vps-control-mihomo-ss@*.service' 2>/dev/null || true
  systemctl restart vps-control-mihomo-ss.target 2>/dev/null || true
}

deploy() {
  check_source
  ensure_runtime_dependencies
  APP_VERSION="$(python3 - "${PROJECT_DIR}/package.json" <<'PY'
import json, sys
version = str(json.load(open(sys.argv[1], encoding="utf-8")).get("version", "1.0.0")).split(".")
print("v" + ".".join((version + ["0", "0"])[:3]))
PY
)"
  BUILD_COMMIT="$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || printf unknown)"
  export APP_VERSION BUILD_COMMIT
  export NEXT_PUBLIC_APP_VERSION="${APP_VERSION}"
  export NEXT_PUBLIC_BUILD_COMMIT="${BUILD_COMMIT}"
  if [[ ! -r "${INSTALL_CONFIG}" ]]; then
    install -m 0600 "${PROJECT_DIR}/install.conf" "${INSTALL_CONFIG}"
  fi
  sync_release
  write_integrity_manifest
  printf '%s\n' "${BUILD_COMMIT}" >"${INSTALL_DIR}/.build-commit"
  rm -f "${DATA_DIR}/application-version.json"
  ensure_environment
  install_api
  build_web
  install_web
  ensure_api_write_access
  install_protocol_monitor
  ensure_mihomo_profile_runtimes
  info "Запуск обновлённой версии 312.net"
  stop_legacy_containers
  systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  restart_mihomo_manager_if_present
  curl --fail --silent --show-error --retry 6 --retry-connrefused --retry-delay 2 \
    "http://127.0.0.1:3000/" >/dev/null
  cleanup_legacy_runtime
  ok "панель запущена: ${PANEL_URL}"
}

start_services() {
  configure_access
  ensure_api_write_access
  if [[ -r "${INSTALL_DIR}/.build-commit" ]]; then
    BUILD_COMMIT="$(<"${INSTALL_DIR}/.build-commit")"
  fi
  export APP_VERSION BUILD_COMMIT
  info "Запуск 312.net"
  systemctl start "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  ok "панель запущена: ${PANEL_URL}"
}

stop_services() {
  info "Остановка 312.net"
  systemctl stop caddy.service "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
  ok "панель остановлена."
}

uninstall_app() {
  [[ "${2:-}" == "--yes" ]] || die "полное удаление необратимо; повторите команду с параметром --yes."
  info "Удаление служб, данных и конфигурации панели"
  stop_legacy_containers
  systemctl disable --now "${APP_NAME}-api.service" "${APP_NAME}-web.service" 2>/dev/null || true
  systemctl disable --now vpn-monitor.timer 2>/dev/null || true
  rm -f "${SERVICE_FILE}" "${WEB_SERVICE_FILE}" /etc/systemd/system/vpn-monitor.service /etc/systemd/system/vpn-monitor.timer \
    /etc/logrotate.d/vps-control-monitor "${COMMAND_PATH}"
  systemctl daemon-reload
  rm -rf -- "${INSTALL_DIR}" "${DATA_DIR}"
  rm -f -- "${ENV_FILE}" "${INSTALL_CONFIG}" "${MANAGER_CONFIG}"
  ufw delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
  ok "панель полностью удалена; общие системные пакеты сохранены."
}

restart_services() {
  ensure_api_write_access
  info "Перезапуск служб панели"
  systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  restart_mihomo_manager_if_present
  verify_app
  ok "Панель перезапущена."
}

start_preferred_ssh() {
  if systemctl is-active --quiet ssh.socket || systemctl is-active --quiet ssh.service; then
    return 0
  fi
  if systemctl is-enabled --quiet ssh.socket 2>/dev/null; then
    systemctl start ssh.socket
  else
    systemctl start ssh.service
  fi
}

prepare_update_ssh() {
  if ! systemctl is-active --quiet ssh.service && ! systemctl is-active --quiet ssh.socket; then
    info "Временный запуск SSH на время обновления"
    start_preferred_ssh
    SSH_TEMP_STARTED="yes"
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    if ! ufw status | grep -Eq '(^|[[:space:]])(22/tcp|OpenSSH)[[:space:]]+ALLOW'; then
      ufw allow OpenSSH
      SSH_TEMP_RULE="yes"
    fi
  fi
}

restore_update_ssh() {
  if [[ "${SSH_TEMP_RULE}" == "yes" ]]; then
    ufw delete allow OpenSSH >/dev/null 2>&1 || true
    SSH_TEMP_RULE="no"
  fi
  if [[ "${SSH_TEMP_STARTED}" == "yes" ]]; then
    systemctl stop ssh.socket ssh.service >/dev/null 2>&1 || true
    SSH_TEMP_STARTED="no"
  fi
}

install_prebuilt_release() {
  local archive="${2:-}" preserve_previous="${3:-no}" archive_path archive_listing stage_root payload rollback requirements_hash installed_requirements_hash build_commit candidate_python venv_entry first_line legacy_runtime="no" requirements_changed="no"
  [[ -n "${archive}" ]] || die "укажите путь к подготовленному vps-control-release.tar.gz."
  archive_path="$(readlink -f -- "${archive}")"
  [[ -f "${archive_path}" ]] || die "архив релиза не найден: ${archive}."
  archive_listing="$(tar -tzf "${archive_path}")"
  grep -Eq '^vps-control-release/(\.prebuilt-release|release\.sha256)$' <<<"${archive_listing}" \
    || die "архив не является подготовленным релизом 312.net."
  if grep -Eq '(^|/)\.\.(/|$)|^/' <<<"${archive_listing}"; then
    die "архив содержит небезопасные пути."
  fi

  stage_root="$(mktemp -d /opt/vps-control.release.XXXXXX)"
  tar -xzf "${archive_path}" -C "${stage_root}" --no-same-owner
  payload="${stage_root}/vps-control-release"
  (
    cd "${payload}"
    sha256sum -c release.sha256 >/dev/null
  ) || { rm -rf -- "${stage_root}"; die "контрольные суммы подготовленного релиза не совпали."; }
  [[ -x "${payload}/node_modules/.bin/vinext" && -f "${payload}/dist/server/index.js" && -f "${payload}/api/main.py" ]] \
    || { rm -rf -- "${stage_root}"; die "в архиве отсутствует готовая web/API-сборка."; }

  requirements_hash="$(sha256sum "${payload}/api/requirements.txt" | awk '{print $1}')"
  installed_requirements_hash="$(cat "${INSTALL_DIR}/venv/.requirements.sha256" 2>/dev/null || true)"
  candidate_python="${INSTALL_DIR}/venv/bin/python"
  if [[ -z "${installed_requirements_hash}" || "${requirements_hash}" != "${installed_requirements_hash}" ]]; then
    requirements_changed="yes"
    [[ -x "${candidate_python}" ]] \
      || { rm -rf -- "${stage_root}"; die "установленное Python-окружение повреждено; выполните полную установку."; }
    info "Подготовка обновлённых Python-зависимостей в отдельном окружении"
    cp -a -- "${INSTALL_DIR}/venv" "${payload}/venv"
    candidate_python="${payload}/venv/bin/python"
    run_with_status "Подготовка Python-зависимостей релиза" \
      timeout --signal=TERM --kill-after=15s "${DEPENDENCY_INSTALL_TIMEOUT}" \
        "${candidate_python}" -m pip install --disable-pip-version-check \
        -r "${payload}/api/requirements.txt" \
      || { rm -rf -- "${stage_root}"; die "не удалось подготовить Python-зависимости нового релиза."; }
    printf '%s\n' "${requirements_hash}" >"${payload}/venv/.requirements.sha256"
  fi

  # Import the candidate API with the installed production dependencies before
  # replacing the working tree. This catches Pydantic schema and other module
  # initialization errors without interrupting the running release.
  PYTHONPATH="${payload}" "${candidate_python}" -c 'import api.main' >/dev/null \
    || { rm -rf -- "${stage_root}"; die "API нового релиза не проходит проверку импорта; обновление отменено до остановки служб."; }

  if [[ "${requirements_changed}" == "yes" ]]; then
    while IFS= read -r -d '' venv_entry; do
      IFS= read -r first_line <"${venv_entry}" || true
      if [[ "${first_line}" == "#!${payload}/venv/"* ]]; then
        sed -i "1s|^#!${payload}/venv/|#!${INSTALL_DIR}/venv/|" "${venv_entry}"
      fi
    done < <(find "${payload}/venv/bin" -maxdepth 1 -type f -print0)
  fi

  rollback="${INSTALL_DIR}.rollback.$(date -u +%Y%m%dT%H%M%SZ)"
  UPDATE_ROLLBACK_DIR="${rollback}"
  UPDATE_SWAP_ACTIVE="yes"
  info "Установка заранее собранного релиза без Docker и сборки на VPS"
  if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '^vps-control-(web|gateway)-1$'; then
    legacy_runtime="yes"
    stop_legacy_containers
  fi
  systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
  mv -- "${INSTALL_DIR}" "${rollback}"
  mv -- "${payload}" "${INSTALL_DIR}"
  if [[ "${requirements_changed}" == "no" ]]; then
    mv -- "${rollback}/venv" "${INSTALL_DIR}/venv"
  fi
  chmod 0755 "${INSTALL_DIR}" "${INSTALL_DIR}/scripts/vps-control.sh"
  PROJECT_DIR="${INSTALL_DIR}"
  write_caddy_config

  if ! install_api \
    || ! install_web \
    || ! ensure_api_write_access \
    || ! ensure_mihomo_profile_runtimes \
    || ! grep -Eq '^ReadWritePaths=.*-?/etc/vps-control([[:space:]]|$)' "${SERVICE_FILE}" \
    || ! build_commit="$(awk -F= '$1 == "commit" {print $2}' "${INSTALL_DIR}/.prebuilt-release")" \
    || ! printf '%s\n' "${build_commit:-manual}" >"${INSTALL_DIR}/.build-commit" \
    || ! write_integrity_manifest \
    || ! systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! restart_mihomo_manager_if_present \
    || ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 \
      "http://127.0.0.1:8000/api/health" >/dev/null \
    || ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 \
      "http://127.0.0.1:3000/" >/dev/null; then
    warn "новый релиз не прошёл проверку; выполняется откат."
    systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
    if [[ -d "${INSTALL_DIR}/venv" && ! -e "${rollback}/venv" ]]; then
      mv -- "${INSTALL_DIR}/venv" "${rollback}/venv"
    fi
    rm -rf -- "${INSTALL_DIR}"
    mv -- "${rollback}" "${INSTALL_DIR}"
    PROJECT_DIR="${INSTALL_DIR}"
    write_caddy_config
    if [[ "${legacy_runtime}" == "yes" ]]; then
      systemctl stop "${APP_NAME}-web.service" caddy.service 2>/dev/null || true
      start_legacy_containers
      systemctl restart "${APP_NAME}-api.service"
    else
      systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
    fi
    restart_mihomo_manager_if_present
    rm -rf -- "${stage_root}"
    die "подготовленный релиз отклонён; предыдущая версия восстановлена."
  fi

  if [[ "${preserve_previous}" == "yes" ]]; then
    [[ "${TEST_BACKUP_DIR}" == "${DATA_DIR}/test-app-backup" ]] || die "небезопасный путь резервной версии."
    rm -rf -- "${TEST_BACKUP_DIR}"
    mv -- "${rollback}" "${TEST_BACKUP_DIR}"
  else
    rm -rf -- "${rollback}"
  fi
  UPDATE_SWAP_ACTIVE="no"
  UPDATE_ROLLBACK_DIR=""
  rm -rf -- "${stage_root}"
  cleanup_legacy_runtime
  install -m 0755 "${INSTALL_DIR}/scripts/vps-control.sh" "${COMMAND_PATH}"
  ok "подготовленный релиз установлен; WG/AWG и системные пакеты не изменялись."
}

update_prebuilt_branch() {
  local branch="$1" release_tag="$2"
  local remote="${REMOTE_URL:-https://github.com/aske312/vpsController.git}"
  local latest current repository_path release_url archive release_commit release_revision ready attempt

  if [[ "${remote}" =~ ^git@github\.com:(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  elif [[ "${remote}" =~ ^ssh://git@github\.com/(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  fi
  [[ "${branch}" == "stabl" ]] || die "готовые релизы публикуются только из ветки stabl."
  latest="$(git ls-remote "${remote}" "refs/heads/${branch}" 2>/dev/null | awk 'NR == 1 {print $1}')"
  [[ "${latest}" =~ ^[0-9a-f]{40}$ ]] || die "не удалось получить актуальную ревизию ветки ${branch}."
  current="$(cat "${INSTALL_DIR}/.build-commit" 2>/dev/null || true)"
  if [[ -n "${current}" && "${latest}" == "${current}"* ]]; then
    ok "установлена актуальная версия ветки ${branch} (${current})."
    return 0
  fi

  ready="no"
  for attempt in $(seq 1 "${STABL_RELEASE_WAIT_ATTEMPTS}"); do
    release_revision="$(git ls-remote "${remote}" "refs/tags/${release_tag}^{}" 2>/dev/null | awk 'NR == 1 {print $1}')"
    if [[ -z "${release_revision}" ]]; then
      release_revision="$(git ls-remote "${remote}" "refs/tags/${release_tag}" 2>/dev/null | awk 'NR == 1 {print $1}')"
    fi
    if [[ "${release_revision}" == "${latest}" ]]; then
      ready="yes"
      break
    fi
    if (( attempt == 1 || attempt % 6 == 0 )); then
      info "GitHub готовит релиз ${branch} ${latest:0:7}; ожидание публикации"
    fi
    sleep 10
  done
  [[ "${ready}" == "yes" ]] \
    || die "релиз для актуальной версии ${branch} не опубликован; проверьте GitHub Actions."

  release_url="${APP_RELEASE_URL:-}"
  if [[ -z "${release_url}" && "${remote}" =~ ^https://github\.com/([^/]+/[^/]+)$ ]]; then
    repository_path="${BASH_REMATCH[1]%.git}"
    release_url="https://github.com/${repository_path}/releases/download/${release_tag}/vps-control-release.tar.gz"
  fi
  [[ "${release_url}" =~ ^https:// ]] || die "не настроен HTTPS-адрес подготовленного релиза ${branch}."

  install -d -m 0750 "${DATA_DIR}/tmp"
  UPDATE_TEMP_DIR="$(mktemp -d "${DATA_DIR}/tmp/update.XXXXXX")"
  archive="${UPDATE_TEMP_DIR}/vps-control-release.tar.gz"
  info "Загрузка подготовленного релиза ветки ${branch}"
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --connect-timeout 15 --max-time "${UPDATE_DOWNLOAD_TIMEOUT}" --output "${archive}" "${release_url}"
  release_commit="$(tar -xOf "${archive}" vps-control-release/.prebuilt-release 2>/dev/null \
    | awk -F= '$1 == "commit" {print $2}')"
  [[ "${release_commit}" =~ ^[0-9a-f]{7,40}$ && "${latest}" == "${release_commit}"* ]] \
    || die "подготовленный релиз не соответствует актуальной ревизии ветки ${branch}."

  install_prebuilt_release install-release "${archive}"
  ok "приложение обновлено до ${branch} ${release_commit}."
  write_action_status "succeeded" 100 "Обновление установлено и проверено"
}

update_test_branch() {
  local remote="${REMOTE_URL:-https://github.com/aske312/vpsController.git}"
  local latest current repository_path release_url archive release_commit release_revision ready="no" attempt
  if [[ "${remote}" =~ ^git@github\.com:(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  elif [[ "${remote}" =~ ^ssh://git@github\.com/(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  fi
  [[ "${remote}" =~ ^https://github\.com/([^/]+/[^/]+)$ ]] \
    || die "тестовая ветка main должна находиться в GitHub-репозитории по HTTPS."
  repository_path="${BASH_REMATCH[1]%.git}"
  latest="$(git ls-remote "${remote}" refs/heads/main 2>/dev/null | awk 'NR == 1 {print $1}')"
  [[ "${latest}" =~ ^[0-9a-f]{40}$ ]] || die "не удалось получить актуальную ревизию ветки main."
  current="$(cat "${INSTALL_DIR}/.build-commit" 2>/dev/null || true)"
  if [[ -n "${current}" && "${latest}" == "${current}"* ]]; then
    ok "установлена актуальная тестовая версия main (${current})."
    return 0
  fi

  release_url="https://github.com/${repository_path}/releases/download/main-latest/vps-control-main.tar.gz"
  info "ожидание подготовленной GitHub-сборки main ${latest:0:7}; рабочая версия продолжает обслуживать запросы"
  for attempt in $(seq 1 "${MAIN_RELEASE_WAIT_ATTEMPTS}"); do
    release_revision="$(git ls-remote "${remote}" 'refs/tags/main-latest^{}' 2>/dev/null | awk 'NR == 1 {print $1}')"
    [[ -n "${release_revision}" ]] || release_revision="$(git ls-remote "${remote}" refs/tags/main-latest 2>/dev/null | awk 'NR == 1 {print $1}')"
    if [[ "${release_revision}" == "${latest}" ]] && curl --fail --location --silent --show-error --range 0-0 \
      --connect-timeout 10 --max-time 30 --output /dev/null "${release_url}" 2>/dev/null; then
      ready="yes"
      break
    fi
    sleep 10
  done
  [[ "${ready}" == "yes" ]] \
    || die "подготовленная сборка main ${latest:0:7} не опубликована; рабочая версия не изменена."

  install -d -m 0750 "${DATA_DIR}/tmp"
  UPDATE_TEMP_DIR="$(mktemp -d "${DATA_DIR}/tmp/update.XXXXXX")"
  archive="${UPDATE_TEMP_DIR}/vps-control-main.tar.gz"
  info "загрузка готовой тестовой сборки main без сборки на VPS"
  curl --fail --location --silent --show-error --retry 4 --retry-all-errors --retry-delay 2 \
    --connect-timeout 15 --max-time "${UPDATE_DOWNLOAD_TIMEOUT}" --output "${archive}" "${release_url}"
  release_commit="$(tar -xOf "${archive}" vps-control-release/.prebuilt-release 2>/dev/null \
    | awk -F= '$1 == "commit" {print $2}')"
  [[ "${release_commit}" == "${latest}" ]] \
    || die "подготовленная сборка не соответствует main ${latest}; рабочая версия не изменена."
  if [[ -d "${TEST_BACKUP_DIR}" ]]; then
    install_prebuilt_release install-release "${archive}"
  else
    install_prebuilt_release install-release "${archive}" yes
  fi
  rm -f "${DATA_DIR}/application-version.json"
  ok "подготовленная тестовая ветка main ${latest:0:7} установлена с автоматическим откатом при ошибке."
  write_action_status "succeeded" 100 "Тестовое обновление установлено и проверено"
}

update_app() {
  update_prebuilt_branch "${PRODUCTION_BRANCH}" "stabl-latest"
}

update_test_app() {
  [[ -r "${SERVICE_MODE_FILE}" ]] || die "переход на тестовую версию разрешён только в сервисном режиме."
  update_test_branch
}

restore_test_app() {
  local failed_install
  [[ -r "${SERVICE_MODE_FILE}" ]] || die "возврат с тестовой версии разрешён только в сервисном режиме."
  [[ -d "${TEST_BACKUP_DIR}" ]] || die "сохранённая рабочая версия приложения не найдена."
  failed_install="${INSTALL_DIR}.failed-test.$(date -u +%Y%m%dT%H%M%SZ)"
  systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service"
  mv -- "${INSTALL_DIR}" "${failed_install}"
  mv -- "${TEST_BACKUP_DIR}" "${INSTALL_DIR}"
  if [[ -d "${failed_install}/venv" && ! -e "${INSTALL_DIR}/venv" ]]; then
    mv -- "${failed_install}/venv" "${INSTALL_DIR}/venv"
  fi
  PROJECT_DIR="${INSTALL_DIR}"
  if ! install_api || ! install_web || ! ensure_api_write_access || ! ensure_mihomo_profile_runtimes \
    || ! systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! restart_mihomo_manager_if_present \
    || ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 "http://127.0.0.1:8000/api/health" >/dev/null \
    || ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 "http://127.0.0.1:3000/" >/dev/null; then
    warn "сохранённая версия не запустилась; тестовая версия восстанавливается."
    systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
    if [[ -d "${INSTALL_DIR}/venv" && ! -e "${failed_install}/venv" ]]; then
      mv -- "${INSTALL_DIR}/venv" "${failed_install}/venv"
    fi
    mv -- "${INSTALL_DIR}" "${TEST_BACKUP_DIR}"
    mv -- "${failed_install}" "${INSTALL_DIR}"
    systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
    restart_mihomo_manager_if_present
    die "возврат отклонён; тестовая версия продолжает работать."
  fi
  [[ "${failed_install}" == "${INSTALL_DIR}.failed-test."* ]] || die "небезопасный путь очистки тестовой версии."
  rm -rf -- "${failed_install}"
  install -m 0755 "${INSTALL_DIR}/scripts/vps-control.sh" "${COMMAND_PATH}"
  rm -f "${DATA_DIR}/application-version.json"
  ok "рабочая версия приложения восстановлена; WG/AWG, клиенты и модули не изменялись."
}

change_access_mode() {
  local requested="${2:-}"
  [[ "${requested}" == "external" || "${requested}" == "vpn" ]] || die "режим должен быть external или vpn."
  ACCESS_MODE="${requested}"
  set_config_value "${INSTALL_CONFIG}" "ACCESS_MODE" "${ACCESS_MODE}"
  configure_access
  configure_firewall "panel-only"
  write_caddy_config
  caddy validate --config "${CADDY_CONFIG}" >/dev/null
  systemctl restart caddy.service
  systemctl restart "${APP_NAME}-api.service"
  if [[ "${ACCESS_MODE}" == "vpn" ]]; then
    ok "панель доступна через защищённые подключения: http://${INTERNAL_PANEL_HOST}"
  else
    ok "публичный доступ к панели открыт."
  fi
}

change_service_mode() {
  local requested="${2:-}" previous_access ssh_service_active ssh_socket_active ssh_public active_timers
  [[ "${requested}" == "enable" || "${requested}" == "disable" ]] || die "режим должен быть enable или disable."
  if [[ "${requested}" == "enable" ]]; then
    [[ ! -f "${SERVICE_MODE_FILE}" ]] || { warn "сервисный режим уже включён."; return; }
    previous_access="${ACCESS_MODE}"
    ssh_service_active="$([[ "$(systemctl is-active ssh.service)" == "active" ]] && printf yes || printf no)"
    ssh_socket_active="$([[ "$(systemctl is-active ssh.socket)" == "active" ]] && printf yes || printf no)"
    ssh_public="$([[ "$(ufw status | grep -Ec '^OpenSSH[[:space:]]+ALLOW[[:space:]]+Anywhere([[:space:]]|$)')" -gt 0 ]] && printf yes || printf no)"
    active_timers=""
    for timer in vpn-monitor.timer vps-control-auto-reboot.timer vps-control-auto-cleanup.timer vps-control-auto-update.timer apt-daily.timer apt-daily-upgrade.timer; do
      if systemctl is-active --quiet "${timer}"; then
        active_timers+="${timer},"
        systemctl stop "${timer}"
      fi
    done
    install -d -m 0750 "${DATA_DIR}"
    python3 - "${SERVICE_MODE_FILE}" "${previous_access}" "${ssh_service_active}" "${ssh_socket_active}" "${ssh_public}" "${active_timers%,}" <<'PY'
import json, os, sys
from datetime import datetime, timezone
path, access, ssh_service, ssh_socket, ssh_public, timers = sys.argv[1:]
with open(path, "w", encoding="utf-8") as stream:
    json.dump({"active": True, "previous_access": access,
               "ssh_service_was_active": ssh_service == "yes",
               "ssh_socket_was_active": ssh_socket == "yes",
               "ssh_public_was_allowed": ssh_public == "yes",
               "timers": [item for item in timers.split(",") if item],
               "enabled_at": datetime.now(timezone.utc).isoformat()}, stream)
os.chmod(path, 0o600)
PY
    start_preferred_ssh
    ufw allow OpenSSH
    change_access_mode "$1" external
    ok "сервисный режим включён; версия приложения не изменена."
  else
    [[ -r "${SERVICE_MODE_FILE}" ]] || { warn "сервисный режим уже выключен."; return; }
    if [[ -d "${TEST_BACKUP_DIR}" ]]; then
      info "возврат к сохранённой стабильной версии перед выключением сервисного режима"
      restore_test_app
    fi
    readarray -t saved < <(python3 - "${SERVICE_MODE_FILE}" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("previous_access", "external"))
legacy_ssh = data.get("ssh_was_active", True)
print("yes" if data.get("ssh_service_was_active", legacy_ssh) else "no")
print("yes" if data.get("ssh_socket_was_active", legacy_ssh) else "no")
print("yes" if data.get("ssh_public_was_allowed") else "no")
print(",".join(data.get("timers", [])))
PY
)
    previous_access="${saved[0]:-external}"
    ssh_service_active="${saved[1]:-yes}"
    ssh_socket_active="${saved[2]:-yes}"
    ssh_public="${saved[3]:-yes}"
    IFS=',' read -ra timers <<<"${saved[4]:-}"
    for timer in "${timers[@]}"; do [[ -z "${timer}" ]] || systemctl start "${timer}" || true; done
    rm -f "${SERVICE_MODE_FILE}"
    rm -f "${DATA_DIR}/application-version.json"
    change_access_mode "$1" "${previous_access}"
    [[ "${ssh_public}" == "yes" ]] || ufw delete allow OpenSSH >/dev/null 2>&1 || true
    [[ "${ssh_service_active}" == "yes" ]] || systemctl stop ssh.service
    [[ "${ssh_socket_active}" == "yes" ]] || systemctl stop ssh.socket
    ok "сервисный режим выключен; исходные состояния восстановлены."
  fi
}

update_kernel() {
  info "Проверка обновления ядра Ubuntu"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  local packages=()
  for package in linux-virtual linux-generic linux-image-virtual linux-headers-virtual linux-image-generic linux-headers-generic; do
    if dpkg-query -W -f='${db:Status-Abbrev}' "${package}" 2>/dev/null | grep -q '^ii'; then
      packages+=("${package}")
    fi
  done
  ((${#packages[@]})) || die "не найден поддерживаемый метапакет ядра Ubuntu."
  if apt-get -s install --only-upgrade "${packages[@]}" 2>/dev/null | grep -q '^Inst '; then
    REBOOT_AFTER_UPDATE="yes"
  fi
  apt-get install -y --only-upgrade "${packages[@]}"
  ok "Пакеты ядра проверены и обновлены; при наличии нового ядра выполните reboot."
  if [[ "${REBOOT_AFTER_UPDATE}" == "yes" && -z "${CURRENT_ACTION}" ]]; then
    systemctl --no-block --no-wall reboot
  fi
}

optimize_resources() {
  local disk_before disk_after mem_before mem_after log_retention_days=30
  disk_before="$(df -B1 / | awk 'NR==2 {print $4}')"
  mem_before="$(awk '/MemAvailable/ {print $2 * 1024}' /proc/meminfo)"
  info "Безопасная очистка кэшей и неиспользуемых данных"
  apt-get clean
  if [[ -r /etc/vps-control-logging.conf ]]; then
    # shellcheck source=/dev/null
    source /etc/vps-control-logging.conf
    log_retention_days="${LOG_RETENTION_DAYS:-30}"
  fi
  if (( log_retention_days > 0 )); then
    journalctl --vacuum-time="${log_retention_days}d"
  fi
  if [[ -d "${DATA_DIR}/tmp" ]]; then
    find "${DATA_DIR}/tmp" -mindepth 1 -maxdepth 1 -type d -name 'update.*' -mtime +1 -exec rm -rf -- {} +
  fi
  sync
  printf '3\n' >/proc/sys/vm/drop_caches
  disk_after="$(df -B1 / | awk 'NR==2 {print $4}')"
  mem_after="$(awk '/MemAvailable/ {print $2 * 1024}' /proc/meminfo)"
  ok "освобождено на диске: $(((disk_after - disk_before) / 1024 / 1024)) МБ; доступная память: $((mem_before / 1024 / 1024)) → $((mem_after / 1024 / 1024)) МБ."
}

configure_logging() {
  local requested="${2:-}" retention="${3:-30}" persistent
  [[ "${requested}" == "enable" || "${requested}" == "disable" ]] || die "режим записи должен быть enable или disable."
  [[ "${retention}" =~ ^[0-9]+$ ]] && (( retention <= 365 )) || die "срок хранения должен быть от 0 до 365 дней."
  persistent="$([[ "${requested}" == "enable" ]] && printf yes || printf no)"
  cat >/etc/vps-control-logging.conf <<EOF
LOG_PERSISTENT=${persistent}
LOG_RETENTION_DAYS=${retention}
EOF
  chmod 0600 /etc/vps-control-logging.conf
  install -d -m 0755 /etc/systemd/journald.conf.d
  {
    printf '[Journal]\nStorage=%s\nSystemMaxUse=500M\nRuntimeMaxUse=100M\n' \
      "$([[ "${persistent}" == "yes" ]] && printf persistent || printf volatile)"
    if (( retention > 0 )); then
      printf 'MaxRetentionSec=%sday\n' "${retention}"
    fi
  } >/etc/systemd/journald.conf.d/90-vps-control.conf
  if [[ -f /etc/logrotate.d/vps-control-monitor ]]; then
    sed -i -E "s/^[[:space:]]*rotate[[:space:]]+[0-9]+/  rotate $(( retention > 0 ? retention : 10000 ))/" /etc/logrotate.d/vps-control-monitor
  fi
  systemd-analyze cat-config systemd/journald.conf >/dev/null
  systemctl restart systemd-journald
  ok "настройки записи и хранения журналов применены."
}

clear_managed_logs() {
  info "Очистка системных и мониторинговых журналов"
  journalctl --rotate
  journalctl --vacuum-time=1s
  find "${DATA_DIR}/monitor" -maxdepth 1 -type f \( -name '*.csv' -o -name '*.log' -o -name '*.gz' \) -delete 2>/dev/null || true
  ok "управляемые журналы очищены."
}

apply_automation() {
  info "Применение расписаний обслуживания"
  [[ -r "${AUTOMATION_FILE}" ]] || die "не найден ${AUTOMATION_FILE}."
  local values reboot_enabled reboot_cadence reboot_weekday reboot_hour reboot_minute
  local cleanup_enabled cleanup_cadence cleanup_weekday cleanup_hour cleanup_minute
  local update_enabled update_cadence update_weekday update_hour update_minute
  values="$(python3 - "${AUTOMATION_FILE}" <<'PY'
import json
import shlex
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
for section in ("reboot", "cleanup", "update"):
    item = data.get(section, {})
    values = (
        "yes" if item.get("enabled") else "no",
        str(item.get("cadence", "weekly")),
        str(item.get("weekday", "Sun")),
        str(int(item.get("hour", 4))),
        str(int(item.get("minute", 0))),
    )
    print(" ".join(shlex.quote(value) for value in values))
PY
)"
  read -r reboot_enabled reboot_cadence reboot_weekday reboot_hour reboot_minute <<<"$(sed -n '1p' <<<"${values}")"
  read -r cleanup_enabled cleanup_cadence cleanup_weekday cleanup_hour cleanup_minute <<<"$(sed -n '2p' <<<"${values}")"
  read -r update_enabled update_cadence update_weekday update_hour update_minute <<<"$(sed -n '3p' <<<"${values}")"
  update_enabled="false"

  automation_calendar() {
    local cadence="$1" weekday="$2" hour="$3" minute="$4"
    [[ "${hour}" =~ ^([0-9]|1[0-9]|2[0-3])$ && "${minute}" =~ ^([0-9]|[1-5][0-9])$ ]] \
      || die "некорректное время автоматизации."
    case "${cadence}" in
      daily) printf '*-*-* %02d:%02d:00' "${hour}" "${minute}" ;;
      weekly)
        [[ "${weekday}" =~ ^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$ ]] || die "некорректный день недели."
        printf '%s *-*-* %02d:%02d:00' "${weekday}" "${hour}" "${minute}"
        ;;
      monthly) printf '*-*-01 %02d:%02d:00' "${hour}" "${minute}" ;;
      *) die "неподдерживаемая периодичность ${cadence}." ;;
    esac
  }

  install_automation_timer() {
    local id="$1" description="$2" command="$3" enabled="$4" calendar="$5"
    cat >"/etc/systemd/system/vps-control-auto-${id}.service" <<EOF
[Unit]
Description=${description}
After=network-online.target

[Service]
Type=oneshot
ExecStart=${COMMAND_PATH} ${command}
EOF
    cat >"/etc/systemd/system/vps-control-auto-${id}.timer" <<EOF
[Unit]
Description=${description}

[Timer]
OnCalendar=${calendar}
Persistent=true
RandomizedDelaySec=120
Unit=vps-control-auto-${id}.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl disable --now "vps-control-auto-${id}.timer" >/dev/null 2>&1 || true
    if [[ "${enabled}" == "yes" ]]; then
      systemctl enable --now "vps-control-auto-${id}.timer"
    fi
  }

  local reboot_calendar cleanup_calendar update_calendar
  reboot_calendar="$(automation_calendar "${reboot_cadence}" "${reboot_weekday}" "${reboot_hour}" "${reboot_minute}")"
  cleanup_calendar="$(automation_calendar "${cleanup_cadence}" "${cleanup_weekday}" "${cleanup_hour}" "${cleanup_minute}")"
  update_calendar="$(automation_calendar "${update_cadence}" "${update_weekday}" "${update_hour}" "${update_minute}")"
  install_automation_timer "reboot" "Scheduled VPS reboot by 312.net" "reboot" "${reboot_enabled}" "${reboot_calendar}"
  install_automation_timer "cleanup" "Scheduled VPS cleanup by 312.net" "optimize" "${cleanup_enabled}" "${cleanup_calendar}"
  install_automation_timer "update" "Scheduled 312.net application update" "update" "${update_enabled}" "${update_calendar}"
  ok "расписания обслуживания применены."
}

reboot_server() {
  info "Перезагрузка сервера"
  systemctl --no-block --no-wall reboot
}

poweroff_server() {
  info "Выключение сервера"
  systemctl --no-block --no-wall poweroff
}

status_app() {
  printf '\nAPI service:\n'
  systemctl --no-pager --full status "${APP_NAME}-api.service" || true
  printf '\nWeb service:\n'
  systemctl --no-pager --full status "${APP_NAME}-web.service" || true
  printf '\nGateway service:\n'
  systemctl --no-pager --full status caddy.service || true
}

logs_app() {
  if [[ ${2:-} == "api" ]]; then
    exec journalctl -u "${APP_NAME}-api.service" -f
  fi
  if [[ ${2:-} == "web" || ${2:-} == "gateway" ]]; then
    [[ ${2:-} == "web" ]] && exec journalctl -u "${APP_NAME}-web.service" -f
    exec journalctl -u caddy.service -f
  fi
  journalctl -u "${APP_NAME}-api.service" -u "${APP_NAME}-web.service" -u caddy.service -n 200 --no-pager
}

verify_app() {
  configure_access
  info "Проверка установки"
  systemctl is-active --quiet "${APP_NAME}-api.service" || die "API не запущен."
  systemctl is-active --quiet "${APP_NAME}-web.service" || die "веб-служба не запущена."
  systemctl is-active --quiet caddy.service || die "Caddy не запущен."
  curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 \
    http://127.0.0.1:8000/api/health \
    || die "API не отвечает на локальную проверку."
  printf '\n'
  curl --fail --silent --show-error --connect-timeout 10 --max-time 15 \
    --retry 18 --retry-all-errors --retry-delay 5 "${PANEL_URL}/" >/dev/null \
    || die "веб-панель не отвечает."
  ok "установка исправна; веб-интерфейс отвечает."
}

network_check() {
  info "Проверка внешней сети, панели и защищённых туннелей"
  local uplink ping_output loss conntrack_count conntrack_max conntrack_percent counter value
  uplink="$(ip -o -4 route show default | awk 'NR==1 {print $5}')"
  [[ -n "${uplink}" ]] || die "не найден маршрут IPv4 по умолчанию."
  getent ahosts github.com >/dev/null || die "DNS не разрешает внешние адреса."
  curl --fail --silent --show-error --max-time 15 https://www.google.com/generate_204 >/dev/null \
    || die "нет стабильного HTTPS-доступа во внешнюю сеть."
  ping_output="$(ping -n -q -c 5 -i 0.2 -W 1 1.1.1.1 2>/dev/null || true)"
  loss="$(printf '%s\n' "${ping_output}" | sed -nE 's/.* ([0-9]+([.][0-9]+)?)% packet loss.*/\1/p' | head -n 1)"
  [[ -n "${loss}" ]] || loss=100
  if awk -v value="${loss}" 'BEGIN { exit !(value >= 20) }'; then
    die "критические потери пакетов до 1.1.1.1: ${loss}%."
  elif awk -v value="${loss}" 'BEGIN { exit !(value > 0) }'; then
    warn "обнаружены потери пакетов до 1.1.1.1: ${loss}%."
  else
    ok "контрольная серия ICMP: потерь нет."
  fi
  ping -n -c 1 -W 2 -M do -s 1200 1.1.1.1 >/dev/null 2>&1 \
    || warn "не прошёл пакет 1200 байт с DF; возможна проблема Path MTU."
  systemctl is-active --quiet vpn-monitor.timer \
    || warn "таймер расширенного мониторинга vpn-monitor.timer не активен."
  conntrack_count="$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)"
  conntrack_max="$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)"
  if [[ "${conntrack_max}" =~ ^[0-9]+$ && "${conntrack_max}" -gt 0 ]]; then
    conntrack_percent="$((conntrack_count * 100 / conntrack_max))"
    [[ "${conntrack_percent}" -lt 80 ]] \
      || warn "таблица conntrack заполнена на ${conntrack_percent}% (${conntrack_count}/${conntrack_max})."
  fi
  for counter in rx_errors tx_errors rx_dropped tx_dropped; do
    value="$(cat "/sys/class/net/${uplink}/statistics/${counter}" 2>/dev/null || echo 0)"
    [[ "${value}" -eq 0 ]] || warn "${uplink}: ${counter}=${value}."
  done
  verify_app
  local found="no" interface service tool port protocol unit transport settings_file
  for tuple in "wg:${WG_INTERFACE}:wg-quick:${WG_PORT}" "awg:${AWG_INTERFACE}:awg-quick:${AWG_PORT}"; do
    IFS=':' read -r tool interface service port <<<"${tuple}"
    ip link show "${interface}" >/dev/null 2>&1 || continue
    found="yes"
    systemctl is-active --quiet "${service}@${interface}.service" \
      || die "${tool^^}: служба ${service}@${interface} не запущена."
    ip link show dev "${interface}" | grep -qE '<[^>]*UP([,>])' \
      || die "${tool^^}: интерфейс ${interface} не поднят."
    "${tool}" show "${interface}" >/dev/null 2>&1 \
      || die "${tool^^}: не удалось прочитать состояние ${interface}."
    ss -Hlun | grep -Eq "[:.]${port}[[:space:]]" \
      || die "${tool^^}: UDP-порт ${port} не прослушивается."
    ok "${tool^^}: ${interface} работает, UDP ${port} прослушивается."
  done

  check_protocol_unit() {
    local label="$1" check_unit="$2"
    systemctl is-enabled --quiet "${check_unit}" 2>/dev/null || return 0
    found="yes"
    systemctl is-active --quiet "${check_unit}" \
      || die "${label}: установленная служба ${check_unit} не запущена."
    ok "${label}: служба ${check_unit} работает."
  }

  check_protocol_port() {
    local label="$1" check_transport="$2" check_port="$3"
    [[ "${check_port}" =~ ^[0-9]+$ && "${check_port}" -ge 1 && "${check_port}" -le 65535 ]] \
      || die "${label}: не удалось определить порт."
    if [[ "${check_transport}" == "udp" ]]; then
      ss -Hlun | grep -Eq "[:.]${check_port}[[:space:]]" \
        || die "${label}: UDP-порт ${check_port} не прослушивается."
    else
      ss -Hltn | grep -Eq "[:.]${check_port}[[:space:]]" \
        || die "${label}: TCP-порт ${check_port} не прослушивается."
    fi
    ok "${label}: ${check_transport^^} ${check_port} прослушивается."
  }

  json_protocol_value() {
    local path="$1" key="$2" fallback="$3"
    python3 - "${path}" "${key}" "${fallback}" <<'PY'
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding='utf-8')).get(sys.argv[2],sys.argv[3])
except (OSError,ValueError,TypeError):
    value=sys.argv[3]
print(value)
PY
  }

  unit="vps-control-vless-reality-xhttp.service"
  if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    check_protocol_unit "VLESS" "${unit}"
    port="$(sed -n 's/^PORT=//p' /etc/vps-control/vless-reality-xhttp/reality.env 2>/dev/null | tail -n 1)"
    check_protocol_port "VLESS" tcp "${port:-443}"
  fi

  unit="vps-control-shadowsocks.target"
  if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    check_protocol_unit "Shadowsocks" "${unit}"
    while IFS= read -r port; do
      [[ -z "${port}" ]] || check_protocol_port "Shadowsocks" tcp "${port}"
    done < <(python3 - <<'PY'
import glob,json
for path in glob.glob('/etc/vps-control/shadowsocks/clients/*.json'):
    try: print(int(json.load(open(path)).get('server_port',0)))
    except (OSError,ValueError,TypeError): pass
PY
)
  fi

  for protocol in hysteria2 tuic trojan; do
    unit="vps-control-${protocol}.service"
    systemctl is-enabled --quiet "${unit}" 2>/dev/null || continue
    check_protocol_unit "${protocol^^}" "${unit}"
    settings_file="/etc/vps-control/${protocol}/settings.json"
    case "${protocol}" in
      hysteria2) port=8443 ;;
      tuic) port=8444 ;;
      trojan) port=8445 ;;
    esac
    port="$(json_protocol_value "${settings_file}" port "${port}")"
    [[ "${protocol}" == trojan ]] && transport=tcp || transport=udp
    check_protocol_port "${protocol^^}" "${transport}" "${port}"
  done

  unit="vps-control-openvpn.service"
  if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    check_protocol_unit "OpenVPN" "${unit}"
    port="$(json_protocol_value /etc/vps-control/openvpn/settings.json port 1194)"
    transport="$(json_protocol_value /etc/vps-control/openvpn/settings.json protocol udp)"
    check_protocol_port "OpenVPN" "${transport}" "${port}"
  fi

  unit="vps-control-ikev2.service"
  if systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    check_protocol_unit "IKEv2" "${unit}"
    check_protocol_port "IKEv2" udp 500
    check_protocol_port "IKEv2 NAT-T" udp 4500
  fi

  check_protocol_unit "Mihomo Manager" "vps-control-mihomo-manager.service"
  while IFS= read -r unit; do
    [[ -z "${unit}" ]] || check_protocol_unit "Mihomo runtime" "${unit}"
  done < <(systemctl list-unit-files 'vps-control-mihomo-*.service' --state=enabled --no-legend 2>/dev/null | awk '{print $1}' | grep -v '^vps-control-mihomo-manager.service$' || true)

  [[ "${found}" == "yes" ]] || warn "защищённые туннели не установлены; проверена только внешняя сеть и панель."
  ok "сеть, панель и установленные защищённые туннели работают."
}

integrity_check() {
  configure_access
  info "Проверка целостности приложения и системной конфигурации"
  local manifest="${DATA_DIR}/release.sha256" command_mode
  [[ -s "${manifest}" ]] || die "не найден эталонный manifest целостности; выполните обновление приложения."
  sha256sum --check --quiet "${manifest}" || die "обнаружены изменённые или отсутствующие файлы приложения."
  [[ "$(stat -c '%U:%a' "${ENV_FILE}")" == "root:600" ]] \
    || die "${ENV_FILE} должен принадлежать root и иметь права 0600."
  [[ -r "${SERVICE_FILE}" ]] \
    || die "не найден systemd-профиль API ${SERVICE_FILE}."
  grep -Eq '^ReadWritePaths=.*-?/etc/vps-control\.env([[:space:]]|$)' "${SERVICE_FILE}" \
    && grep -Eq '^ReadWritePaths=.*-?/etc/vps-control([[:space:]]|$)' "${SERVICE_FILE}" \
    && grep -Eq '^ReadWritePaths=.*-?/etc/caddy/vps-control\.d([[:space:]]|$)' "${SERVICE_FILE}" \
    || die "systemd-профиль API не разрешает сохранять конфигурацию приложения."
  [[ "$(stat -c '%U' "${COMMAND_PATH}")" == "root" ]] \
    || die "${COMMAND_PATH} должен принадлежать root."
  command_mode="$(stat -c '%a' "${COMMAND_PATH}")"
  (( (8#${command_mode} & 8#022) == 0 )) \
    || die "${COMMAND_PATH} доступен для посторонней записи."
  bash -n "${COMMAND_PATH}" || die "управляющий скрипт содержит синтаксическую ошибку."
  "${INSTALL_DIR}/venv/bin/python" -m py_compile "${INSTALL_DIR}/api/main.py" \
    || die "Python API не проходит синтаксическую проверку."
  [[ -r "${WEB_SERVICE_FILE}" ]] || die "не найден systemd-профиль web ${WEB_SERVICE_FILE}."
  caddy validate --config "${CADDY_CONFIG}" >/dev/null || die "Caddy содержит ошибку конфигурации."
  systemctl is-active --quiet "${APP_NAME}-api.service" || die "API-служба не запущена."
  systemctl is-active --quiet "${APP_NAME}-web.service" || die "web-служба не запущена."
  systemctl is-active --quiet caddy.service || die "Caddy не запущен."
  curl --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null \
    || die "API не отвечает на локальную проверку."
  curl --fail --silent --show-error --max-time 15 "${PANEL_URL}/" >/dev/null \
    || die "веб-панель не отвечает."
  ok "файлы, права, конфигурация и запущенные компоненты соответствуют эталону."
}

show_credentials() {
  [[ -r "${ENV_FILE}" ]] || die "${ENV_FILE} не найден."
  printf 'Логин: '; env_value ADMIN_USER
  printf 'Пароль: '; env_value ADMIN_PASSWORD
}

usage() {
  cat <<'EOF'
312.net — управление инфраструктурой

Использование:
  sudo bash scripts/vps-control.sh install
  sudo vps-control <команда>

Команды:
  install          установить зависимости и развернуть панель
  uninstall --yes  полностью удалить панель, данные и конфигурацию
  doctor           проверить совместимость сервера без изменений
  start            запустить API и веб-панель
  stop             остановить панель
  restart          перезапустить панель
  update           обновить приложение проверенным релизом основной ветки stabl
  test-update      перейти на подготовленную тестовую версию ветки main (только сервисный режим)
  test-rollback    вернуться к версии приложения, сохранённой перед переходом на main
  install-release <архив>
                   вручную установить заранее собранный Linux-релиз без Docker, npm и apt
  status           показать состояние сервисов
  logs [api|web|gateway]
                   показать журналы (для выбранного сервиса — в реальном времени)
  verify           проверить API, веб-панель и привязку порта
  network-check    проверить интернет, панель и все установленные защищённые протоколы
  integrity-check  проверить файлы, права, конфигурацию и компоненты приложения
  identity         повторно определить IP и геолокацию сервера
  secure           установить и включить базовую защиту Ubuntu
  kernel-update    обновить ядро Ubuntu
  vpn-firewall     восстановить маршрутизацию и NAT установленных WG/AWG
  optimize         очистить безопасные кэши и старые журналы
  automation-apply применить сохранённые расписания обслуживания
  logging-config <enable|disable> <0..365>
                   настроить постоянную запись и срок хранения журналов
  logs-clear       очистить системные, контейнерные и мониторинговые журналы
  access-mode <external|vpn>
                   изменить доступность панели
  service-mode <enable|disable>
                   включить или выключить сервисный режим
  reboot           перезагрузить сервер
  poweroff         выключить сервер
  protocol-install <id>
                   установить протокол из образа
  protocol-remove <id>
                   удалить протокол, сохранив образ
  protocol-update <id>
                   обновить протокол до последней официальной версии
  client-firewall <add|delete> <port>
                   изменить правило отдельного Shadowsocks-подключения
  vless-cdn-firewall
                   разрешить HTTPS и проверку сертификата для CDN-маршрута VLESS
  credentials      показать логин и пароль администратора
  help             показать эту справку
EOF
}

main() {
  require_root
  load_manager_config
  load_install_config
  case "${1:-help}" in
    install|install-release|uninstall|doctor|start|stop|restart|update|test-update|test-rollback|verify|network-check|integrity-check|identity|secure|kernel-update|vpn-firewall|vless-cdn-firewall|optimize|automation-apply|logging-config|logs-clear|access-mode|service-mode|reboot|poweroff|protocol-install|protocol-remove|protocol-update|ssh-key-add|ssh-key-reset|ssh-access-begin|ssh-access-confirm|ssh-access-rollback)
      case "${1}" in
        protocol-install|protocol-remove|protocol-update) begin_operation "${1}${2:+:${2}}" ;;
        *) begin_operation "${1}" ;;
      esac
      trap handle_exit EXIT
      trap 'exit 124' TERM INT
      ;;
  esac
  case "${1:-help}" in
    install)
      UI_TOTAL=9
      ui_header
      ui_stage "Проверка сервера"
      doctor
      ui_done "сервер совместим"
      ui_stage "Обновление Ubuntu/Debian"
      update_platform
      ui_done "операционная система обновлена"
      ui_stage "Системные зависимости"
      install_packages
      ui_done "зависимости установлены"
      ui_stage "Сетевой доступ панели"
      configure_firewall
      ui_done "правила доступа применены"
      ui_stage "Подготовка источника обновлений"
      save_source_path
      ui_done "ветка stabl назначена источником релизов"
      ui_stage "Развёртывание локальной версии"
      deploy full
      # Identity and the verified domain are known only after deploy creates
      # the environment. Reconcile public ports before ACME/HTTPS verification.
      configure_firewall "panel-only"
      verify_app
      ui_done "локальная версия установлена"
      ui_stage "Режим обновлений"
      ui_done "последующие релизы устанавливаются вручную из готового архива"
      ui_stage "Проверка файлов и служб"
      verify_app
      integrity_check
      ui_done "целостность подтверждена"
      ui_stage "Завершение"
      ui_summary
      printf '\nОткройте: %s\n' "${PANEL_URL}"
      printf '\n\033[1mДанные для входа:\033[0m\n'
      show_credentials
      printf '\n\033[1;33mСохраните пароль сейчас. Позже его можно посмотреть командой: vps-control credentials\033[0m\n'
      if [[ "${REBOOT_AFTER_UPDATE}" == "yes" || -e /var/run/reboot-required ]]; then
        printf '\n\033[1;33m⚠ После обновления ОС рекомендуется перезагрузить VPS: sudo reboot\033[0m\n'
      fi
      ui_done "установка завершена"
      ;;
    uninstall) uninstall_app "$@" ;;
    install-release) install_prebuilt_release "$@" ;;
    doctor) doctor ;;
    start) check_vpn; start_services ;;
    stop) stop_services ;;
    restart) check_vpn; restart_services ;;
    update) update_app ;;
    test-update) update_test_app ;;
    test-rollback) restore_test_app ;;
    status) status_app ;;
    logs) logs_app "$@" ;;
    verify) verify_app ;;
    network-check) network_check ;;
    integrity-check) integrity_check ;;
    identity)
      refresh_server_identity
      systemctl restart "${APP_NAME}-api.service"
      systemctl restart caddy.service
      verify_app
      ;;
    secure) secure_server ;;
    kernel-update) update_kernel ;;
    vpn-firewall) configure_vpn_firewall_policy ;;
    vless-cdn-firewall) configure_vless_cdn_firewall ;;
    optimize) optimize_resources ;;
    ssh-key-add) shift; ssh_access_add_key "$@" ;;
    ssh-key-reset) ssh_access_reset_key ;;
    ssh-access-begin) ssh_access_begin_hardening ;;
    ssh-access-confirm) ssh_access_confirm ;;
    ssh-access-rollback) ssh_access_rollback ;;
    automation-apply) apply_automation ;;
    logging-config) configure_logging "$@" ;;
    logs-clear) clear_managed_logs ;;
    access-mode) change_access_mode "$@" ;;
    service-mode) change_service_mode "$@" ;;
    reboot) reboot_server ;;
    poweroff) poweroff_server ;;
    protocol-install) install_protocol_image "$@" ;;
    protocol-remove) remove_protocol_image "$@" ;;
    protocol-update) update_protocol_image "$@" ;;
    client-firewall) client_firewall "$@" ;;
    credentials) show_credentials ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
