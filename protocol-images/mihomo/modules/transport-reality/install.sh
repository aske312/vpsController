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
TRANSPORT="$(setting transport xhttp)"
TRANSPORT_PATH="$(setting transport_path /vless)"
TRANSPORT_PATH_CONFIGURED="no"
if [[ -s "${MIHOMO_SETTINGS_FILE:-}" ]] && grep -q '"transport_path"' "${MIHOMO_SETTINGS_FILE}"; then
  TRANSPORT_PATH_CONFIGURED="yes"
fi
XHTTP_MODE="$(setting xhttp_mode auto)"
XPADDING="$(setting xpadding 100-1000)"
XMUX_CONCURRENCY="$(setting xmux_concurrency 12)"
XRAY_DNS="$(setting dns '1.1.1.1, 1.0.0.1')"
LOGLEVEL="$(setting loglevel warning)"
TARGET_HOST="${TARGET%:*}"

[[ "${PORT}" =~ ^[0-9]+$ && "${PORT}" -ge 1024 && "${PORT}" -le 65535 ]] || { echo "Некорректный порт" >&2; exit 1; }
[[ "${TARGET}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || { echo "Некорректный REALITY target" >&2; exit 1; }
[[ "${TRANSPORT}" == "xhttp" || "${TRANSPORT}" == "raw" || "${TRANSPORT}" == "grpc" ]] || { echo "Некорректный транспорт VLESS" >&2; exit 1; }
[[ "${TRANSPORT_PATH}" =~ ^/[A-Za-z0-9._~!$\&\'\(\)*+,\;=:@%/-]*$ ]] || { echo "Некорректный путь транспорта" >&2; exit 1; }
[[ "${XHTTP_MODE}" == "auto" || "${XHTTP_MODE}" == "stream-one" || "${XHTTP_MODE}" == "stream-up" || "${XHTTP_MODE}" == "packet-up" ]] || { echo "Некорректный режим XHTTP" >&2; exit 1; }
[[ "${XPADDING}" =~ ^[0-9]+(-[0-9]+)?$ ]] || { echo "Некорректный XHTTP padding" >&2; exit 1; }
[[ "${XMUX_CONCURRENCY}" =~ ^[0-9]+$ && "${XMUX_CONCURRENCY}" -ge 1 && "${XMUX_CONCURRENCY}" -le 64 ]] || { echo "Некорректный параллелизм XHTTP" >&2; exit 1; }
[[ "${LOGLEVEL}" == "debug" || "${LOGLEVEL}" == "info" || "${LOGLEVEL}" == "warning" || "${LOGLEVEL}" == "error" || "${LOGLEVEL}" == "none" ]] || { echo "Некорректный уровень журнала" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl unzip iptables

install -d -m 0755 "${MODULE_DIR}"

# Mihomo Reality owns its Xray runtime. Never copy the direct VRX runtime:
# the two modules must remain independently upgradeable and removable.
# Track upstream latest (like the direct VRX module already does) instead of
# a hardcoded version, so the "update available" check and this installer
# agree on what "latest" means, and re-running this (e.g. on a settings
# save) picks up new releases automatically.
latest_url="$(curl -fsSL --retry 4 -o /dev/null -w '%{url_effective}' https://github.com/XTLS/Xray-core/releases/latest)"
release_tag="${latest_url##*/}"
[[ "${release_tag}" =~ ^v[0-9][0-9A-Za-z._-]*$ ]] || { echo "Не удалось определить последнюю версию Xray" >&2; exit 1; }
XRAY_VERSION="${release_tag#v}"

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

  download_url="https://github.com/XTLS/Xray-core/releases/download/${release_tag}/${asset}"
  digest_url="${download_url}.dgst"

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

PREVIOUS_PORT="$(sed -n 's/^PORT=//p' "${CONFIG_DIR}/reality.env" | tail -n1 || true)"
sed -i '/^TARGET=/d;/^PORT=/d;/^TRANSPORT=/d;/^TRANSPORT_PATH=/d' "${CONFIG_DIR}/reality.env"
printf 'TARGET=%s\nPORT=%s\nTRANSPORT=%s\nTRANSPORT_PATH=%s\n' "${TARGET}" "${PORT}" "${TRANSPORT}" "${TRANSPORT_PATH}" >>"${CONFIG_DIR}/reality.env"
chmod 0600 "${CONFIG_DIR}/reality.env"
source "${CONFIG_DIR}/reality.env"

CANDIDATE="${CONFIG_DIR}/config.json.candidate"
rm -f -- "${CANDIDATE}"
[[ ! -s "${CONFIG_DIR}/config.json" ]] || cp -p -- "${CONFIG_DIR}/config.json" "${CANDIDATE}"
python3 - "${CANDIDATE}" "${PORT}" "${TARGET}" "${TARGET_HOST}" "${PRIVATE_KEY}" "${SHORT_ID}" "${XHTTP_PATH}" "${TRANSPORT}" "${TRANSPORT_PATH}" "${TRANSPORT_PATH_CONFIGURED}" "${XHTTP_MODE}" "${XPADDING}" "${XMUX_CONCURRENCY}" "${XRAY_DNS}" "${LOGLEVEL}" <<'PY'
import json, os, sys
output, port, target, host, private_key, short_id, legacy_path, transport, transport_path, transport_path_configured, xhttp_mode, xpadding, xmux_concurrency, dns_value, loglevel = sys.argv[1:]
clients = []
try:
    with open(output, encoding="utf-8") as h:
        previous = json.load(h)
    inbound = next(item for item in previous["inbounds"] if item.get("tag") == "mihomo-reality")
    clients = inbound["settings"].get("clients", [])
except (OSError, ValueError, KeyError, IndexError, StopIteration):
    pass
if transport_path_configured != "yes" and legacy_path:
    transport_path = legacy_path
stream = {"network": transport, "security": "reality"}
if transport == "xhttp":
    stream["xhttpSettings"] = {
      "path": transport_path, "mode": xhttp_mode,
      "extra": {"xPaddingBytes": xpadding, "xmux": {
        "maxConcurrency": str(xmux_concurrency),
        "hMaxRequestTimes": "600-900", "hMaxReusableSecs": "1800-3000"
      }}
    }
elif transport == "grpc":
    stream["grpcSettings"] = {"serviceName": transport_path.lstrip("/") or "vless", "multiMode": False}
else:
    stream["rawSettings"] = {"header": {"type": "none"}}
stream["realitySettings"] = {
  "show": False, "target": target, "xver": 0, "serverNames": [host],
  "privateKey": private_key, "shortIds": [short_id],
  "limitFallbackUpload": {"afterBytes": 1048576, "bytesPerSec": 262144, "burstBytesPerSec": 524288},
  "limitFallbackDownload": {"afterBytes": 1048576, "bytesPerSec": 262144, "burstBytesPerSec": 524288}
}
dns_servers = [value.strip() for value in dns_value.split(",") if value.strip()]
config = {
  "log": {"loglevel": loglevel},
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
      "streamSettings": stream,
      "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
    },
    {
      "tag": "api", "listen": "127.0.0.1", "port": 10086, "protocol": "dokodemo-door",
      "settings": {"address": "127.0.0.1"}
    }
  ],
  "dns": {"servers": dns_servers, "queryStrategy": "UseIP"},
  "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
    {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
    {"type": "field", "ip": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"], "outboundTag": "blocked"}
  ]},
  "outbounds": [{"protocol": "freedom", "tag": "direct"}, {"protocol": "blackhole", "tag": "blocked"}]
}
with open(output, "w", encoding="utf-8") as h:
    json.dump(config, h, ensure_ascii=False, indent=2)
os.chmod(output, 0o640)
PY
chown root:nogroup "${CANDIDATE}"

"${MODULE_DIR}/xray" run -test -config "${CANDIDATE}"
mv -f -- "${CANDIDATE}" "${CONFIG_DIR}/config.json"

cat >/etc/systemd/system/vps-control-mihomo-reality.service <<EOF
[Unit]
Description=GATE.312 Mihomo VLESS Reality
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStartPre=+/bin/sh -ec '/usr/sbin/iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null || /usr/sbin/iptables -I INPUT 1 -p tcp --dport ${PORT} -j ACCEPT'
ExecStart=${MODULE_DIR}/xray run -config ${CONFIG_DIR}/config.json
ExecStopPost=+/bin/sh -ec 'while /usr/sbin/iptables -C INPUT -p tcp --dport ${PORT} -j ACCEPT 2>/dev/null; do /usr/sbin/iptables -D INPUT -p tcp --dport ${PORT} -j ACCEPT; done'
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

if [[ -n "${PREVIOUS_PORT}" && "${PREVIOUS_PORT}" != "${PORT}" && "${PREVIOUS_PORT}" =~ ^[0-9]+$ ]]; then
  while /usr/sbin/iptables -C INPUT -p tcp --dport "${PREVIOUS_PORT}" -j ACCEPT 2>/dev/null; do
    /usr/sbin/iptables -D INPUT -p tcp --dport "${PREVIOUS_PORT}" -j ACCEPT
  done
  if command -v ufw >/dev/null; then
    ufw --force delete allow "${PREVIOUS_PORT}/tcp" >/dev/null 2>&1 || true
  fi
fi

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

echo "Mihomo/VLESS: TCP ${PORT}, ${TRANSPORT}, target ${TARGET}, Xray PID ${main_pid}"
