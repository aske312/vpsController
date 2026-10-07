#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="vps-control"
INSTALL_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
PRODUCT_FILE="${DATA_DIR}/product.json"
TEST_BACKUP_DIR="${DATA_DIR}/test-app-backup"
ENV_FILE="/etc/${APP_NAME}.env"
MANAGER_CONFIG="/etc/${APP_NAME}-manager.conf"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}-api.service"
WEB_SERVICE_FILE="/etc/systemd/system/${APP_NAME}-web.service"
CADDY_CONFIG="/etc/caddy/Caddyfile"
CADDY_CONFIG_BACKUP="${CADDY_CONFIG}.vps-control-backup"
COMMAND_PATH="/usr/local/sbin/${APP_NAME}"
INSTALL_CONFIG="/etc/${APP_NAME}-install.conf"
PANEL_URL=""
CADDY_CONFIG_ROLLBACK_READY="no"
CADDY_CONFIG_PREVIOUS_EXISTS="no"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
if [[ "${SCRIPT_DIR}" == "/usr/local/sbin" && -d "${INSTALL_DIR}" ]]; then
  PROJECT_DIR="${INSTALL_DIR}"
fi

ACCESS_MODE="external"
ADMIN_USER="admin"
ADMIN_PASSWORD=""
PUBLIC_DOMAIN=""
LOCAL_ADDRESS=""
LOCAL_CIDR=""
HTTP_PORT="80"
WG_PORT="51820"
AWG_PORT="51822"
HYSTERIA2_PORT="8443"
TUIC_PORT="8444"
XRAY_PORT="8445"
WG_INTERFACE="wg0"
AWG_INTERFACE="awg0"
AWG_MTU="1280"
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
SSH_TEMP_STARTED="no"
SSH_TEMP_RULE="no"
APP_VERSION="v1.0.0"
BUILD_COMMIT="unknown"
PRESERVE_MANAGER="no"
PRODUCT_EDITION="light"
PRODUCTION_BRANCH="light"
PRODUCTION_RELEASE_TAG="light-latest"
TEST_BRANCH="test-light"
TEST_RELEASE_TAG="light-test-latest"
ACTION_FILE="${DATA_DIR}/application-action.json"
AUTOMATION_FILE="${DATA_DIR}/automation.json"
SERVICE_MODE_FILE="${DATA_DIR}/service-mode.json"
UPDATES_FILE="${DATA_DIR}/security-updates.json"
CURRENT_ACTION=""
ACTION_STARTED_AT=""
ACTION_PROGRESS=0
ACTION_FAILURE_MESSAGE=""
REBOOT_AFTER_UPDATE="no"
INSTALL_LOG="/var/log/vps-control-install.log"

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
          write_action_status "rebooting" 100 "Ядро обновлено; сервер перезагружается"
          systemctl --no-block --no-wall reboot
        else
          write_action_status "succeeded" 100 "Установлена актуальная версия ядра"
        fi
        ;;
      *) write_action_status "succeeded" 100 "Операция завершена" ;;
    esac
  else
    write_action_status "failed" "${ACTION_PROGRESS}" "${ACTION_FAILURE_MESSAGE:-Операция завершилась с ошибкой}"
  fi
}

handle_exit() {
  local exit_code="$?"
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
die() { ACTION_FAILURE_MESSAGE="$*"; printf '\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

generate_admin_password() {
  od -An -N18 -tx1 /dev/urandom | tr -d ' \n'
}

system_architecture() {
  case "$(dpkg --print-architecture 2>/dev/null || uname -m)" in
    amd64|x86_64) printf 'amd64\n' ;;
    arm64|aarch64) printf 'arm64\n' ;;
    *) die "неподдерживаемая архитектура сервера." ;;
  esac
}

release_metadata_value() {
  local metadata="$1" key="$2"
  awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "${metadata}"
}

ensure_product_identity() {
  local mode="${1:-write}"
  install -d -m 0750 "${DATA_DIR}"
  python3 - "${PRODUCT_FILE}" "${PRODUCT_EDITION}" "${mode}" <<'PY'
import json, os, sys, tempfile
path, expected, mode = sys.argv[1:]
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as handle:
            actual = json.load(handle).get("edition")
    except (OSError, ValueError, AttributeError) as exc:
        raise SystemExit(f"invalid product identity: {exc}")
    if actual != expected:
        raise SystemExit(f"installed edition is {actual!r}, expected {expected!r}")
    raise SystemExit(0)
if mode == "check":
    raise SystemExit(0)
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix="product.", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"schema": 1, "edition": expected}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

