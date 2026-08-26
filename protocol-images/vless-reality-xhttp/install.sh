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
[[ "${TARGET}" != "www.microsoft.com:443" && "${TARGET}" != "www.apple.com:443" ]] || TARGET="www.intel.com:443"
[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1 && "${PORT}" -le 65535 ]] || { echo "Некорректный VLESS_REALITY_PORT" >&2; exit 1; }
[[ "${TARGET}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "Некорректный VLESS_REALITY_TARGET" >&2; exit 1; }
TARGET_HOST="${TARGET%:*}"

if ss -H -ltn "sport = :${PORT}" | grep -q . && ! systemctl is-active --quiet vps-control-vless-reality-xhttp.service 2>/dev/null; then
  echo "Порт ${PORT}/tcp уже занят другой службой" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl unzip

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
  umask 077
  printf 'PRIVATE_KEY=%s\nPUBLIC_KEY=%s\nSHORT_ID=%s\nXHTTP_PATH=%s\nTARGET=%s\nPORT=%s\n' \
    "${private_key}" "${public_key}" "${short_id}" "${path}" "${TARGET}" "${PORT}" >"${CONFIG_DIR}/reality.env"
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

env_setting() {
  sed -n "s/^${1}=//p" "${CONFIG_DIR}/reality.env" | tail -n 1
}
PRIVATE_KEY="$(env_setting PRIVATE_KEY)"
PUBLIC_KEY="$(env_setting PUBLIC_KEY)"
SHORT_ID="$(env_setting SHORT_ID)"
XHTTP_PATH="$(env_setting XHTTP_PATH)"
XHTTP_PATH="${XHTTP_PATH:-$(env_setting PATH)}"
[[ -n "${PRIVATE_KEY}" && -n "${PUBLIC_KEY}" && -n "${SHORT_ID}" && -n "${XHTTP_PATH}" ]] || { echo "Конфигурация REALITY неполна" >&2; exit 1; }
if ! grep -q '^XHTTP_PATH=' "${CONFIG_DIR}/reality.env"; then
  sed -i "s|^PATH=.*|XHTTP_PATH=${XHTTP_PATH}|" "${CONFIG_DIR}/reality.env"
fi
python3 - "${CONFIG_DIR}/config.json" "${PORT}" "${TARGET}" "${TARGET_HOST}" "${PRIVATE_KEY}" "${SHORT_ID}" "${XHTTP_PATH}" <<'PY'
import json, sys
output, port, target, host, private_key, short_id, path = sys.argv[1:]
existing_clients = []
try:
    with open(output, encoding="utf-8") as handle:
        existing_clients = json.load(handle)["inbounds"][0]["settings"].get("clients", [])
except (OSError, ValueError, KeyError, IndexError):
    pass
config = {
  "log": {"loglevel": "warning"},
  "stats": {},
  "api": {"tag": "api", "listen": "127.0.0.1:10085", "services": ["StatsService"]},
  "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
  "inbounds": [{
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
  }],
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

cat >/etc/systemd/system/vps-control-vless-reality-xhttp.service <<EOF
[Unit]
Description=312.net VLESS REALITY XHTTP
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${MODULE_DIR}/xray run -config ${CONFIG_DIR}/config.json
Restart=on-failure
RestartSec=3
User=nobody
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
systemctl enable --now vps-control-vless-reality-xhttp.service
for _ in {1..10}; do
  if systemctl is-active --quiet vps-control-vless-reality-xhttp.service && ss -H -ltn "sport = :${PORT}" | grep -q .; then
    break
  fi
  sleep 0.5
done
systemctl is-active --quiet vps-control-vless-reality-xhttp.service
ss -H -ltn "sport = :${PORT}" | grep -q . || { journalctl -u vps-control-vless-reality-xhttp.service -n 20 --no-pager >&2; exit 1; }
echo "VLESS + REALITY + XHTTP установлен на TCP ${PORT}."
