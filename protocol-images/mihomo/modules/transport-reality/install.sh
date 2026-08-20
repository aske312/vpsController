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

# Mihomo Reality owns its Xray runtime. Never copy the direct VRX runtime:
# the two modules must remain independently upgradeable and removable.
# Track upstream latest (like the direct VRX module already does) instead of
# a hardcoded version, so the "update available" check and this installer
# agree on what "latest" means, and re-running this (e.g. on a settings
# save) picks up new releases automatically.
release_json="$(curl -fsSL --retry 4 https://api.github.com/repos/XTLS/Xray-core/releases/latest)"
XRAY_VERSION="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name","").lstrip("v"))' <<<"${release_json}")"
[[ -n "${XRAY_VERSION}" ]] || { echo "Не удалось определить последнюю версию Xray" >&2; exit 1; }

current_xray_version=""
if [[ -x "${MODULE_DIR}/xray" ]]; then
  current_xray_version="$("${MODULE_DIR}/xray" version 2>/dev/null | head -n1 || true)"
fi

if [[ "${current_xray_version}" != *"${XRAY_VERSION}"* ]]; then
  case "$(dpkg --print-architecture)" in
    amd64) asset="Xray-linux-64.zip" ;;
    arm64) asset="Xray-linux-arm64-v8a.zip" ;;
    *) echo "Архитектура Xray не поддерживается" >&2; exit 1 ;;
  esac

  download_url="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release_json}")"
  digest_url="$(python3 -c 'import json,sys; d=json.load(sys.stdin); n=sys.argv[1]+".dgst"; print(next((a["browser_download_url"] for a in d["assets"] if a["name"]==n), ""))' "${asset}" <<<"${release_json}")"
  [[ -n "${download_url}" && -n "${digest_url}" ]] || { echo "Не найдены официальные assets Xray" >&2; exit 1; }

  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp}"' EXIT

  curl -fL --retry 4 -o "${tmp}/${asset}" "${download_url}"
  curl -fL --retry 4 -o "${tmp}/${asset}.dgst" "${digest_url}"

  expected="$(grep -Eio '[0-9a-f]{64}' "${tmp}/${asset}.dgst" | head -n1 | tr 'A-F' 'a-f')"
  actual="$(sha256sum "${tmp}/${asset}" | awk '{print $1}')"
  [[ -n "${expected}" && "${expected}" == "${actual}" ]] || {
    echo "Xray SHA256 mismatch" >&2
    exit 1
  }

  unzip -q -o "${tmp}/${asset}" xray -d "${tmp}"
  install -m 0755 "${tmp}/xray" "${MODULE_DIR}/xray"
  rm -rf "${tmp}"
  trap - EXIT
fi

"${MODULE_DIR}/xray" version | head -n1 | grep -F "${XRAY_VERSION}" >/dev/null || {
  echo "Mihomo Reality: установлен неожиданный Xray build" >&2
  exit 1
}

if [[ "${XRAY_UPDATE_ONLY:-}" == "1" ]]; then
  # Binary-only update: the on-disk Xray build is now current, but the
  # running service still has the old one loaded in memory. Leave it be —
  # restarting here would drop every live profile connection without being
  # asked. An admin restart from the Services page picks up the new binary.
  echo "Mihomo/Reality Xray обновлён до ${XRAY_VERSION}; служба не перезапущена — перезапустите вручную (Службы), чтобы применить."
  exit 0
fi

install -d -o root -g nogroup -m 0750 "${CONFIG_DIR}"
# Xray drops privileges to nobody:nogroup below. The parent directory is
# created 0700 by the Mihomo core installer, which blocks traversal for
# anyone but root regardless of this directory's own permissions.
chmod 0711 "$(dirname -- "${CONFIG_DIR}")"
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
        previous = json.load(h)
    inbound = next(item for item in previous["inbounds"] if item.get("tag") == "mihomo-reality")
    clients = inbound["settings"].get("clients", [])
except (OSError, ValueError, KeyError, IndexError, StopIteration):
    pass
config = {
  "log": {"loglevel": "warning"},
  # Stats API on loopback: lets the Mihomo Manager read per-profile traffic
  # (matched by the client "email" tag, mihomo-<profile_id>) without exposing
  # anything externally. Uses a different port than the direct VRX module's
  # own Xray instance (10085) since both processes can run on the same host.
  "api": {"tag": "api", "services": ["StatsService"]},
  "stats": {},
  "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
  "inbounds": [
    {
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
    },
    {
      "tag": "api", "listen": "127.0.0.1", "port": 10086, "protocol": "dokodemo-door",
      "settings": {"address": "127.0.0.1"}
    }
  ],
  "routing": {"rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]},
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
Group=nogroup
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
systemctl enable vps-control-mihomo-reality.service >/dev/null
systemctl restart vps-control-mihomo-reality.service

# A single immediate is-active check can succeed before a crashing process
# exits. Require the service to remain active for several checks.
for check in 1 2 3 4; do
  sleep 0.75
  if ! systemctl is-active --quiet vps-control-mihomo-reality.service; then
    echo "Mihomo/Reality не смог запуститься." >&2
    systemctl status vps-control-mihomo-reality.service --no-pager -l >&2 || true
    journalctl -u vps-control-mihomo-reality.service -n 60 --no-pager >&2 || true
    exit 1
  fi
done

main_pid="$(systemctl show -p MainPID --value vps-control-mihomo-reality.service)"
[[ "${main_pid}" =~ ^[1-9][0-9]*$ ]] || {
  echo "Mihomo/Reality: systemd не получил рабочий PID Xray." >&2
  journalctl -u vps-control-mihomo-reality.service -n 60 --no-pager >&2 || true
  exit 1
}

echo "Mihomo/Reality: TCP ${PORT}, target ${TARGET}, Xray PID ${main_pid}"