cleanup_update_dir() {
  [[ -n "${UPDATE_TEMP_DIR}" ]] || return 0
  local target base
  target="$(realpath -m -- "${UPDATE_TEMP_DIR}")"
  base="$(realpath -m -- "${DATA_DIR}/tmp")"
  [[ "${target}" == "${base}"/update.* ]] || die "отказ от очистки неожиданного пути ${target}."
  rm -rf -- "${target}"
  UPDATE_TEMP_DIR=""
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "запустите команду от root."
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

valid_public_domain() {
  local domain="$1"
  [[ ${#domain} -le 253 && "${domain}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

load_install_config() {
  local config="${INSTALL_CONFIG}" domain_override="${VPS_CONTROL_PUBLIC_DOMAIN:-}"
  [[ -r "${config}" ]] || config="${PROJECT_DIR}/install.conf"
  if [[ -r "${config}" ]]; then
    # Конфиг принадлежит администратору и содержит только shell-переменные.
    # shellcheck source=/dev/null
    source "${config}"
  fi
  if [[ -n "${domain_override}" ]]; then
    PUBLIC_DOMAIN="${domain_override}"
  fi
  PUBLIC_DOMAIN="${PUBLIC_DOMAIN%.}"
  PUBLIC_DOMAIN="${PUBLIC_DOMAIN,,}"
  # Обновления сохраняют старый /etc/vps-control-install.conf. Дополняем
  # прежний URL координатами в памяти, не перезаписывая настройки владельца.
  if [[ "${GEOLOCATION_FALLBACK_URL}" == "https://ipwho.is/?fields=success,ip,city,country,country_code" ]]; then
    GEOLOCATION_FALLBACK_URL="https://ipwho.is/?fields=success,ip,city,country,country_code,latitude,longitude"
  fi
  [[ "${ACCESS_MODE}" == "external" || "${ACCESS_MODE}" == "local" || "${ACCESS_MODE}" == "vpn" ]] \
    || die "ACCESS_MODE должен быть external, local или vpn."
  [[ "${HTTP_PORT}" =~ ^[0-9]+$ ]] || die "HTTP_PORT должен быть числом."
  if [[ -n "${PUBLIC_DOMAIN}" ]]; then
    valid_public_domain "${PUBLIC_DOMAIN}" \
      || die "PUBLIC_DOMAIN должен содержать корректное доменное имя без схемы и пути."
  fi
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

domain_points_to_public_ip() {
  local domain="$1" public_ip="$2"
  python3 - "${domain}" "${public_ip}" <<'PY'
import ipaddress
import socket
import sys

domain, expected = sys.argv[1:]
try:
    expected_ip = ipaddress.ip_address(expected)
    addresses = {
        ipaddress.ip_address(item[4][0])
        for item in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
    }
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if expected_ip in addresses else 1)
PY
}

configure_access() {
  local public_ip confirmed_domain=""
  public_ip="$(env_value PUBLIC_IP)"
  if [[ "${ACCESS_MODE}" == "local" ]]; then
    detect_local_network
    set_env_value "PANEL_HOST" "${LOCAL_ADDRESS}"
    set_env_value "PUBLIC_DOMAIN" ""
    set_env_value "CORS_ORIGINS" "http://${LOCAL_ADDRESS}:${HTTP_PORT}"
    PANEL_URL="http://${LOCAL_ADDRESS}:${HTTP_PORT}"
  elif [[ "${ACCESS_MODE}" == "vpn" ]]; then
    local wg_address awg_address
    wg_address="$({ ip -o -4 addr show dev "${WG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    awg_address="$({ ip -o -4 addr show dev "${AWG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    [[ -n "${wg_address}" || -n "${awg_address}" ]] || die "у WG/AWG нет IPv4-адресов для доступа к панели."
    local vpn_origins=""
    [[ -z "${wg_address}" ]] || vpn_origins="http://${wg_address}:${HTTP_PORT}"
    if [[ -n "${awg_address}" ]]; then
      [[ -z "${vpn_origins}" ]] || vpn_origins+=","
      vpn_origins+="http://${awg_address}:${HTTP_PORT}"
    fi
    set_env_value "PANEL_HOST" "0.0.0.0"
    set_env_value "PUBLIC_DOMAIN" ""
    set_env_value "CORS_ORIGINS" "${vpn_origins}"
    PANEL_URL="${vpn_origins%%,*}"
  else
    local wg_address awg_address origins
    wg_address="$({ ip -o -4 addr show dev "${WG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    awg_address="$({ ip -o -4 addr show dev "${AWG_INTERFACE}" 2>/dev/null || true; } | awk 'NR==1 {split($4,a,"/"); print a[1]}')"
    origins="http://${public_ip}:${HTTP_PORT}"
    [[ -z "${wg_address}" ]] || origins+=",http://${wg_address}:${HTTP_PORT}"
    [[ -z "${awg_address}" ]] || origins+=",http://${awg_address}:${HTTP_PORT}"
    set_env_value "PANEL_HOST" "0.0.0.0"
    if [[ -n "${PUBLIC_DOMAIN}" ]]; then
      if domain_points_to_public_ip "${PUBLIC_DOMAIN}" "${public_ip}"; then
        confirmed_domain="${PUBLIC_DOMAIN}"
        origins="https://${confirmed_domain},${origins}"
      else
        warn "домен ${PUBLIC_DOMAIN} пока не указывает на ${public_ip}; панель продолжит работать по IP. После обновления DNS выполните vps-control identity."
      fi
    fi
    set_env_value "PUBLIC_DOMAIN" "${confirmed_domain}"
    set_env_value "CORS_ORIGINS" "${origins}"
    if [[ -n "${confirmed_domain}" ]]; then
      PANEL_URL="https://${confirmed_domain}"
    else
      PANEL_URL="http://${public_ip}:${HTTP_PORT}"
    fi
  fi
  set_env_value "HTTP_PORT" "${HTTP_PORT}"
  set_env_value "ACCESS_MODE" "${ACCESS_MODE}"
  set_env_value "WG_PORT" "${WG_PORT}"
  set_env_value "AWG_PORT" "${AWG_PORT}"
  set_env_value "HYSTERIA2_PORT" "${HYSTERIA2_PORT}"
  set_env_value "TUIC_PORT" "${TUIC_PORT}"
  set_env_value "XRAY_PORT" "${XRAY_PORT}"
  set_env_value "WG_INTERFACE" "${WG_INTERFACE}"
  set_env_value "AWG_INTERFACE" "${AWG_INTERFACE}"
  set_env_value "AWG_MTU" "${AWG_MTU}"
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

env_value() {
  sed -n "s/^${1}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1
}

set_env_value() {
  local key="$1" value="$2" encoded escaped
  encoded="${value}"
  if [[ "${encoded}" =~ [[:space:]] ]]; then
    encoded="${encoded//\\/\\\\}"
    encoded="${encoded//\"/\\\"}"
    encoded="${encoded//\$/\\\$}"
    encoded="${encoded//\`/\\\`}"
    encoded="\"${encoded}\""
  fi
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
  local public_ip city country country_code agreement override_city override_country override_country_code
  info "Определение публичного IP и локации"
  install -d -m 0750 "${DATA_DIR}/tmp"
  rm -f -- "${geo_file}.primary.json" "${geo_file}.fallback.json" "${geo_file}.tertiary.json" \
    "${geo_file}.quaternary.json" "${geo_file}.quinary.json" "${geo_file}.senary.json" "${geo_file}.result"
  curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 \
    "${GEOLOCATION_PRIMARY_URL}" >"${geo_file}.primary.json" || rm -f -- "${geo_file}.primary.json"
  curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 \
    "${GEOLOCATION_FALLBACK_URL}" >"${geo_file}.fallback.json" || rm -f -- "${geo_file}.fallback.json"
  public_ip="$(python3 - "${geo_file}.primary.json" "${geo_file}.fallback.json" <<'PY'
import json
import sys
for path in sys.argv[1:]:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    value = str(data.get("ip") or data.get("ipAddress") or "")
    if value:
        print(value)
        break
PY
  )"
  if [[ ! "${public_ip}" =~ ^[0-9a-fA-F:.]+$ ]]; then
    public_ip="$(curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 \
      "${PUBLIC_IP_DISCOVERY_URL}" 2>/dev/null || true)"
  fi
  if [[ -n "${public_ip}" ]]; then
    curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 \
      "${GEOLOCATION_TERTIARY_URL}/${public_ip}" >"${geo_file}.tertiary.json" || rm -f -- "${geo_file}.tertiary.json"
    curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_QUATERNARY_URL}/${public_ip}/json/" >"${geo_file}.quaternary.json" || rm -f -- "${geo_file}.quaternary.json"
    curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_QUINARY_URL}/${public_ip}" >"${geo_file}.quinary.json" || rm -f -- "${geo_file}.quinary.json"
    curl -4 --fail --silent --show-error --retry 1 --retry-all-errors --max-time 12 -H "Accept: application/json" \
      "${GEOLOCATION_SENARY_URL}/${public_ip}/json" >"${geo_file}.senary.json" || rm -f -- "${geo_file}.senary.json"
  fi
  if python3 "${PROJECT_DIR}/scripts/resolve-geolocation.py" \
    "${geo_file}.primary.json" "${geo_file}.fallback.json" "${geo_file}.tertiary.json" \
    "${geo_file}.quaternary.json" "${geo_file}.quinary.json" "${geo_file}.senary.json" >"${geo_file}.result"; then
    readarray -t geo <"${geo_file}.result"
    public_ip="${geo[0]:-}"
    city="${geo[1]:-Unknown}"
    country="${geo[2]:-Unknown}"
    country_code="${geo[3]:-}"
    agreement="${geo[4]:-}"
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
      else
        ok "локация подтверждена независимыми источниками (${agreement}): ${city}, ${country} (${public_ip})."
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

check_os() {
  local ID="" VERSION_ID="" PRETTY_NAME=""
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
  fi
  case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04|debian:13) ;;
    *) warn "${PRETTY_NAME:-Неизвестная ОС} не проходила проверку совместимости; продолжаем с проверкой зависимостей apt/dpkg и systemd." ;;
  esac
}

doctor() {
  check_os
  local failed=0 memory_kb disk_kb architecture
  command -v apt-get >/dev/null && command -v dpkg >/dev/null \
    && ok "apt/dpkg доступны" || { warn "для установки зависимостей необходимы apt-get и dpkg"; failed=1; }
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

node_runtime_supported() {
  node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 13) ? 0 : 1)' >/dev/null 2>&1
}

install_packages() {
  info "Установка системных зависимостей"
  export DEBIAN_FRONTEND=noninteractive
  install -d -m 0750 "$(dirname -- "${INSTALL_LOG}")"
  prepare_package_manager
  run_with_status "Загрузка списка пакетов" apt-get -o DPkg::Lock::Timeout=300 update
  run_with_status "Установка системных зависимостей" apt-get -o DPkg::Lock::Timeout=300 install -y auditd build-essential ca-certificates caddy curl fail2ban git iproute2 iputils-ping openssh-server openssl procps python3 python3-venv rsync tar ufw unattended-upgrades
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 || ! node_runtime_supported; then
    run_with_status "Подключение Node.js 22" bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
    run_with_status "Установка Node.js 22" apt-get -o DPkg::Lock::Timeout=300 install -y nodejs
  fi
}

secure_server() {
  info "Настройка защиты системы"
  apt-get update
  apt-get install -y auditd fail2ban unattended-upgrades
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
  systemctl enable --now fail2ban
  systemctl restart fail2ban
  systemctl enable --now unattended-upgrades
  systemctl enable --now auditd
  install -d -m 0755 /etc/sysctl.d /etc/ssh/sshd_config.d
  cat >/etc/sysctl.d/99-vps-control-routing.conf <<'EOF'
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
kernel.dmesg_restrict = 1
EOF
  sysctl --system >/dev/null 2>&1 || true
  local ssh_config="/etc/ssh/sshd_config.d/99-vps-control-tunnels.conf"
  local ssh_backup
  ssh_backup="$(mktemp)"
  local ssh_config_existed=0
  if [[ -f "${ssh_config}" ]]; then
    cp -a "${ssh_config}" "${ssh_backup}"
    ssh_config_existed=1
  fi
  cat >"${ssh_config}" <<'EOF'
X11Forwarding no
AllowTcpForwarding yes
PermitTunnel yes
LoginGraceTime 30
MaxStartups 30:30:100
EOF
  if sshd -T 2>/dev/null | grep -q '^persourcemaxstartups '; then
    printf 'PerSourceMaxStartups 3\n' >>"${ssh_config}"
  fi
  if sshd -T 2>/dev/null | grep -q '^persourcepenalties '; then
    printf 'PerSourcePenalties no\n' >>"${ssh_config}"
  fi
  if ! sshd -t >/dev/null 2>&1; then
    if (( ssh_config_existed )); then
      cp -a "${ssh_backup}" "${ssh_config}"
    else
      rm -f "${ssh_config}"
    fi
    rm -f "${ssh_backup}"
    die "Новые параметры SSH не прошли проверку; предыдущая конфигурация восстановлена."
  fi
  rm -f "${ssh_backup}"
  systemctl reload ssh.service
  ok "Fail2ban, auditd и автоматические security-обновления включены."
}

check_vpn() {
  command -v awg >/dev/null 2>&1 || warn "AmneziaWG CLI пока не установлен; панель покажет AWG остановленным."
  [[ -s "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf" || -s "/etc/amnezia/${AWG_INTERFACE}.conf" ]] \
    || warn "отсутствует конфигурация ${AWG_INTERFACE}; AmneziaWG пока недоступен."
}

prepare_package_manager() {
  export DEBIAN_FRONTEND=noninteractive
  if [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
    info "Восстановление незавершённой пакетной операции"
    apt-get -o DPkg::Lock::Timeout=300 -f install -y
  fi
  [[ -z "$(dpkg --audit 2>/dev/null)" ]] \
    || die "dpkg остаётся в незавершённом состоянии; проверьте журнал пакетного менеджера."
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
  local installer image_root
  image_root="$(dirname -- "${manifest}")"
  installer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("installer",""))' "${manifest}")"
  [[ "${installer}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${installer}" ]] \
    || die "образ ${image_id} содержит некорректный installer."
  info "Установка образа ${image_id}"
  prepare_package_manager
  ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
    AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
    HYSTERIA2_PORT="${HYSTERIA2_PORT}" TUIC_PORT="${TUIC_PORT}" XRAY_PORT="${XRAY_PORT}" \
    bash "${image_root}/${installer}"
  install -d -m 0700 /etc/wireguard /etc/amnezia /etc/amnezia/amneziawg
  sync_protocol_monitor
  systemctl restart "${APP_NAME}-api.service"
  curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 \
    "http://127.0.0.1:8000/api/health" >/dev/null
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
  PRESERVE_COMPONENT_DATA=0 ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
    AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
    HYSTERIA2_PORT="${HYSTERIA2_PORT}" TUIC_PORT="${TUIC_PORT}" XRAY_PORT="${XRAY_PORT}" \
    bash "${image_root}/${uninstaller}"
  install -d -m 0700 /etc/wireguard /etc/amnezia /etc/amnezia/amneziawg
  sync_protocol_monitor
  systemctl restart "${APP_NAME}-api.service"
  curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 \
    "http://127.0.0.1:8000/api/health" >/dev/null
  ok "Протокол ${image_id} удалён; образ сохранён."
}

set_protocol_client_update_state() {
  local protocol="$1" state="${2:-}" message="${3:-}"
  python3 - "${DATA_DIR}/clients.json" "${protocol}" "${state}" "${message}" <<'PY'
import json, os, sys
path, protocol, state, message = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as source:
        clients = json.load(source)
except (FileNotFoundError, json.JSONDecodeError):
    clients = []
for client in clients:
    if client.get("protocol") != protocol:
        continue
    if state:
        client["update_state"] = state
        client["update_message"] = message
    else:
        client.pop("update_state", None)
        client.pop("update_message", None)
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
temporary = path + ".update"
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(clients, output, ensure_ascii=False, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
}

protocol_binary_version() {
  local binary="$1"
  [[ -x "${binary}" ]] || return 0
  local output
  output="$("${binary}" version 2>/dev/null || "${binary}" --version 2>/dev/null || true)"
  grep -Eo 'v?[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?' <<<"${output}" | head -n1 | sed 's/^v//' || true
}

update_protocol_image() {
  local image_id="${2:-}"
  [[ "${image_id}" =~ ^(awg|hysteria2|tuic|xray)$ ]] || die "протокол ${image_id} не поддерживает обновление."
  local manifest image_root installer service binary old_version new_version was_active="no"
  manifest="$(find "${INSTALL_DIR}/protocol-images" -mindepth 2 -maxdepth 2 -type f -name manifest.json -print | while read -r candidate; do
    [[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id",""))' "${candidate}")" == "${image_id}" ]] && { echo "${candidate}"; break; }
  done)"
  [[ -n "${manifest}" ]] || die "образ ${image_id} не найден."
  image_root="$(dirname -- "${manifest}")"
  installer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("installer",""))' "${manifest}")"
  [[ "${installer}" =~ ^[a-zA-Z0-9._-]+$ && -f "${image_root}/${installer}" ]] || die "для ${image_id} отсутствует безопасный installer."
  case "${image_id}" in
    awg) service="awg-quick@${AWG_INTERFACE}.service"; binary="$(command -v awg || true)" ;;
    hysteria2) service="vps-control-hysteria2.service"; binary="/usr/local/lib/vps-control-hysteria2/hysteria" ;;
    tuic) service="vps-control-tuic.service"; binary="/usr/local/lib/vps-control-tuic/sing-box" ;;
    xray) service="vps-control-xray.service"; binary="/usr/local/lib/vps-control-xray/xray" ;;
  esac
  systemctl is-active --quiet "${service}" && was_active="yes"
  old_version="$(protocol_binary_version "${binary}")"
  local backup_dir
  backup_dir="$(mktemp -d "${DATA_DIR}/protocol-update-${image_id}.XXXXXX")"
  chmod 0700 "${backup_dir}"
  [[ -n "${binary}" && -f "${binary}" ]] && install -m 0755 "${binary}" "${backup_dir}/binary"
  set_protocol_client_update_state "${image_id}" "paused" "Обновление протокола запущено: подключение временно приостановлено"
  info "Обновление ${image_id}; существующие подключения помечены как приостановленные"
  if ! (prepare_package_manager && \
    ENV_FILE="${ENV_FILE}" WG_INTERFACE="${WG_INTERFACE}" WG_PORT="${WG_PORT}" \
      AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
      HYSTERIA2_PORT="${HYSTERIA2_PORT}" TUIC_PORT="${TUIC_PORT}" XRAY_PORT="${XRAY_PORT}" \
      bash "${image_root}/${installer}"); then
    if [[ -f "${backup_dir}/binary" && -n "${binary}" ]]; then
      install -m 0755 "${backup_dir}/binary" "${binary}"
      systemctl restart "${service}" || true
    fi
    set_protocol_client_update_state "${image_id}" "incompatible" "Новая версия не прошла проверку совместимости; сохранена предыдущая версия протокола"
    rm -rf -- "${backup_dir}"
    die "обновление ${image_id} отклонено: новая версия нарушает запуск или текущую конфигурацию."
  fi
  [[ "${was_active}" == "yes" ]] || systemctl stop "${service}"
  new_version="$(protocol_binary_version "${binary}")"
  if [[ -n "${old_version}" && -n "${new_version}" && "${old_version%%.*}" != "${new_version%%.*}" ]]; then
    set_protocol_client_update_state "${image_id}" "attention" "После смены основной версии проверьте подключение; при необходимости пересоздайте клиентский профиль"
  else
    set_protocol_client_update_state "${image_id}" "" ""
  fi
  rm -rf -- "${backup_dir}"
  sync_protocol_monitor
  systemctl restart "${APP_NAME}-api.service"
  curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 "http://127.0.0.1:8000/api/health" >/dev/null
  ok "${image_id} обновлён${new_version:+ до версии ${new_version}}."
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
    local vpn_interface_available="no"
    if ip link show "${WG_INTERFACE}" >/dev/null 2>&1; then
      ufw allow in on "${WG_INTERFACE}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via WG'
      vpn_interface_available="yes"
    fi
    if ip link show "${AWG_INTERFACE}" >/dev/null 2>&1; then
      ufw allow in on "${AWG_INTERFACE}" to any port "${HTTP_PORT}" proto tcp comment '312.net panel via AWG'
      vpn_interface_available="yes"
    fi
    [[ "${vpn_interface_available}" == "yes" ]] || die "нет доступного интерфейса WG/AWG для панели."
    ufw delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
  else
    ufw allow "${HTTP_PORT}/tcp"
    if [[ -n "${PUBLIC_DOMAIN}" ]]; then
      ufw allow 80/tcp
      ufw allow 443/tcp
    fi
  fi
  ufw --force enable
}

