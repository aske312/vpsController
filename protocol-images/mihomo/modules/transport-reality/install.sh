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

MODULE_DIR="/usr/local/lib/vps-control-mihomo-reality"
CONFIG_DIR="/etc/vps-control/mihomo/reality"
PORT="$(setting port 9443)"
TARGET="$(setting target www.intel.com:443)"
TARGET_HOST="${TARGET%:*}"

[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1024 && "${PORT}" -le 65535 ]] || { echo "Некорректный порт" >&2; exit 1; }
[[ "${TARGET}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "Некорректный REALITY target" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl unzip

install -d -m 0755 "${MODULE_DIR}"
if [[ -x /usr/local/lib/vps-control-vless-reality-xhttp/xray ]]; then
  install -m 0755 /usr/local/lib/vps-control-vless-reality-xhttp/xray "${MODULE_DIR}/xray"
elif [[ ! -x "${MODULE_DIR}/xray" ]]; then
  case "$(dpkg --print-architecture)" in
    amd64) asset="Xray-linux-64.zip" ;;
    arm64) asset="Xray-linux-arm64-v8a.zip" ;;
    *) echo "Архитектура Xray не поддерживается" >&2; exit 1 ;;
  esac
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' EXIT
  release="$(curl -fsSL --retry 3 https://api.github.com/repos/XTLS/Xray-core/releases/latest)"
  url="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release}")"
  dgst="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]+".dgst"; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release}")"
  [[ -n "${url}" && -n "${dgst}" ]] || { echo "Xray assets не найдены" >&2; exit 1; }
  curl -fL --retry 3 -o "${tmp}/${asset}" "${url}"
  curl -fL --retry 3 -o "${tmp}/${asset}.dgst" "${dgst}"
  expected="$(grep -Eio '[0-9a-f]{64}' "${tmp}/${asset}.dgst" | head -n1 | tr 'A-F' 'a-f')"
  actual="$(sha256sum "${tmp}/${asset}" | awk '{print $1}')"
  [[ -n "${expected}" && "${expected}" == "${actual}" ]] || { echo "Xray SHA256 mismatch" >&2; exit 1; }
  unzip -q "${tmp}/${asset}" xray -d "${tmp}"
  install -m 0755 "${tmp}/xray" "${MODULE_DIR}/xray"
fi

install -d -m 0700 "${CONFIG_DIR}"
tls_probe="$("${MODULE_DIR}/xray" tls ping "${TARGET}" 2>&1)"
grep -q 'Handshake succeeded' <<<"${tls_probe}" || { echo "REALITY target не проходит TLS probe" >&2; exit 1; }

if [[ ! -s "${CONFIG_DIR}/reality.env" ]]; then
  keys="$("${MODULE_DIR}/xray" x25519)"
  private="$(sed -n 's/^PrivateKey:[[:space:]]*//p' <<<"${keys}" | head -n1)"
  public="$(sed -n -E 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' <<<"${keys}" | head -n1)"
  short_id="$(openssl rand -hex 8)"
  path="/$(openssl rand -hex 12)"
  printf 'PRIVATE_KEY=%s\nPUBLIC_KEY=%s\nSHORT_ID=%s\nXHTTP_PATH=%s\n' "${private}" "${public}" "${short_id}" "${path}" >"${CONFIG_DIR}/reality.env"
fi

sed -i '/^TARGET=/d;/^PORT=/d' "${CONFIG_DIR}/reality.env"
printf 'TARGET=%s\nPORT=%s\n' "${TARGET}" "${PORT}" >>"${CONFIG_DIR}/reality.env"
chmod 0600 "${CONFIG_DIR}/reality.env"
source "${CONFIG_DIR}/reality.env"

python3 - "${CONFIG_DIR}/config.json" "${PORT}" "${TARGET}" "${TARGET_HOST}" "${PRIVATE_KEY}" "${SHORT_ID}" "${XHTTP_PATH}" <<'PY'
import json, os, sys
output, port, target, host, private_key, short_id, path = sys.argv[1:]
clients = []
try:
    with open(output, encoding="utf-8") as h:
        clients = json.load(h)["inbounds"][0]["settings"].get("clients", [])
except (OSError, ValueError, KeyError, IndexError):
    pass
config = {
  "log": {"loglevel": "warning"},
  "inbounds": [{
    "tag": "mihomo-reality", "listen": "::", "port": int(port), "protocol": "vless",
    "settings": {"clients": clients, "decryption": "none"},
    "streamSettings": {
      "network": "xhttp", "security": "reality",
      "xhttpSettings": {"path": path, "mode": "auto"},
      "realitySettings": {
        "show": False, "target": target, "xver": 0, "serverNames": [host],
        "privateKey": private_key, "shortIds": [short_id]
      }
    }
  }],
  "outbounds": [{"protocol": "freedom", "tag": "direct"}]
}
with open(output, "w", encoding="utf-8") as h:
    json.dump(config, h, ensure_ascii=False, indent=2)
os.chmod(output, 0o640)
PY
chown root:nogroup "${CONFIG_DIR}/config.json"

"${MODULE_DIR}/xray" run -test -config "${CONFIG_DIR}/config.json"

cat >/etc/systemd/system/vps-control-mihomo-reality.service <<EOF
[Unit]
Description=GATE.312 Mihomo VLESS Reality XHTTP
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
ProtectHome=true
[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow "${PORT}/tcp" comment 'GATE.312 Mihomo Reality' >/dev/null || true
fi
systemctl daemon-reload
systemctl enable --now vps-control-mihomo-reality.service
systemctl restart vps-control-mihomo-reality.service
systemctl is-active --quiet vps-control-mihomo-reality.service
echo "Mihomo/Reality: TCP ${PORT}, target ${TARGET}"
