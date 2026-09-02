#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/hysteria2
STATE=/var/lib/vps-control/hysteria2
BIN=/usr/local/lib/vps-control-hysteria2/hysteria
CONFIG="${ROOT}/config.yaml"
PORT="${HYSTERIA2_PORT:-8443}"
if [[ ! -s "${ROOT}/settings.json" ]]; then
  while ss -H -lun "sport = :${PORT}" | grep -q .; do PORT=$((PORT + 1)); [[ ${PORT} -le 65535 ]] || { echo 'No free UDP port for Hysteria2' >&2; exit 1; }; done
fi

case "$(dpkg --print-architecture)" in amd64) asset_arch=amd64;; arm64) asset_arch=arm64;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl iptables openssl
install -d -m 0755 "$(dirname "${BIN}")"
install -d -m 0700 "${ROOT}" "${STATE}"
release_json="$(mktemp)"; artifact="$(mktemp)"
trap 'rm -f -- "${release_json}" "${artifact}"' EXIT
curl -fsSL --retry 3 --retry-all-errors https://api.github.com/repos/apernet/hysteria/releases/latest -o "${release_json}"
read -r asset_url asset_digest < <(python3 - "${release_json}" "${asset_arch}" <<'PY'
import json,re,sys
r=json.load(open(sys.argv[1],encoding='utf-8')); suffix=f"linux-{sys.argv[2]}"
for a in r.get('assets',[]):
    if str(a.get('name','')).endswith(suffix):
        digest=str(a.get('digest',''))
        if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest): raise SystemExit('official SHA-256 is missing')
        print(a['browser_download_url'],digest.removeprefix('sha256:')); break
else: raise SystemExit('release asset not found')
PY
)
curl -fsSL --retry 3 --retry-all-errors "${asset_url}" -o "${artifact}"
printf '%s  %s\n' "${asset_digest}" "${artifact}" | sha256sum -c -
install -m 0755 "${artifact}" "${BIN}.new"; "${BIN}.new" version >/dev/null; mv -f "${BIN}.new" "${BIN}"

if [[ ! -s "${ROOT}/server.crt" || ! -s "${ROOT}/server.key" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 -subj '/CN=hysteria2.local' \
    -addext 'subjectAltName=DNS:hysteria2.local' -keyout "${ROOT}/server.key" -out "${ROOT}/server.crt"
fi
chmod 0600 "${ROOT}/server.key" "${ROOT}/server.crt"
[[ -s "${ROOT}/users.json" ]] || printf '{}\n' >"${ROOT}/users.json"
[[ -s "${ROOT}/settings.json" ]] || printf '{"port":%s,"tls_mode":"pinned","domain":"","obfs_enabled":false,"obfs_password":""}\n' "${PORT}" >"${ROOT}/settings.json"
chmod 0600 "${ROOT}/users.json" "${ROOT}/settings.json"
cat >"${CONFIG}" <<EOF
listen: :${PORT}
tls:
  cert: ${ROOT}/server.crt
  key: ${ROOT}/server.key
  sniGuard: disable
auth:
  type: http
  http:
    url: http://127.0.0.1:18081/auth
trafficStats:
  listen: 127.0.0.1:18082
  secret: vps-control-local
masquerade:
  type: string
  string:
    content: Not Found
    statusCode: 404
EOF
chmod 0600 "${CONFIG}"
install -m 0755 "$(dirname "$0")/user-api.py" /usr/local/lib/vps-control-hysteria2/user-api.py
install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-hysteria2/firewall.sh
cat >/etc/systemd/system/vps-control-hysteria2-auth.service <<'EOF'
[Unit]
Description=312.net Hysteria2 authentication
After=network.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-hysteria2/user-api.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=/etc/vps-control/hysteria2/users.json
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/vps-control-hysteria2.service <<EOF
[Unit]
Description=312.net Hysteria2 server
After=network-online.target vps-control-hysteria2-auth.service
Wants=network-online.target
Requires=vps-control-hysteria2-auth.service
[Service]
ExecStartPre=/usr/local/lib/vps-control-hysteria2/firewall.sh add
ExecStart=${BIN} server -c ${CONFIG}
ExecStopPost=/usr/local/lib/vps-control-hysteria2/firewall.sh delete
Restart=on-failure
RestartSec=2
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=${ROOT}
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable vps-control-hysteria2-auth.service vps-control-hysteria2.service
systemctl restart vps-control-hysteria2-auth.service vps-control-hysteria2.service
systemctl is-active --quiet vps-control-hysteria2.service