configure_vpn_firewall_policy() {
  local uplink policy_script policy_service interface subnet installed=0
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
      installed=$((installed + 1))
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
  set_config_value "${INSTALL_CONFIG}" "PUBLIC_DOMAIN" "${PUBLIC_DOMAIN}"
}

sync_release() {
  info "Копирование приложения в ${INSTALL_DIR}"
  install -d -m 0755 "${INSTALL_DIR}"
  chmod 0755 "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.idea/' \
    --exclude '.openai/' \
    --exclude '.servers/' \
    --exclude 'docs/audit/' \
    --exclude 'docs/backlog/' \
    --exclude 'AGENTS.md' \
    --exclude 'tmp/' \
    --exclude 'output/' \
    --exclude 'work/' \
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
  rm -f -- "${DATA_DIR}/personalization.json"
  if [[ ! -s "${ENV_FILE}" ]]; then
    install -m 0600 "${PROJECT_DIR}/.env.example" "${ENV_FILE}"
    [[ -n "${ADMIN_PASSWORD}" ]] || ADMIN_PASSWORD="$(generate_admin_password)"
    set_env_value "ADMIN_USER" "${ADMIN_USER}"
    set_env_value "ADMIN_PASSWORD" "${ADMIN_PASSWORD}"
    chmod 0600 "${ENV_FILE}"
    ok "создан ${ENV_FILE}; постоянные учётные данные администратора подготовлены."
  else
    ok "существующий ${ENV_FILE} сохранён."
    [[ -n "$(env_value ADMIN_USER)" ]] || set_env_value "ADMIN_USER" "${ADMIN_USER}"
    if [[ -z "$(env_value ADMIN_PASSWORD)" ]]; then
      [[ -n "${ADMIN_PASSWORD}" ]] || ADMIN_PASSWORD="$(generate_admin_password)"
      set_env_value "ADMIN_PASSWORD" "${ADMIN_PASSWORD}"
    fi
  fi
  if [[ -z "$(env_value PUBLIC_IP)" ]]; then
    refresh_server_identity
  else
    # Updates reuse the verified identity and avoid unnecessary external GeoIP
    # requests. The explicit `identity` action remains available for refreshes.
    configure_access
  fi
}

