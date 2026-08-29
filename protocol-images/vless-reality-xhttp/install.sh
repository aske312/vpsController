#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"
MODULE_DIR="/usr/local/lib/vps-control-vless-reality-xhttp"
CONFIG_DIR="/etc/vps-control/vless-reality-xhttp"

setting() {
  local key="$1" fallback="$2" value=""
  value="$(sed -n "s/^${key}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')"
  printf '%s' "${value:-${fallback}}"
}

PORT="$(setting VLESS_REALITY_PORT 443)"
TARGET="$(setting VLESS_REALITY_TARGET www.intel.com:443)"
CDN_DOMAIN="$(setting VLESS_CDN_DOMAIN '')"
CDN_PORT="$(setting VLESS_CDN_PORT 10087)"
CDN_TRANSPORT="$(setting VLESS_CDN_TRANSPORT xhttp)"
CDN_XHTTP_MODE="$(setting VLESS_CDN_XHTTP_MODE auto)"
TLS_DOMAIN="$(setting VLESS_TLS_DOMAIN '')"
TLS_PORT="$(setting VLESS_TLS_PORT 10088)"
TLS_TRANSPORT="$(setting VLESS_TLS_TRANSPORT xhttp)"
TLS_XHTTP_MODE="$(setting VLESS_TLS_XHTTP_MODE auto)"
TLS_ENABLED="no"
[[ -z "${TLS_DOMAIN}" ]] || TLS_ENABLED="yes"
CDN_ENABLED="no"
[[ -z "${CDN_DOMAIN}" ]] || CDN_ENABLED="yes"
if [[ -s "${CONFIG_DIR}/reality.env" ]]; then
  saved_cdn_domain="$(sed -n 's/^CDN_DOMAIN=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_cdn_port="$(sed -n 's/^CDN_PORT=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_cdn_enabled="$(sed -n 's/^CDN_ENABLED=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_cdn_transport="$(sed -n 's/^CDN_TRANSPORT=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_cdn_xhttp_mode="$(sed -n 's/^CDN_XHTTP_MODE=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_tls_domain="$(sed -n 's/^TLS_DOMAIN=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_tls_enabled="$(sed -n 's/^TLS_ENABLED=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  saved_tls_transport="$(sed -n 's/^TLS_TRANSPORT=//p' "${CONFIG_DIR}/reality.env" | tail -n 1)"
  [[ -z "${saved_cdn_domain}" ]] || CDN_DOMAIN="${saved_cdn_domain}"
  [[ -z "${saved_cdn_port}" ]] || CDN_PORT="${saved_cdn_port}"
  [[ -z "${saved_cdn_enabled}" ]] || CDN_ENABLED="${saved_cdn_enabled}"
  [[ -z "${saved_cdn_transport}" ]] || CDN_TRANSPORT="${saved_cdn_transport}"
  [[ -z "${saved_cdn_xhttp_mode}" ]] || CDN_XHTTP_MODE="${saved_cdn_xhttp_mode}"
  [[ -z "${saved_tls_domain}" ]] || TLS_DOMAIN="${saved_tls_domain}"
  [[ -z "${saved_tls_enabled}" ]] || TLS_ENABLED="${saved_tls_enabled}"
  [[ -z "${saved_tls_transport}" ]] || TLS_TRANSPORT="${saved_tls_transport}"
fi
[[ "${TARGET}" != "www.microsoft.com:443" && "${TARGET}" != "www.apple.com:443" ]] || TARGET="www.intel.com:443"
[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1 && "${PORT}" -le 65535 ]] || { echo "Некорректный VLESS_REALITY_PORT" >&2; exit 1; }
[[ "${TARGET}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "Некорректный VLESS_REALITY_TARGET" >&2; exit 1; }
[[ "${CDN_PORT}" =~ ^[0-9]+$ && "${CDN_PORT}" -ge 1024 && "${CDN_PORT}" -le 65535 ]] || { echo "Некорректный VLESS_CDN_PORT" >&2; exit 1; }
[[ "${CDN_PORT}" != "${PORT}" ]] || { echo "VLESS_CDN_PORT конфликтует с VLESS_REALITY_PORT" >&2; exit 1; }
[[ -z "${CDN_DOMAIN}" || "${CDN_DOMAIN}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] \
  || { echo "Некорректный VLESS_CDN_DOMAIN" >&2; exit 1; }
[[ "${CDN_ENABLED}" == "yes" || "${CDN_ENABLED}" == "no" ]] || { echo "Некорректный CDN_ENABLED" >&2; exit 1; }
[[ "${CDN_TRANSPORT}" == "websocket" || "${CDN_TRANSPORT}" == "xhttp" || "${CDN_TRANSPORT}" == "httpupgrade" || "${CDN_TRANSPORT}" == "grpc" ]] || { echo "Некорректный CDN transport" >&2; exit 1; }
[[ "${CDN_XHTTP_MODE}" == "auto" || "${CDN_XHTTP_MODE}" == "stream-one" || "${CDN_XHTTP_MODE}" == "stream-up" || "${CDN_XHTTP_MODE}" == "packet-up" ]] || { echo "Некорректный CDN XHTTP mode" >&2; exit 1; }
[[ "${CDN_ENABLED}" != "yes" || -n "${CDN_DOMAIN}" ]] || { echo "Для включения CDN необходим домен" >&2; exit 1; }
[[ -z "${TLS_DOMAIN}" || "${TLS_DOMAIN}" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || { echo "Некорректный VLESS_TLS_DOMAIN" >&2; exit 1; }
[[ "${TLS_PORT}" =~ ^[0-9]+$ && "${TLS_PORT}" -ge 1024 && "${TLS_PORT}" -le 65535 && "${TLS_PORT}" != "${CDN_PORT}" ]] || { echo "Некорректный VLESS_TLS_PORT" >&2; exit 1; }
[[ "${TLS_TRANSPORT}" == "websocket" || "${TLS_TRANSPORT}" == "xhttp" || "${TLS_TRANSPORT}" == "httpupgrade" || "${TLS_TRANSPORT}" == "grpc" ]] || { echo "Некорректный TLS transport" >&2; exit 1; }
if [[ "${TLS_ENABLED}" == "yes" ]]; then
  if [[ -n "${CDN_DOMAIN}" && "${TLS_DOMAIN}" == "${CDN_DOMAIN}" ]]; then
    echo "VLESS TLS пропущен: прямой TLS и CDN требуют разные домены." >&2
    TLS_ENABLED="no"
  fi
  public_ipv4="$(setting PUBLIC_IPV4 '')"
  resolved_ipv4="$(getent ahostsv4 "${TLS_DOMAIN}" 2>/dev/null | awk 'NR==1{print $1}')"
  if [[ -z "${public_ipv4}" || "${resolved_ipv4}" != "${public_ipv4}" ]]; then
    echo "VLESS TLS пропущен: домен ${TLS_DOMAIN} пока не указывает на IPv4 этого VPS." >&2
    TLS_ENABLED="no"
  fi
fi
TARGET_HOST="${TARGET%:*}"

if ss -H -ltn "sport = :${PORT}" | grep -q . && ! systemctl is-active --quiet vps-control-vless-reality-xhttp.service 2>/dev/null; then
  echo "Порт ${PORT}/tcp уже занят другой службой" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl unzip iptables

case "$(dpkg --print-architecture)" in
  amd64) asset="Xray-linux-64.zip" ;;
  arm64) asset="Xray-linux-arm64-v8a.zip" ;;
  *) echo "Архитектура не поддерживается Xray image" >&2; exit 1 ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
latest_url="$(curl -fsSL --retry 3 -o /dev/null -w '%{url_effective}' https://github.com/XTLS/Xray-core/releases/latest)"
release_tag="${latest_url##*/}"
[[ "${release_tag}" =~ ^v[0-9][0-9A-Za-z._-]*$ ]] || { echo "Не удалось определить последний официальный релиз Xray" >&2; exit 1; }
download_url="https://github.com/XTLS/Xray-core/releases/download/${release_tag}/${asset}"
digest_url="${download_url}.dgst"
curl -fL --retry 3 -o "${tmp_dir}/${asset}" "${download_url}"
curl -fL --retry 3 -o "${tmp_dir}/${asset}.dgst" "${digest_url}"
expected="$(grep -Eio '[0-9a-f]{64}' "${tmp_dir}/${asset}.dgst" | head -n 1 | tr 'A-F' 'a-f')"
actual="$(sha256sum "${tmp_dir}/${asset}" | awk '{print $1}')"
[[ -n "${expected}" && "${actual}" == "${expected}" ]] || { echo "Контрольная сумма Xray не совпала" >&2; exit 1; }
unzip -q "${tmp_dir}/${asset}" xray -d "${tmp_dir}"
"${tmp_dir}/xray" version >/dev/null

install -d -m 0755 "${MODULE_DIR}"

if [[ "${XRAY_UPDATE_ONLY:-}" == "1" ]]; then
  candidate="${MODULE_DIR}/.xray.new"
  previous="${MODULE_DIR}/.xray.previous"
  install -m 0755 "${tmp_dir}/xray" "${candidate}"
  "${candidate}" run -test -config "${CONFIG_DIR}/config.json"
  [[ ! -x "${MODULE_DIR}/xray" ]] || cp -p -- "${MODULE_DIR}/xray" "${previous}"
  mv -f -- "${candidate}" "${MODULE_DIR}/xray"
  update_ready=false
  if systemctl restart vps-control-vless-reality-xhttp.service; then
    for _ in {1..10}; do
      if systemctl is-active --quiet vps-control-vless-reality-xhttp.service && ss -H -ltn "sport = :${PORT}" | grep -q .; then
        update_ready=true
        break
      fi
      sleep 0.5
    done
  fi
  if [[ "${update_ready}" != "true" ]]; then
    journalctl -u vps-control-vless-reality-xhttp.service -n 20 --no-pager >&2 || true
    if [[ -x "${previous}" ]]; then
      mv -f -- "${previous}" "${MODULE_DIR}/xray"
      systemctl restart vps-control-vless-reality-xhttp.service || true
    fi
    echo "Новая версия Xray не запустилась; восстановлена предыдущая." >&2
    exit 1
  fi
  rm -f -- "${previous}"
  echo "Xray обновлён и запущен: $("${MODULE_DIR}/xray" version | head -n1)."
  exit 0
fi

install -m 0755 "${tmp_dir}/xray" "${MODULE_DIR}/xray"

install -d -m 0750 -o root -g nogroup "${CONFIG_DIR}"

tls_probe="$("${MODULE_DIR}/xray" tls ping "${TARGET}" 2>&1)"
grep -q 'Handshake succeeded' <<<"${tls_probe}" \
  || { echo "REALITY target ${TARGET} не завершает TLS handshake" >&2; exit 1; }
certificate_length="$(sed -n -E 's/.*total length:[[:space:]]*([0-9]+).*/\1/p' <<<"${tls_probe}" | tail -n 1)"
[[ "${certificate_length}" =~ ^[0-9]+$ && "${certificate_length}" -le 3500 ]] \
  || { echo "REALITY target ${TARGET} использует слишком большую TLS-цепочку" >&2; exit 1; }

if [[ ! -s "${CONFIG_DIR}/reality.env" ]]; then
  key_output="$("${MODULE_DIR}/xray" x25519)"
  private_key="$(sed -n 's/^PrivateKey:[[:space:]]*//p' <<<"${key_output}" | head -n 1)"
  public_key="$(sed -n -E 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' <<<"${key_output}" | head -n 1)"
  [[ -n "${private_key}" && -n "${public_key}" ]] || { echo "Xray не создал ключи REALITY" >&2; exit 1; }
  short_id="$(openssl rand -hex 8)"
  path="/$(openssl rand -hex 12)"
  ws_path="/$(openssl rand -hex 16)"
  umask 077
  printf 'PRIVATE_KEY=%s\nPUBLIC_KEY=%s\nSHORT_ID=%s\nXHTTP_PATH=%s\nWS_PATH=%s\nCDN_PATH=%s\nTARGET=%s\nPORT=%s\nCDN_ENABLED=%s\nCDN_DOMAIN=%s\nCDN_PORT=%s\nCDN_TRANSPORT=%s\nCDN_XHTTP_MODE=%s\n' \
    "${private_key}" "${public_key}" "${short_id}" "${path}" "${ws_path}" "${ws_path}" "${TARGET}" "${PORT}" "${CDN_ENABLED}" "${CDN_DOMAIN}" "${CDN_PORT}" "${CDN_TRANSPORT}" "${CDN_XHTTP_MODE}" >"${CONFIG_DIR}/reality.env"
fi

if grep -q '^TARGET=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^TARGET=.*|TARGET=${TARGET}|" "${CONFIG_DIR}/reality.env"
else
  printf 'TARGET=%s\n' "${TARGET}" >>"${CONFIG_DIR}/reality.env"
fi
if grep -q '^PORT=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^PORT=.*|PORT=${PORT}|" "${CONFIG_DIR}/reality.env"
else
  printf 'PORT=%s\n' "${PORT}" >>"${CONFIG_DIR}/reality.env"
fi
if grep -q '^CDN_DOMAIN=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^CDN_DOMAIN=.*|CDN_DOMAIN=${CDN_DOMAIN}|" "${CONFIG_DIR}/reality.env"
else
  printf 'CDN_DOMAIN=%s\n' "${CDN_DOMAIN}" >>"${CONFIG_DIR}/reality.env"
fi
if grep -q '^CDN_ENABLED=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^CDN_ENABLED=.*|CDN_ENABLED=${CDN_ENABLED}|" "${CONFIG_DIR}/reality.env"
else
  printf 'CDN_ENABLED=%s\n' "${CDN_ENABLED}" >>"${CONFIG_DIR}/reality.env"
fi
if grep -q '^CDN_PORT=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^CDN_PORT=.*|CDN_PORT=${CDN_PORT}|" "${CONFIG_DIR}/reality.env"
else
  printf 'CDN_PORT=%s\n' "${CDN_PORT}" >>"${CONFIG_DIR}/reality.env"
fi
for pair in "CDN_TRANSPORT=${CDN_TRANSPORT}" "CDN_XHTTP_MODE=${CDN_XHTTP_MODE}"; do
  key="${pair%%=*}"; value="${pair#*=}"
  if grep -q "^${key}=" "${CONFIG_DIR}/reality.env"; then sed -i "s|^${key}=.*|${key}=${value}|" "${CONFIG_DIR}/reality.env"; else printf '%s=%s\n' "${key}" "${value}" >>"${CONFIG_DIR}/reality.env"; fi
done
env_setting() {
  sed -n "s/^${1}=//p" "${CONFIG_DIR}/reality.env" | tail -n 1
}
PRIVATE_KEY="$(env_setting PRIVATE_KEY)"
PUBLIC_KEY="$(env_setting PUBLIC_KEY)"
SHORT_ID="$(env_setting SHORT_ID)"
XHTTP_PATH="$(env_setting XHTTP_PATH)"
XHTTP_PATH="${XHTTP_PATH:-$(env_setting PATH)}"
WS_PATH="$(env_setting WS_PATH)"
CDN_PATH="$(env_setting CDN_PATH)"
if [[ -z "${WS_PATH}" ]]; then
  WS_PATH="/$(openssl rand -hex 16)"
  printf 'WS_PATH=%s\n' "${WS_PATH}" >>"${CONFIG_DIR}/reality.env"
fi
CDN_PATH="${CDN_PATH:-${WS_PATH}}"
if ! grep -q '^CDN_PATH=' "${CONFIG_DIR}/reality.env"; then printf 'CDN_PATH=%s\n' "${CDN_PATH}" >>"${CONFIG_DIR}/reality.env"; fi
for pair in "TLS_ENABLED=${TLS_ENABLED}" "TLS_DOMAIN=${TLS_DOMAIN}" "TLS_PORT=${TLS_PORT}" "TLS_PATH=${CDN_PATH}-tls" "TLS_TRANSPORT=${TLS_TRANSPORT}" "TLS_XHTTP_MODE=${TLS_XHTTP_MODE}"; do
  key="${pair%%=*}"
  if grep -q "^${key}=" "${CONFIG_DIR}/reality.env"; then sed -i "s|^${key}=.*|${pair}|" "${CONFIG_DIR}/reality.env"; else printf '%s\n' "${pair}" >>"${CONFIG_DIR}/reality.env"; fi
done
[[ -n "${PRIVATE_KEY}" && -n "${PUBLIC_KEY}" && -n "${SHORT_ID}" && -n "${XHTTP_PATH}" && -n "${WS_PATH}" ]] || { echo "Конфигурация VLESS неполна" >&2; exit 1; }
if ! grep -q '^XHTTP_PATH=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^PATH=.*|XHTTP_PATH=${XHTTP_PATH}|" "${CONFIG_DIR}/reality.env"
fi
python3 - "${CONFIG_DIR}/config.json" "${PORT}" "${TARGET}" "${TARGET_HOST}" "${PRIVATE_KEY}" "${SHORT_ID}" "${XHTTP_PATH}" "${CDN_DOMAIN}" "${CDN_PORT}" "${CDN_PATH}" "${CDN_ENABLED}" "${CDN_TRANSPORT}" "${CDN_XHTTP_MODE}" "${TLS_DOMAIN}" "${TLS_PORT}" "${CDN_PATH}-tls" "${TLS_ENABLED}" "${TLS_TRANSPORT}" "${TLS_XHTTP_MODE}" <<'PY'
import json, sys
output, port, target, host, private_key, short_id, path, cdn_domain, cdn_port, cdn_path, cdn_enabled, cdn_transport, cdn_xhttp_mode, tls_domain, tls_port, tls_path, tls_enabled, tls_transport, tls_xhttp_mode = sys.argv[1:]
existing_clients_by_id = {}
try:
    with open(output, encoding="utf-8") as handle:
        for inbound in json.load(handle).get("inbounds", []):
            if inbound.get("protocol") != "vless":
                continue
            for client in inbound.get("settings", {}).get("clients", []):
                if client.get("id"):
                    existing_clients_by_id[client["id"]] = client
except (OSError, ValueError, AttributeError):
    pass
existing_clients = list(existing_clients_by_id.values())
inbounds = [{
  "tag": "vless-reality-xhttp", "listen": "::", "port": int(port), "protocol": "vless",
  "settings": {"clients": existing_clients, "decryption": "none"},
  "streamSettings": {
    "network": "xhttp", "security": "reality", "xhttpSettings": {"path": path, "mode": "auto", "extra": {
      "xPaddingBytes": "100-1000", "xmux": {"maxConcurrency": "8-16", "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000"}
    }},
    "realitySettings": {
      "show": False, "target": target, "xver": 0, "serverNames": [host],
      "privateKey": private_key, "shortIds": [short_id],
      "limitFallbackUpload": {"afterBytes": 1048576, "bytesPerSec": 262144, "burstBytesPerSec": 524288},
      "limitFallbackDownload": {"afterBytes": 1048576, "bytesPerSec": 262144, "burstBytesPerSec": 524288}
    }
  },
  "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
}]
if cdn_enabled == "yes" and cdn_domain:
    cdn_stream = {"network": cdn_transport, "security": "none"}
    if cdn_transport == "xhttp":
        cdn_stream["xhttpSettings"] = {"path": cdn_path, "mode": cdn_xhttp_mode}
    elif cdn_transport == "grpc":
        cdn_stream["grpcSettings"] = {"serviceName": cdn_path.lstrip("/") or "vless", "multiMode": False}
    elif cdn_transport == "httpupgrade":
        cdn_stream["httpupgradeSettings"] = {"path": cdn_path}
    else:
        cdn_stream["network"] = "websocket"
        cdn_stream["wsSettings"] = {"path": cdn_path}
    inbounds.append({
      "tag": "vless-cdn", "listen": "127.0.0.1", "port": int(cdn_port), "protocol": "vless",
      "settings": {"clients": existing_clients, "decryption": "none"},
      "streamSettings": cdn_stream,
      "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
    })
if tls_enabled == "yes" and tls_domain:
    tls_stream = {"network": tls_transport, "security": "none"}
    if tls_transport == "xhttp": tls_stream["xhttpSettings"] = {"path": tls_path, "mode": tls_xhttp_mode}
    elif tls_transport == "grpc": tls_stream["grpcSettings"] = {"serviceName": tls_path.lstrip("/"), "multiMode": False}
    elif tls_transport == "httpupgrade": tls_stream["httpupgradeSettings"] = {"path": tls_path}
    else: tls_stream.update({"network": "websocket", "wsSettings": {"path": tls_path}})
    inbounds.append({"tag": "vless-tls", "listen": "127.0.0.1", "port": int(tls_port), "protocol": "vless", "settings": {"clients": existing_clients, "decryption": "none"}, "streamSettings": tls_stream})
config = {
  "log": {"loglevel": "warning"},
  "stats": {},
  "api": {"tag": "api", "listen": "127.0.0.1:10085", "services": ["StatsService"]},
  "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
  "inbounds": inbounds,
  "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "blocked"}],
  "routing": {"domainStrategy": "AsIs", "rules": [{"type": "field", "ip": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"], "outboundTag": "blocked"}]}
}
with open(output, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
PY
chown root:nogroup "${CONFIG_DIR}/config.json"
chmod 0640 "${CONFIG_DIR}/config.json"
chmod 0600 "${CONFIG_DIR}/reality.env"
"${MODULE_DIR}/xray" run -test -config "${CONFIG_DIR}/config.json"

CADDY_SNIPPET_DIR="/etc/caddy/vps-control.d"
CADDY_SNIPPET="${CADDY_SNIPPET_DIR}/vless-cdn.caddy"
PREVIOUS_CADDY_SNIPPET="${tmp_dir}/vless-cdn.caddy.previous"
HAD_CADDY_SNIPPET="no"
install -d -m 0755 "${CADDY_SNIPPET_DIR}"
if [[ -f "${CADDY_SNIPPET}" ]]; then
  cp -p -- "${CADDY_SNIPPET}" "${PREVIOUS_CADDY_SNIPPET}"
  HAD_CADDY_SNIPPET="yes"
fi
rm -f -- "${CADDY_SNIPPET}.tmp"
if [[ "${CDN_ENABLED}" == "yes" && -n "${CDN_DOMAIN}" ]]; then
  cat >"${CADDY_SNIPPET}.tmp" <<EOF
${CDN_DOMAIN} {
    handle ${CDN_PATH}$([[ "${CDN_TRANSPORT}" == "websocket" ]] || printf '*') {
        reverse_proxy $([[ "${CDN_TRANSPORT}" == "grpc" ]] && printf 'h2c://')127.0.0.1:${CDN_PORT}
    }
    respond 404
}
EOF
fi
if [[ "${TLS_ENABLED}" == "yes" && -n "${TLS_DOMAIN}" ]]; then
  cat >>"${CADDY_SNIPPET}.tmp" <<EOF
${TLS_DOMAIN} {
    handle ${CDN_PATH}-tls$([[ "${TLS_TRANSPORT}" == "websocket" || "${TLS_TRANSPORT}" == "httpupgrade" ]] || printf '*') {
        reverse_proxy $([[ "${TLS_TRANSPORT}" == "grpc" ]] && printf 'h2c://')127.0.0.1:${TLS_PORT}
    }
    respond 404
}
EOF
fi
if [[ -s "${CADDY_SNIPPET}.tmp" ]]; then
  chmod 0644 "${CADDY_SNIPPET}.tmp"
  mv -f -- "${CADDY_SNIPPET}.tmp" "${CADDY_SNIPPET}"
else
  rm -f -- "${CADDY_SNIPPET}" "${CADDY_SNIPPET}.tmp"
fi
if command -v caddy >/dev/null 2>&1 && [[ -s /etc/caddy/Caddyfile ]]; then
  if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null; then
    if [[ "${HAD_CADDY_SNIPPET}" == "yes" ]]; then
      cp -p -- "${PREVIOUS_CADDY_SNIPPET}" "${CADDY_SNIPPET}"
    else
      rm -f -- "${CADDY_SNIPPET}"
    fi
    echo "Caddy отклонил конфигурацию VLESS CDN; предыдущий маршрут восстановлен." >&2
    exit 1
  fi
fi

cat >/etc/systemd/system/vps-control-vless-reality-xhttp.service <<EOF
[Unit]
Description=312.net VLESS REALITY XHTTP
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=+/bin/sh -ec '/usr/sbin/iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null || /usr/sbin/iptables -I INPUT 1 -p tcp --dport ${PORT} -j ACCEPT'
ExecStart=${MODULE_DIR}/xray run -config ${CONFIG_DIR}/config.json
ExecStopPost=+/bin/sh -ec 'while /usr/sbin/iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null; do /usr/sbin/iptables -D INPUT -p tcp --dport ${PORT} -j ACCEPT; done'
Restart=on-failure
RestartSec=3
DynamicUser=yes
SupplementaryGroups=nogroup
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null 2>&1 && [[ "${ENABLE_UFW:-yes}" == "yes" ]]; then
  ufw allow "${PORT}/tcp" comment '312.net VLESS REALITY XHTTP'
fi
systemctl daemon-reload
systemctl enable vps-control-vless-reality-xhttp.service >/dev/null
systemctl restart vps-control-vless-reality-xhttp.service
if command -v caddy >/dev/null 2>&1 && systemctl is-active --quiet caddy.service; then
  if ! systemctl reload caddy.service; then
    if [[ "${HAD_CADDY_SNIPPET}" == "yes" ]]; then
      cp -p -- "${PREVIOUS_CADDY_SNIPPET}" "${CADDY_SNIPPET}"
    else
      rm -f -- "${CADDY_SNIPPET}"
    fi
    systemctl reload caddy.service || true
    echo "Caddy не применил VLESS CDN; предыдущий маршрут восстановлен." >&2
    exit 1
  fi
fi
for _ in {1..10}; do
  if systemctl is-active --quiet vps-control-vless-reality-xhttp.service && ss -H -ltn "sport = :${PORT}" | grep -q .; then
    break
  fi
  sleep 0.5
done
systemctl is-active --quiet vps-control-vless-reality-xhttp.service
ss -H -ltn "sport = :${PORT}" | grep -q . || { journalctl -u vps-control-vless-reality-xhttp.service -n 20 --no-pager >&2; exit 1; }
echo "VLESS + REALITY + XHTTP установлен на TCP ${PORT}."
[[ "${CDN_ENABLED}" != "yes" || -z "${CDN_DOMAIN}" ]] || echo "VLESS CDN WebSocket подготовлен: ${CDN_DOMAIN}:443 -> 127.0.0.1:${CDN_PORT}."
