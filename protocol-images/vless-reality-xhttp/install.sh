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
TARGET="$(setting VLESS_REALITY_TARGET www.microsoft.com:443)"
[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1 && "${PORT}" -le 65535 ]] || { echo "Некорректный VLESS_REALITY_PORT" >&2; exit 1; }
[[ "${TARGET}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "Некорректный VLESS_REALITY_TARGET" >&2; exit 1; }
TARGET_HOST="${TARGET%:*}"

if ss -H -ltn "sport = :${PORT}" | grep -q . && ! systemctl is-active --quiet vps-control-vless-reality-xhttp.service 2>/dev/null; then
  echo "Порт ${PORT}/tcp уже занят другой службой" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl unzip

case "$(dpkg --print-architecture)" in
  amd64) asset="Xray-linux-64.zip" ;;
  arm64) asset="Xray-linux-arm64-v8a.zip" ;;
  *) echo "Архитектура не поддерживается Xray image" >&2; exit 1 ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
release_json="$(curl -fsSL --retry 3 https://api.github.com/repos/XTLS/Xray-core/releases/latest)"
download_url="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release_json}")"
digest_url="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]+".dgst"; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release_json}")"
[[ -n "${download_url}" && -n "${digest_url}" ]] || { echo "Не найдены официальные assets Xray" >&2; exit 1; }
curl -fL --retry 3 -o "${tmp_dir}/${asset}" "${download_url}"
curl -fL --retry 3 -o "${tmp_dir}/${asset}.dgst" "${digest_url}"
expected="$(grep -Eio '[0-9a-f]{64}' "${tmp_dir}/${asset}.dgst" | head -n 1 | tr 'A-F' 'a-f')"
actual="$(sha256sum "${tmp_dir}/${asset}" | awk '{print $1}')"
[[ -n "${expected}" && "${actual}" == "${expected}" ]] || { echo "Контрольная сумма Xray не совпала" >&2; exit 1; }
unzip -q "${tmp_dir}/${asset}" xray -d "${tmp_dir}"

install -d -m 0755 "${MODULE_DIR}"
install -m 0755 "${tmp_dir}/xray" "${MODULE_DIR}/xray"
install -d -m 0750 -o root -g nogroup "${CONFIG_DIR}"

if [[ ! -s "${CONFIG_DIR}/reality.env" ]]; then
  key_output="$("${MODULE_DIR}/xray" x25519)"
  private_key="$(sed -n 's/^PrivateKey:[[:space:]]*//p' <<<"${key_output}" | head -n 1)"
  public_key="$(sed -n -E 's/^(Password|PublicKey):[[:space:]]*//p' <<<"${key_output}" | head -n 1)"
  [[ -n "${private_key}" && -n "${public_key}" ]] || { echo "Xray не создал ключи REALITY" >&2; exit 1; }
  short_id="$(openssl rand -hex 8)"
  path="/$(openssl rand -hex 12)"
  umask 077
  printf 'PRIVATE_KEY=%s\nPUBLIC_KEY=%s\nSHORT_ID=%s\nPATH=%s\nTARGET=%s\nPORT=%s\n' \
    "${private_key}" "${public_key}" "${short_id}" "${path}" "${TARGET}" "${PORT}" >"${CONFIG_DIR}/reality.env"
fi

set -a
# shellcheck disable=SC1091
source "${CONFIG_DIR}/reality.env"
set +a
python3 - "${CONFIG_DIR}/config.json" "${PORT}" "${TARGET}" "${TARGET_HOST}" "${PRIVATE_KEY}" "${SHORT_ID}" "${PATH}" <<'PY'
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
  "inbounds": [{
    "tag": "vless-reality-xhttp", "listen": "0.0.0.0", "port": int(port), "protocol": "vless",
    "settings": {"clients": existing_clients, "decryption": "none"},
    "streamSettings": {
      "network": "xhttp", "security": "reality", "xhttpSettings": {"path": path, "mode": "auto"},
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
systemctl is-active --quiet vps-control-vless-reality-xhttp.service
echo "VLESS + REALITY + XHTTP установлен на TCP ${PORT}."