ensure_runtime_dependencies() {
  local command_name
  for command_name in caddy curl git node npm python3 rsync; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      install_packages
      return
    fi
  done
  if ! node_runtime_supported; then
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
ReadWritePaths=-/etc/vps-control.env -/etc/wireguard -/etc/amnezia -/etc/vps-control ${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "${APP_NAME}-api.service" >>"${INSTALL_LOG}" 2>&1
}

ensure_api_write_access() {
  local expected="ReadWritePaths=-/etc/vps-control.env -/etc/wireguard -/etc/amnezia -/etc/vps-control ${DATA_DIR}"
  if ! grep -Eq '^ReadWritePaths=.*-?/etc/vps-control\.env([[:space:]]|$)' "${SERVICE_FILE}" \
    || ! grep -Eq '^ReadWritePaths=.*-?/etc/vps-control([[:space:]]|$)' "${SERVICE_FILE}"; then
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
  systemctl daemon-reload
  systemctl enable "${APP_NAME}-web.service" caddy.service >>"${INSTALL_LOG}" 2>&1
}

render_caddy_config() {
  local template="$1" destination="$2" confirmed_domain site_address
  confirmed_domain="$(env_value PUBLIC_DOMAIN)"
  site_address=":${HTTP_PORT}"
  if [[ "${ACCESS_MODE}" == "external" && -n "${confirmed_domain}" ]]; then
    site_address="${confirmed_domain}"
  fi
  sed \
    -e "s|:{\$HTTP_PORT}|${site_address}|g" \
    -e "s|{\$SITE_ADDRESS}|${site_address}|g" \
    -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
    "${template}" >"${destination}"
}

validate_caddy_template() {
  local template="$1" candidate
  candidate="$(mktemp /tmp/vps-control.Caddyfile.XXXXXX)"
  if ! render_caddy_config "${template}" "${candidate}" \
    || grep -Eq '\{\$[A-Za-z_][A-Za-z0-9_]*\}' "${candidate}" \
    || ! caddy validate --adapter caddyfile --config "${candidate}" >/dev/null; then
    rm -f -- "${candidate}"
    return 1
  fi
  rm -f -- "${candidate}"
}

write_caddy_config() {
  local candidate
  candidate="$(mktemp /etc/caddy/.Caddyfile.XXXXXX)"
  if ! render_caddy_config "${INSTALL_DIR}/Caddyfile" "${candidate}" \
    || grep -Eq '\{\$[A-Za-z_][A-Za-z0-9_]*\}' "${candidate}" \
    || ! caddy validate --adapter caddyfile --config "${candidate}" >/dev/null; then
    rm -f -- "${candidate}"
    warn "новая конфигурация Caddy отклонена; рабочая конфигурация не изменена."
    return 1
  fi
  CADDY_CONFIG_PREVIOUS_EXISTS="no"
  if [[ -f "${CADDY_CONFIG_BACKUP}" ]]; then
    CADDY_CONFIG_PREVIOUS_EXISTS="yes"
  elif [[ -f "${CADDY_CONFIG}" ]]; then
    cp -a -- "${CADDY_CONFIG}" "${CADDY_CONFIG_BACKUP}"
    CADDY_CONFIG_PREVIOUS_EXISTS="yes"
  fi
  chmod 0644 "${candidate}"
  mv -f -- "${candidate}" "${CADDY_CONFIG}"
  CADDY_CONFIG_ROLLBACK_READY="yes"
}

discard_caddy_config_backup() {
  rm -f -- "${CADDY_CONFIG_BACKUP}"
  CADDY_CONFIG_ROLLBACK_READY="no"
  CADDY_CONFIG_PREVIOUS_EXISTS="no"
}

restore_caddy_config() {
  [[ "${CADDY_CONFIG_ROLLBACK_READY}" == "yes" ]] || return 0
  if [[ "${CADDY_CONFIG_PREVIOUS_EXISTS}" == "yes" && -f "${CADDY_CONFIG_BACKUP}" ]]; then
    mv -f -- "${CADDY_CONFIG_BACKUP}" "${CADDY_CONFIG}"
  elif [[ "${CADDY_CONFIG_PREVIOUS_EXISTS}" == "no" ]]; then
    rm -f -- "${CADDY_CONFIG}"
  else
    return 1
  fi
  CADDY_CONFIG_ROLLBACK_READY="no"
  CADDY_CONFIG_PREVIOUS_EXISTS="no"
}

restart_caddy_service() {
  if systemctl restart caddy.service; then
    discard_caddy_config_backup
    return 0
  fi
  warn "Caddy не принял новую конфигурацию; восстанавливается предыдущая."
  if restore_caddy_config; then
    systemctl restart caddy.service 2>/dev/null \
      || warn "предыдущая конфигурация Caddy восстановлена, но служба не запустилась."
  else
    warn "резервная конфигурация Caddy недоступна."
  fi
  return 1
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

remove_obsolete_trojan() {
  sed -i '/^TROJAN_PORT=/d' "${ENV_FILE}" "${INSTALL_CONFIG}" 2>/dev/null || true
  [[ -e /etc/systemd/system/vps-control-trojan.service || -d /usr/local/lib/vps-control-trojan || -d /etc/vps-control/trojan ]] || return 0
  info "Удаление исключённого модуля Trojan"
  systemctl disable --now vps-control-trojan.service 2>/dev/null || true
  if [[ -x /usr/local/lib/vps-control-trojan/firewall.sh ]]; then
    /usr/local/lib/vps-control-trojan/firewall.sh delete 2>/dev/null || true
  fi
  rm -f -- /etc/systemd/system/vps-control-trojan.service
  rm -rf -- /usr/local/lib/vps-control-trojan /etc/vps-control/trojan
  python3 - "${DATA_DIR}/clients.json" <<'PY'
import json
import os
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    clients = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    clients = []
if any(item.get("protocol") == "trojan" for item in clients):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps([item for item in clients if item.get("protocol") != "trojan"], ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
PY
  systemctl daemon-reload
  ok "модуль Trojan и его подключения удалены."
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
  RELEASE_BRANCH="$(git -C "${PROJECT_DIR}" branch --show-current 2>/dev/null || true)"
  [[ "${RELEASE_BRANCH}" == "test-light" ]] || RELEASE_BRANCH="light"
  export APP_VERSION BUILD_COMMIT
  export NEXT_PUBLIC_APP_VERSION="${APP_VERSION}"
  export NEXT_PUBLIC_BUILD_COMMIT="${BUILD_COMMIT}"
  export NEXT_PUBLIC_RELEASE_BRANCH="${RELEASE_BRANCH}"
  if [[ ! -r "${INSTALL_CONFIG}" ]]; then
    install -m 0600 "${PROJECT_DIR}/install.conf" "${INSTALL_CONFIG}"
  fi
  remove_obsolete_trojan
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
  info "Запуск обновлённой версии 312.net"
  stop_legacy_containers
  systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service"
  restart_caddy_service
  systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  curl --fail --silent --retry 6 --retry-connrefused --retry-delay 2 \
    "http://127.0.0.1:${HTTP_PORT}/" >/dev/null
  cleanup_legacy_runtime
  ok "панель запущена: ${PANEL_URL}"
}

start_services() {
  configure_access
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
  systemctl disable --now "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service 2>/dev/null || true
  systemctl disable --now vpn-monitor.timer 2>/dev/null || true
  if [[ -f "${INSTALL_DIR}/protocol-images/amneziawg/uninstall.sh" ]] \
    && [[ -s "/etc/amnezia/amneziawg/${AWG_INTERFACE}.conf" || -s "/etc/amnezia/${AWG_INTERFACE}.conf" ]]; then
    ENV_FILE="${ENV_FILE}" AWG_INTERFACE="${AWG_INTERFACE}" AWG_PORT="${AWG_PORT}" \
      bash "${INSTALL_DIR}/protocol-images/amneziawg/uninstall.sh"
  fi
  local protocol_id
  for protocol_id in hysteria2 tuic xray; do
    if [[ -f "${INSTALL_DIR}/protocol-images/${protocol_id}/uninstall.sh" ]]; then
      PRESERVE_COMPONENT_DATA=0 bash "${INSTALL_DIR}/protocol-images/${protocol_id}/uninstall.sh"
    fi
  done
  rm -f "${SERVICE_FILE}" "${WEB_SERVICE_FILE}" /etc/systemd/system/vpn-monitor.service /etc/systemd/system/vpn-monitor.timer \
    /etc/logrotate.d/vps-control-monitor "${COMMAND_PATH}" /usr/local/sbin/vpn-monitor-sample
  systemctl daemon-reload
  rm -rf -- "${INSTALL_DIR}" "${DATA_DIR}"
  rm -f -- "${ENV_FILE}" "${INSTALL_CONFIG}" "${MANAGER_CONFIG}" "${CADDY_CONFIG}"
  rmdir --ignore-fail-on-non-empty /etc/wireguard /etc/amnezia/amneziawg /etc/amnezia 2>/dev/null || true
  ufw delete allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
  CURRENT_ACTION=""
  ok "панель полностью удалена; общие системные пакеты сохранены."
}

restart_services() {
  info "Перезапуск служб панели"
  systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
  verify_app
  ok "Панель перезапущена."
}

ssh_units_action() {
  local units=(ssh.service)
  if [[ "$(systemctl show ssh.socket --property=LoadState --value 2>/dev/null)" == loaded ]]; then
    units=(ssh.socket "${units[@]}")
  fi
  if [[ "$1" == "start" ]]; then
    # Debian refuses to start ssh.socket while ssh.service already owns port 22.
    # The service alone is sufficient to keep SSH available during maintenance.
    systemctl start ssh.service
  else
    systemctl "$1" "${units[@]}"
  fi
}

prepare_update_ssh() {
  if ! systemctl is-active --quiet ssh.service && ! systemctl is-active --quiet ssh.socket; then
    info "Временный запуск SSH на время обновления"
    ssh_units_action start
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
    ssh_units_action stop >/dev/null 2>&1 || true
    SSH_TEMP_STARTED="no"
  fi
}

install_prebuilt_release() {
  local archive="${2:-}" preserve_previous="${3:-no}" expected_channel="${4:-production}" archive_path archive_listing stage_root payload rollback requirements_hash installed_requirements_hash build_commit legacy_runtime="no"
  local metadata release_schema release_edition release_channel release_architecture release_commit
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
  metadata="${payload}/.prebuilt-release"
  release_schema="$(release_metadata_value "${metadata}" schema)"
  release_edition="$(release_metadata_value "${metadata}" edition)"
  release_channel="$(release_metadata_value "${metadata}" channel)"
  release_architecture="$(release_metadata_value "${metadata}" architecture)"
  release_commit="$(release_metadata_value "${metadata}" commit)"
  [[ "${release_schema}" == "1" ]] || { rm -rf -- "${stage_root}"; die "неподдерживаемая схема метаданных релиза."; }
  [[ "${release_edition}" == "${PRODUCT_EDITION}" ]] || { rm -rf -- "${stage_root}"; die "архив редакции ${release_edition:-unknown} нельзя установить поверх ${PRODUCT_EDITION}."; }
  [[ "${release_channel}" == "${expected_channel}" ]] || { rm -rf -- "${stage_root}"; die "ожидался канал ${expected_channel}, получен ${release_channel:-unknown}."; }
  [[ "${release_architecture}" == "$(system_architecture)" ]] || { rm -rf -- "${stage_root}"; die "архив предназначен для другой архитектуры."; }
  [[ "${release_commit}" =~ ^[0-9a-f]{40}$ ]] || { rm -rf -- "${stage_root}"; die "архив не содержит полный commit SHA."; }
  ensure_product_identity || { rm -rf -- "${stage_root}"; die "идентичность установленной редакции не прошла проверку."; }
  [[ -x "${payload}/node_modules/.bin/vinext" && -f "${payload}/dist/server/index.js" && -f "${payload}/api/main.py" ]] \
    || { rm -rf -- "${stage_root}"; die "в архиве отсутствует готовая web/API-сборка."; }
  validate_caddy_template "${payload}/Caddyfile" \
    || { rm -rf -- "${stage_root}"; die "Caddyfile нового релиза не прошёл предварительную проверку; работающая версия не изменена."; }

  requirements_hash="$(sha256sum "${payload}/api/requirements.txt" | awk '{print $1}')"
  installed_requirements_hash="$(cat "${INSTALL_DIR}/venv/.requirements.sha256" 2>/dev/null || true)"
  [[ -n "${installed_requirements_hash}" && "${requirements_hash}" == "${installed_requirements_hash}" ]] \
    || { rm -rf -- "${stage_root}"; die "Python-зависимости изменились; подготовьте полный системный релиз."; }

  rollback="${INSTALL_DIR}.rollback.$(date -u +%Y%m%dT%H%M%SZ)"
  info "Установка заранее собранного релиза без Docker и сборки на VPS"
  if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -Eq '^vps-control-(web|gateway)-1$'; then
    legacy_runtime="yes"
    stop_legacy_containers
  fi
  systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
  mv -- "${INSTALL_DIR}" "${rollback}"
  mv -- "${payload}" "${INSTALL_DIR}"
  mv -- "${rollback}/venv" "${INSTALL_DIR}/venv"
  chmod 0755 "${INSTALL_DIR}" "${INSTALL_DIR}/scripts/vps-control.sh"
  PROJECT_DIR="${INSTALL_DIR}"

  if ! install_api \
    || ! install_web \
    || ! ensure_api_write_access \
    || ! build_commit="$(awk -F= '$1 == "commit" {print $2}' "${INSTALL_DIR}/.prebuilt-release")" \
    || ! printf '%s\n' "${build_commit:-manual}" >"${INSTALL_DIR}/.build-commit" \
    || ! write_integrity_manifest \
    || ! systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" \
    || ! restart_caddy_service \
    || ! systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! curl --fail --silent --retry 10 --retry-connrefused --retry-delay 2 \
      "http://127.0.0.1:${HTTP_PORT}/" >/dev/null; then
    warn "новый релиз не прошёл проверку; выполняется откат."
    restore_caddy_config || warn "не удалось восстановить предыдущую конфигурацию Caddy."
    systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
    if [[ -d "${INSTALL_DIR}/venv" && ! -e "${rollback}/venv" ]]; then
      mv -- "${INSTALL_DIR}/venv" "${rollback}/venv"
    fi
    rm -rf -- "${INSTALL_DIR}"
    mv -- "${rollback}" "${INSTALL_DIR}"
    PROJECT_DIR="${INSTALL_DIR}"
    write_integrity_manifest
    if [[ "${legacy_runtime}" == "yes" ]]; then
      systemctl stop "${APP_NAME}-web.service" caddy.service 2>/dev/null || true
      start_legacy_containers
      systemctl restart "${APP_NAME}-api.service"
    else
      systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
    fi
    curl --fail --silent --retry 10 --retry-connrefused --retry-delay 2 \
      "http://127.0.0.1:${HTTP_PORT}/" >/dev/null \
      || warn "предыдущая версия восстановлена, но ещё не отвечает на проверку готовности."
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
  rm -rf -- "${stage_root}"
  cleanup_legacy_runtime
  install -m 0755 "${INSTALL_DIR}/scripts/vps-control.sh" "${COMMAND_PATH}"
  ok "подготовленный релиз установлен; WG/AWG и системные пакеты не изменялись."
}

update_prebuilt_branch() {
  local branch="$1" release_tag="$2" expected_channel="${3:-production}" preserve_previous="${4:-no}"
  local remote="${REMOTE_URL:-https://github.com/aske312/vpsController.git}"
  local latest current repository_path release_url archive release_commit release_revision ready attempt

  if [[ "${remote}" =~ ^git@github\.com:(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  elif [[ "${remote}" =~ ^ssh://git@github\.com/(.+)$ ]]; then
    remote="https://github.com/${BASH_REMATCH[1]}"
  fi
  [[ "${branch}:${expected_channel}" == "${PRODUCTION_BRANCH}:production" \
    || "${branch}:${expected_channel}" == "${TEST_BRANCH}:test" ]] \
    || die "ветка ${branch} не соответствует каналу ${expected_channel}."
  latest="$(git ls-remote "${remote}" "refs/heads/${branch}" 2>/dev/null | awk 'NR == 1 {print $1}')"
  [[ "${latest}" =~ ^[0-9a-f]{40}$ ]] || die "не удалось получить актуальную ревизию ветки ${branch}."
  current="$(cat "${INSTALL_DIR}/.build-commit" 2>/dev/null || true)"
  if [[ -n "${current}" && "${latest}" == "${current}" ]]; then
    ok "установлена актуальная версия ветки ${branch} (${current})."
    return 0
  fi

  ready="no"
  for attempt in $(seq 1 48); do
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

  if [[ "${expected_channel}" == "test" ]]; then
    release_url="${TEST_RELEASE_URL:-}"
  else
    release_url="${APP_RELEASE_URL:-}"
  fi
  if [[ -z "${release_url}" && "${remote}" =~ ^https://github\.com/([^/]+/[^/]+)$ ]]; then
    repository_path="${BASH_REMATCH[1]%.git}"
    release_url="https://github.com/${repository_path}/releases/download/${release_tag}/vps-control-${PRODUCT_EDITION}-linux-$(system_architecture).tar.gz"
  fi
  [[ "${release_url}" =~ ^https:// ]] || die "не настроен HTTPS-адрес подготовленного релиза ${branch}."

  install -d -m 0750 "${DATA_DIR}/tmp"
  UPDATE_TEMP_DIR="$(mktemp -d "${DATA_DIR}/tmp/update.XXXXXX")"
  archive="${UPDATE_TEMP_DIR}/vps-control-release.tar.gz"
  info "Загрузка подготовленного релиза ветки ${branch}"
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --connect-timeout 15 --max-time 900 --output "${archive}" "${release_url}"
  release_commit="$(tar -xOf "${archive}" vps-control-release/.prebuilt-release 2>/dev/null \
    | awk -F= '$1 == "commit" {print $2}')"
  [[ "${release_commit}" =~ ^[0-9a-f]{40}$ && "${latest}" == "${release_commit}" ]] \
    || die "подготовленный релиз не соответствует актуальной ревизии ветки ${branch}."

  install_prebuilt_release install-release "${archive}" "${preserve_previous}" "${expected_channel}"
  ok "приложение обновлено до ${branch} ${release_commit}."
}

update_app() {
  update_prebuilt_branch "${PRODUCTION_BRANCH}" "${PRODUCTION_RELEASE_TAG}"
}

scheduled_app_update() {
  if [[ -r "${SERVICE_MODE_FILE}" ]]; then
    warn "плановое обновление light пропущено: включён сервисный режим."
    return 0
  fi
  update_app
}

update_test_app() {
  local archive="${2:-}"
  [[ -r "${SERVICE_MODE_FILE}" ]] || die "переход на тестовую версию разрешён только в сервисном режиме."
  if [[ -n "${archive}" ]]; then
    if [[ -d "${TEST_BACKUP_DIR}" ]]; then
      install_prebuilt_release install-release "${archive}" no test
    else
      install_prebuilt_release install-release "${archive}" yes test
    fi
  elif [[ -d "${TEST_BACKUP_DIR}" ]]; then
    update_prebuilt_branch "${TEST_BRANCH}" "${TEST_RELEASE_TAG}" test no
  else
    update_prebuilt_branch "${TEST_BRANCH}" "${TEST_RELEASE_TAG}" test yes
  fi
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
  if ! install_api || ! install_web || ! ensure_api_write_access \
    || ! systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" \
    || ! restart_caddy_service \
    || ! systemctl is-active --quiet "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service \
    || ! curl --fail --silent --retry 10 --retry-connrefused --retry-delay 2 "http://127.0.0.1:${HTTP_PORT}/" >/dev/null; then
    warn "сохранённая версия не запустилась; тестовая версия восстанавливается."
    restore_caddy_config || warn "не удалось восстановить предыдущую конфигурацию Caddy."
    systemctl stop "${APP_NAME}-web.service" "${APP_NAME}-api.service" 2>/dev/null || true
    if [[ -d "${INSTALL_DIR}/venv" && ! -e "${failed_install}/venv" ]]; then
      mv -- "${INSTALL_DIR}/venv" "${failed_install}/venv"
    fi
    mv -- "${INSTALL_DIR}" "${TEST_BACKUP_DIR}"
    mv -- "${failed_install}" "${INSTALL_DIR}"
    PROJECT_DIR="${INSTALL_DIR}"
    write_integrity_manifest
    systemctl restart "${APP_NAME}-api.service" "${APP_NAME}-web.service" caddy.service
    curl --fail --silent --retry 10 --retry-connrefused --retry-delay 2 \
      "http://127.0.0.1:${HTTP_PORT}/" >/dev/null \
      || warn "тестовая версия восстановлена, но ещё не отвечает на проверку готовности."
    die "возврат отклонён; тестовая версия продолжает работать."
  fi
  [[ "${failed_install}" == "${INSTALL_DIR}.failed-test."* ]] || die "небезопасный путь очистки тестовой версии."
  rm -rf -- "${failed_install}"
  write_integrity_manifest
  install -m 0755 "${INSTALL_DIR}/scripts/vps-control.sh" "${COMMAND_PATH}"
  rm -f "${DATA_DIR}/application-version.json"
  ok "рабочая версия приложения восстановлена; WG/AWG, клиенты и модули не изменялись."
}

change_access_mode() {
  local requested="${2:-}"
  [[ "${requested}" == "external" || "${requested}" == "vpn" ]] || die "режим должен быть external или vpn."
  ACCESS_MODE="${requested}"
  set_config_value "${INSTALL_CONFIG}" "ACCESS_MODE" "${ACCESS_MODE}"
  set_config_value "${INSTALL_DIR}/install.conf" "ACCESS_MODE" "${ACCESS_MODE}"
  configure_access
  configure_firewall "panel-only"
  write_caddy_config
  restart_caddy_service
  systemctl restart "${APP_NAME}-api.service"
  curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 \
    "http://127.0.0.1:8000/api/health" >/dev/null
  if [[ "${ACCESS_MODE}" == "vpn" ]]; then
    ok "панель доступна только через WG/AWG."
  else
    ok "публичный доступ к панели открыт."
  fi
}

change_service_mode() {
  local requested="${2:-}" previous_access ssh_service_active ssh_socket_active ssh_public active_timers
  [[ "${requested}" == "enable" || "${requested}" == "disable" ]] || die "режим должен быть enable или disable."
  if [[ "${requested}" == "disable" && -r "${INSTALL_DIR}/.prebuilt-release" \
    && "$(release_metadata_value "${INSTALL_DIR}/.prebuilt-release" channel)" == "test" ]]; then
    die "сначала вернитесь на light, затем выключите сервисный режим."
  fi
  if [[ "${requested}" == "enable" ]]; then
    [[ ! -f "${SERVICE_MODE_FILE}" ]] || { warn "сервисный режим уже включён."; return; }
    previous_access="${ACCESS_MODE}"
    ssh_service_active="$([[ "$(systemctl is-active ssh.service)" == "active" ]] && printf yes || printf no)"
    ssh_socket_active="$([[ "$(systemctl is-active ssh.socket)" == "active" ]] && printf yes || printf no)"
    ssh_public="$([[ "$(ufw status | grep -Ec '^OpenSSH[[:space:]]+ALLOW[[:space:]]+Anywhere([[:space:]]|$)')" -gt 0 ]] && printf yes || printf no)"
    active_timers=""
    for timer in vps-control-auto-update.timer apt-daily.timer apt-daily-upgrade.timer; do
      if systemctl is-active --quiet "${timer}"; then
        active_timers+="${timer},"
        systemctl stop "${timer}"
      fi
    done
    systemctl stop \
      vps-control-auto-reboot.timer \
      vps-control-auto-cleanup.timer \
      vps-control-auto-protocol-scan.timer \
      vps-control-auto-application-update.timer \
      vps-control-auto-kernel-update.timer 2>/dev/null || true
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
    if [[ -r "${AUTOMATION_FILE}" ]]; then
      apply_automation
    fi
    ssh_units_action start
    ufw allow OpenSSH
    change_access_mode "$1" external
    ok "сервисный режим включён; версия приложения не изменена."
  else
    [[ -r "${SERVICE_MODE_FILE}" ]] || { warn "сервисный режим уже выключен."; return; }
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
    if [[ "${ssh_socket_active}" != "yes" && "$(systemctl show ssh.socket --property=LoadState --value 2>/dev/null)" == loaded ]]; then
      systemctl stop ssh.socket
    fi
    ok "сервисный режим выключен; исходные состояния восстановлены."
  fi
}

installed_kernel_packages() {
  dpkg-query -W -f='${binary:Package} ${db:Status-Abbrev}\n' 'linux-*' 2>/dev/null \
    | awk '$2 == "ii" {
      sub(/:.*/, "", $1)
      if ($1 ~ /^linux-(image-|headers-)?(generic|virtual|aws|azure|gcp|oracle|raspi)(-[a-zA-Z0-9.+-]+)?$/ ||
          $1 ~ /^linux-(image|headers)-(cloud-|rt-)?(amd64|arm64)$/) print $1
    }'
}

fallback_kernel_package() {
  local os_id="" architecture kernel_release flavor
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    os_id="${ID:-}"
  fi
  architecture="$(dpkg --print-architecture 2>/dev/null || true)"
  kernel_release="$(uname -r)"
  case "${os_id}" in
    debian)
      case "${kernel_release}" in
        *-cloud-*) printf 'linux-image-cloud-%s\n' "${architecture}" ;;
        *-rt-*) printf 'linux-image-rt-%s\n' "${architecture}" ;;
        *) printf 'linux-image-%s\n' "${architecture}" ;;
      esac
      ;;
    ubuntu)
      flavor="${kernel_release##*-}"
      case "${flavor}" in
        generic|virtual|aws|azure|gcp|oracle|raspi|kvm) printf 'linux-%s\n' "${flavor}" ;;
        *) printf 'linux-generic\n' ;;
      esac
      ;;
    *)
      die "автоматический выбор пакета ядра поддерживается только для Debian и Ubuntu."
      ;;
  esac
}

kernel_update_packages() {
  local package candidate
  local installed=() packages=()
  mapfile -t installed < <(installed_kernel_packages)
  if ((${#installed[@]})); then
    packages=("${installed[@]}")
  else
    mapfile -t packages < <(fallback_kernel_package)
  fi
  if ((${#packages[@]})) && [[ -n "$(registered_dkms_modules)" ]]; then
    for package in "${packages[@]}"; do
      candidate=""
      case "${package}" in
        linux-image-*) candidate="linux-headers-${package#linux-image-}" ;;
        linux-generic*|linux-virtual*|linux-aws*|linux-azure*|linux-gcp*|linux-oracle*|linux-raspi*|linux-kvm*)
          candidate="linux-headers-${package#linux-}"
          ;;
      esac
      if [[ -n "${candidate}" ]] && apt-cache show "${candidate}" >/dev/null 2>&1; then
        packages+=("${candidate}")
      fi
    done
  fi
  printf '%s\n' "${packages[@]}" | awk 'NF && !seen[$0]++'
}

newest_installed_kernel() {
  find /boot -maxdepth 1 -type f -name 'vmlinuz-*' -printf '%f\n' 2>/dev/null \
    | sed 's/^vmlinuz-//' | sort -V | tail -n 1
}

registered_dkms_modules() {
  command -v dkms >/dev/null 2>&1 || return 0
  dkms status 2>/dev/null | awk -F'[/,]' '/^[a-zA-Z0-9_.+-]+\// {print $1}' | sort -u
}

prepare_dkms_for_kernel() {
  local kernel="$1" module
  local modules=() failed=()
  mapfile -t modules < <(registered_dkms_modules)
  ((${#modules[@]})) || return 0
  [[ -e "/lib/modules/${kernel}/build" ]] \
    || die "ядро ${kernel} установлено без headers; перезагрузка отменена, активные подключения сохранены."
  info "Сборка DKMS-модулей для ядра ${kernel}"
  if ! dkms autoinstall -k "${kernel}"; then
    die "DKMS не смог собрать модули для ядра ${kernel}; перезагрузка отменена, активные подключения сохранены."
  fi
  depmod -a "${kernel}"
  for module in "${modules[@]}"; do
    dkms status -m "${module}" -k "${kernel}" 2>/dev/null | grep -Eq ':[[:space:]]+installed$' \
      || failed+=("${module}")
  done
  ((${#failed[@]} == 0)) \
    || die "модули ${failed[*]} не готовы для ядра ${kernel}; перезагрузка отменена, активные подключения сохранены."
  ok "Все DKMS-модули готовы для ядра ${kernel}."
}

active_managed_protocol_units() {
  local unit
  for unit in \
    "wg-quick@${WG_INTERFACE}.service" \
    "awg-quick@${AWG_INTERFACE}.service" \
    vps-control-hysteria2.service \
    vps-control-tuic.service \
    vps-control-xray.service; do
    systemctl is-active --quiet "${unit}" && printf '%s\n' "${unit}"
  done
}

verify_managed_protocol_units() {
  local unit
  for unit in "$@"; do
    if ! systemctl is-active --quiet "${unit}"; then
      warn "Служба ${unit} остановилась во время обновления; выполняется восстановление."
      systemctl restart "${unit}" \
        || die "служба протокола ${unit} не восстановилась; перезагрузка отменена."
    fi
  done
}

update_kernel() {
  local running_kernel newest_kernel simulation package
  local packages=() active_protocol_units=()
  info "Проверка репозиториев и пакета ядра"
  export DEBIAN_FRONTEND=noninteractive
  prepare_package_manager
  apt-get -o DPkg::Lock::Timeout=300 update
  mapfile -t active_protocol_units < <(active_managed_protocol_units)
  mapfile -t packages < <(kernel_update_packages)
  ((${#packages[@]})) || die "не удалось определить метапакет ядра."
  for package in "${packages[@]}"; do
    apt-cache show "${package}" >/dev/null 2>&1 \
      || die "пакет ядра ${package} отсутствует в настроенных репозиториях."
  done
  simulation="$(apt-get -o DPkg::Lock::Timeout=300 -s install "${packages[@]}")"
  if grep -q '^Inst ' <<<"${simulation}"; then
    info "Доступно обновление пакета ядра"
  else
    info "Новых пакетов ядра в репозитории нет"
  fi
  apt-get -o DPkg::Lock::Timeout=300 install -y "${packages[@]}"
  running_kernel="$(uname -r)"
  newest_kernel="$(newest_installed_kernel)"
  [[ -n "${newest_kernel}" ]] || die "не удалось определить установленное ядро после обновления."
  prepare_dkms_for_kernel "${newest_kernel}"
  verify_managed_protocol_units "${active_protocol_units[@]}"
  if [[ -n "${newest_kernel}" && "${newest_kernel}" != "${running_kernel}" ]]; then
    REBOOT_AFTER_UPDATE="yes"
    ok "Ядро ${newest_kernel} установлено; сервер будет перезагружен для его активации."
  else
    ok "Ядро ${running_kernel} уже актуально; перезагрузка не требуется."
  fi
  if [[ "${REBOOT_AFTER_UPDATE}" == "yes" && -z "${CURRENT_ACTION}" ]]; then
    systemctl --no-block --no-wall reboot
  fi
}

update_system_packages() {
  local simulation
  local active_protocol_units=()
  info "Установка доступных обновлений системных пакетов"
  export DEBIAN_FRONTEND=noninteractive
  prepare_package_manager
  run_with_status "Обновление списка пакетов" apt-get -o DPkg::Lock::Timeout=300 update
  simulation="$(apt-get -o DPkg::Lock::Timeout=300 -s upgrade)"
  if ! grep -q '^Inst ' <<<"${simulation}"; then
    rm -f "${UPDATES_FILE}"
    ok "Системные пакеты уже актуальны."
    return 0
  fi
  mapfile -t active_protocol_units < <(active_managed_protocol_units)
  run_with_status "Установка системных обновлений" apt-get -o DPkg::Lock::Timeout=300 upgrade -y
  verify_managed_protocol_units "${active_protocol_units[@]}"
  rm -f "${UPDATES_FILE}"
  if [[ -e /var/run/reboot-required ]]; then
    warn "Обновления установлены; для их полной активации требуется перезагрузка."
  else
    ok "Системные обновления установлены; перезагрузка не требуется."
  fi
}

scheduled_kernel_update() {
  if [[ -r "${SERVICE_MODE_FILE}" ]]; then
    warn "плановое обновление ядра пропущено: включён сервисный режим."
    return 0
  fi
  update_kernel
}

optimize_resources() {
  local disk_before disk_after disk_freed mem_before mem_after log_retention_days=30
  disk_before="$(df -B1 / | awk 'NR==2 {print $4}')"
  mem_before="$(awk '/MemAvailable/ {print $2 * 1024}' /proc/meminfo)"
  info "Удаление неиспользуемых пакетов и пакетных кэшей"
  prepare_package_manager
  apt-get -o DPkg::Lock::Timeout=300 autoremove --purge -y
  apt-get -o DPkg::Lock::Timeout=300 clean
  if [[ -r /etc/vps-control-logging.conf ]]; then
    # shellcheck source=/dev/null
    source /etc/vps-control-logging.conf
    log_retention_days="${LOG_RETENTION_DAYS:-30}"
  fi
  if (( log_retention_days > 0 )); then
    journalctl --rotate
    journalctl --vacuum-time="${log_retention_days}d"
    journalctl --vacuum-size=500M
  fi
  info "Очистка временных файлов по системным правилам"
  systemd-tmpfiles --clean
  if [[ -d "${DATA_DIR}/tmp" ]]; then
    find "${DATA_DIR}/tmp" -mindepth 1 -maxdepth 1 -type d -name 'update.*' -mtime +1 -exec rm -rf -- {} +
    find "${DATA_DIR}/tmp" -xdev -depth -mindepth 1 -mtime +7 -delete
  fi
  sync
  printf '3\n' >/proc/sys/vm/drop_caches
  disk_after="$(df -B1 / | awk 'NR==2 {print $4}')"
  mem_after="$(awk '/MemAvailable/ {print $2 * 1024}' /proc/meminfo)"
  disk_freed=$((disk_after > disk_before ? (disk_after - disk_before) / 1024 / 1024 : 0))
  ok "Освобождено на диске: ${disk_freed} МБ; доступная память: $((mem_before / 1024 / 1024)) → $((mem_after / 1024 / 1024)) МБ."
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

check_protocol_versions() {
  info "Проверка актуальных версий модулей протоколов"
  python3 - "${ENV_FILE}" <<'PY'
import base64
import json
import sys
import urllib.request

env_path = sys.argv[1]
values = {}
with open(env_path, encoding="utf-8") as stream:
    for raw_line in stream:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
user = values.get("ADMIN_USER", "admin")
password = values.get("ADMIN_PASSWORD", "")
if not password:
    raise SystemExit("administrator credentials are not configured")
token = base64.b64encode(f"{user}:{password}".encode()).decode()
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/protocol-images/versions/check",
    data=b"{}",
    method="POST",
    headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=90) as response:
    payload = json.load(response)
items = payload.get("items", [])
updates = [item.get("name", item.get("id", "module")) for item in items if item.get("update_available")]
print("updates: " + ", ".join(updates) if updates else "updates: none")
PY
  ok "версии модулей протоколов проверены; установка обновлений не выполнялась."
}

apply_automation() {
  info "Применение расписаний обслуживания"
  [[ -r "${AUTOMATION_FILE}" ]] || die "не найден ${AUTOMATION_FILE}."
  local values reboot_enabled reboot_cadence reboot_weekday reboot_hour reboot_minute
  local cleanup_enabled cleanup_cadence cleanup_weekday cleanup_hour cleanup_minute
  local protocol_scan_enabled protocol_scan_cadence protocol_scan_weekday protocol_scan_hour protocol_scan_minute
  local application_update_enabled application_update_cadence application_update_weekday application_update_hour application_update_minute
  local kernel_update_enabled kernel_update_cadence kernel_update_weekday kernel_update_hour kernel_update_minute
  values="$(python3 - "${AUTOMATION_FILE}" <<'PY'
import json
import shlex
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
defaults = {
    "reboot": ("weekly", "Sun", 4, 0),
    "cleanup": ("weekly", "Sun", 3, 0),
    "protocol_scan": ("daily", "Sun", 2, 30),
    "application_update": ("daily", "Sun", 3, 30),
    "kernel_update": ("weekly", "Sun", 4, 30),
}
for section in ("reboot", "cleanup", "protocol_scan", "application_update", "kernel_update"):
    item = data.get(section, {})
    cadence, weekday, hour, minute = defaults[section]
    values = (
        "yes" if item.get("enabled") else "no",
        str(item.get("cadence", cadence)),
        str(item.get("weekday", weekday)),
        str(int(item.get("hour", hour))),
        str(int(item.get("minute", minute))),
    )
    print(" ".join(shlex.quote(value) for value in values))
PY
)"
  read -r reboot_enabled reboot_cadence reboot_weekday reboot_hour reboot_minute <<<"$(sed -n '1p' <<<"${values}")"
  read -r cleanup_enabled cleanup_cadence cleanup_weekday cleanup_hour cleanup_minute <<<"$(sed -n '2p' <<<"${values}")"
  read -r protocol_scan_enabled protocol_scan_cadence protocol_scan_weekday protocol_scan_hour protocol_scan_minute <<<"$(sed -n '3p' <<<"${values}")"
  read -r application_update_enabled application_update_cadence application_update_weekday application_update_hour application_update_minute <<<"$(sed -n '4p' <<<"${values}")"
  read -r kernel_update_enabled kernel_update_cadence kernel_update_weekday kernel_update_hour kernel_update_minute <<<"$(sed -n '5p' <<<"${values}")"

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
    local id="$1" description="$2" command="$3" enabled="$4" calendar="$5" block_in_service_mode="${6:-no}" service_condition=""
    if [[ "${block_in_service_mode}" == "yes" ]]; then
      service_condition="ConditionPathExists=!${SERVICE_MODE_FILE}"
    fi
    cat >"/etc/systemd/system/vps-control-auto-${id}.service" <<EOF
[Unit]
Description=${description}
After=network-online.target
${service_condition}

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

  local reboot_calendar cleanup_calendar protocol_scan_calendar application_update_calendar kernel_update_calendar
  reboot_calendar="$(automation_calendar "${reboot_cadence}" "${reboot_weekday}" "${reboot_hour}" "${reboot_minute}")"
  cleanup_calendar="$(automation_calendar "${cleanup_cadence}" "${cleanup_weekday}" "${cleanup_hour}" "${cleanup_minute}")"
  protocol_scan_calendar="$(automation_calendar "${protocol_scan_cadence}" "${protocol_scan_weekday}" "${protocol_scan_hour}" "${protocol_scan_minute}")"
  application_update_calendar="$(automation_calendar "${application_update_cadence}" "${application_update_weekday}" "${application_update_hour}" "${application_update_minute}")"
  kernel_update_calendar="$(automation_calendar "${kernel_update_cadence}" "${kernel_update_weekday}" "${kernel_update_hour}" "${kernel_update_minute}")"
  install_automation_timer "reboot" "Scheduled VPS reboot by 312.net" "reboot" "${reboot_enabled}" "${reboot_calendar}" yes
  install_automation_timer "cleanup" "Scheduled VPS cleanup by 312.net" "optimize" "${cleanup_enabled}" "${cleanup_calendar}" yes
  install_automation_timer "protocol-scan" "Scheduled protocol version scan by 312.net" "protocol-version-check" "${protocol_scan_enabled}" "${protocol_scan_calendar}" yes
  install_automation_timer "application-update" "Scheduled light application update by 312.net" "scheduled-app-update" "${application_update_enabled}" "${application_update_calendar}" yes
  install_automation_timer "kernel-update" "Scheduled kernel update by 312.net" "scheduled-kernel-update" "${kernel_update_enabled}" "${kernel_update_calendar}" yes
  systemctl disable --now vps-control-auto-update.timer >/dev/null 2>&1 || true
  rm -f -- /etc/systemd/system/vps-control-auto-update.timer /etc/systemd/system/vps-control-auto-update.service
  systemctl daemon-reload
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
  # Commands such as `identity` restart the API immediately before verification.
  # Treat its short startup window as readiness, not as a failed operation.
  curl --fail --silent --retry 10 --retry-connrefused --retry-delay 1 \
    http://127.0.0.1:8000/api/health
  printf '\n'
  curl --fail --silent --retry 6 --retry-connrefused --retry-delay 5 "${PANEL_URL}/" >/dev/null \
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
  local found="no" interface service tool port
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
    || die "systemd-профиль API не разрешает сохранять административный токен в ${ENV_FILE}."
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

change_public_domain() {
  local requested="${2:-}" confirmed
  [[ -n "${requested}" ]] || die "укажите домен без схемы и пути либо off."
  requested="${requested%.}"
  requested="${requested,,}"
  if [[ "${requested}" == "off" ]]; then
    PUBLIC_DOMAIN=""
  else
    valid_public_domain "${requested}" || die "укажите корректное доменное имя без схемы и пути."
    PUBLIC_DOMAIN="${requested}"
  fi
  set_config_value "${INSTALL_CONFIG}" "PUBLIC_DOMAIN" "${PUBLIC_DOMAIN}"
  set_config_value "${INSTALL_DIR}/install.conf" "PUBLIC_DOMAIN" "${PUBLIC_DOMAIN}"
  configure_access
  configure_firewall "panel-only"
  write_caddy_config
  restart_caddy_service
  systemctl restart "${APP_NAME}-api.service"
  confirmed="$(env_value PUBLIC_DOMAIN)"
  if [[ -n "${confirmed}" ]]; then
    ok "домен ${confirmed} подтверждён; панель доступна по https://${confirmed}."
  elif [[ -n "${PUBLIC_DOMAIN}" ]]; then
    ok "домен ${PUBLIC_DOMAIN} сохранён; до подтверждения DNS панель работает по IP."
  else
    ok "домен отключён; панель работает по IP."
  fi
}

usage() {
  cat <<'EOF'
312.net — управление инфраструктурой

Использование:
  bash scripts/vps-control.sh install
  vps-control <команда>

Команды:
  install          установить зависимости и развернуть панель
  uninstall --yes  полностью удалить панель, данные и конфигурацию
  doctor           проверить совместимость сервера без изменений
  start            запустить API и веб-панель
  stop             остановить панель
  restart          перезапустить панель
  update           обновить Light проверенным production-релизом ветки light
  test-update [архив]
                   обновить из test-light или установить локальный test-архив (только сервисный режим)
  test-rollback    вернуться к production-версии, сохранённой перед test-сборкой
  install-release <архив>
                   вручную установить заранее собранный Linux-релиз без Docker, npm и apt
  status           показать состояние сервисов
  logs [api|web|gateway]
                   показать журналы (для выбранного сервиса — в реальном времени)
  verify           проверить API, веб-панель и привязку порта
  network-check    проверить интернет, панель и установленные WG/AWG-туннели
  integrity-check  проверить файлы, права, конфигурацию и компоненты приложения
  identity         повторно определить IP и геолокацию сервера
  secure           установить и включить базовую защиту системы
  system-update    установить доступные обновления системных пакетов
  kernel-update    обновить установленные метапакеты ядра Debian/Ubuntu
  vpn-firewall     восстановить маршрутизацию и NAT установленных WG/AWG
  optimize         очистить безопасные кэши и старые журналы
  automation-apply применить сохранённые расписания обслуживания
  protocol-version-check
                   проверить новые версии модулей протоколов без установки
  logging-config <enable|disable> <0..365>
                   настроить постоянную запись и срок хранения журналов
  logs-clear       очистить системные, контейнерные и мониторинговые журналы
  access-mode <external|vpn>
                   изменить доступность панели
  domain <имя|off> сохранить домен панели или отключить его
  service-mode <enable|disable>
                   включить или выключить сервисный режим
  reboot           перезагрузить сервер
  poweroff         выключить сервер
  protocol-install <id>
                   установить протокол из образа
  protocol-remove <id>
                   удалить протокол, сохранив образ
  protocol-update <id>
                   вручную обновить протокол с проверкой и откатом бинарного файла
  credentials      показать логин и пароль администратора
  help             показать эту справку
EOF
}

main() {
  require_root
  load_manager_config
  load_install_config
  case "${1:-help}" in
    install|install-release|uninstall|doctor|start|stop|restart|update|scheduled-app-update|test-update|test-rollback|verify|network-check|integrity-check|identity|secure|system-update|kernel-update|scheduled-kernel-update|vpn-firewall|optimize|automation-apply|logging-config|logs-clear|access-mode|domain|service-mode|reboot|poweroff|protocol-install|protocol-remove|protocol-update)
      case "${1}" in
        protocol-install|protocol-remove|protocol-update) begin_operation "${1}:${2:-}" ;;
        scheduled-app-update) begin_operation "update" ;;
        scheduled-kernel-update) begin_operation "kernel-update" ;;
        *) begin_operation "${1}" ;;
      esac
      trap handle_exit EXIT
      ;;
  esac
  case "${1:-help}" in
    install)
      UI_TOTAL=8
      ui_header
      ui_stage "Проверка сервера"
      doctor
      ui_done "сервер совместим"
      ui_stage "Системные зависимости"
      install_packages
      ensure_product_identity check
      ui_done "зависимости установлены"
      ui_stage "Сетевой доступ панели"
      configure_firewall
      ui_done "правила доступа применены"
      ui_stage "Подготовка источника обновлений"
      save_source_path
      ui_done "ветка light назначена источником релизов"
      ui_stage "Развёртывание локальной версии"
      deploy full
      ensure_product_identity
      verify_app
      ui_done "локальная версия установлена"
      ui_stage "Обновление до стабильной версии"
      update_app
      ui_done "установлен последний проверенный production-релиз Light"
      ui_stage "Проверка файлов и служб"
      verify_app
      integrity_check
      ui_done "целостность подтверждена"
      ui_stage "Завершение"
      ui_summary
      printf '\nОткройте: %s\n' "${PANEL_URL}"
      show_credentials
      printf 'Сохраните пароль: после завершения установки он не будет показан автоматически.\n'
      ui_done "установка завершена"
      ;;
    uninstall) uninstall_app "$@" ;;
    install-release) install_prebuilt_release "$@" ;;
    doctor) doctor ;;
    start) check_vpn; start_services ;;
    stop) stop_services ;;
    restart) check_vpn; restart_services ;;
    update) update_app ;;
    scheduled-app-update) scheduled_app_update ;;
    test-update) update_test_app "$@" ;;
    test-rollback) restore_test_app ;;
    status) status_app ;;
    logs) logs_app "$@" ;;
    verify) verify_app ;;
    network-check) network_check ;;
    integrity-check) integrity_check ;;
    identity)
      refresh_server_identity
      configure_firewall "panel-only"
      write_caddy_config
      systemctl restart "${APP_NAME}-api.service"
      restart_caddy_service
      verify_app
      ;;
    secure) secure_server ;;
    system-update) update_system_packages ;;
    kernel-update) update_kernel ;;
    scheduled-kernel-update) scheduled_kernel_update ;;
    vpn-firewall) configure_vpn_firewall_policy ;;
    optimize) optimize_resources ;;
    automation-apply) apply_automation ;;
    protocol-version-check) check_protocol_versions ;;
    logging-config) configure_logging "$@" ;;
    logs-clear) clear_managed_logs ;;
    access-mode) change_access_mode "$@" ;;
    domain) change_public_domain "$@" ;;
    service-mode) change_service_mode "$@" ;;
    reboot) reboot_server ;;
    poweroff) poweroff_server ;;
    protocol-install) install_protocol_image "$@" ;;
    protocol-remove) remove_protocol_image "$@" ;;
    protocol-update) update_protocol_image "$@" ;;
    credentials) show_credentials ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
