#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/hysteria2; STATE=/var/lib/vps-control/hysteria2; BIN=/usr/local/lib/vps-control-hysteria2/hysteria; CONFIG="${ROOT}/config.yaml"
PORT="${HYSTERIA2_PORT:-8443}"; AUTH_PORT="${HYSTERIA2_AUTH_PORT:-18081}"; STATS_PORT="${HYSTERIA2_STATS_PORT:-18082}"
case "$(dpkg --print-architecture)" in amd64) arch=amd64;; arm64) arch=arm64;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
apt-get -o DPkg::Lock::Timeout=300 update
DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends ca-certificates curl iptables openssl
install -d -m 0755 "$(dirname "${BIN}")"; install -d -m 0700 "${ROOT}" "${STATE}"
release="$(mktemp)"; artifact="$(mktemp)"; trap 'rm -f -- "${release}" "${artifact}"' EXIT
curl -fsSL --retry 3 --retry-all-errors https://api.github.com/repos/apernet/hysteria/releases/latest -o "${release}"
read -r url digest < <(python3 - "${release}" "${arch}" <<'PY'
import json,re,sys
r=json.load(open(sys.argv[1])); suffix=f'linux-{sys.argv[2]}'
for a in r.get('assets',[]):
    if str(a.get('name','')).endswith(suffix):
        d=str(a.get('digest',''))
        if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',d): raise SystemExit('official SHA-256 is missing')
        print(a['browser_download_url'],d.removeprefix('sha256:')); break
else: raise SystemExit('release asset not found')
PY
)
curl -fsSL --retry 3 --retry-all-errors "${url}" -o "${artifact}"; printf '%s  %s\n' "${digest}" "${artifact}" | sha256sum -c -
install -m 0755 "${artifact}" "${BIN}.new"; "${BIN}.new" version >/dev/null; mv -f "${BIN}.new" "${BIN}"
if [[ ! -s "${ROOT}/server.crt" ]]; then openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 -subj '/CN=endpoint.internal' -addext 'subjectAltName=DNS:endpoint.internal' -keyout "${ROOT}/server.key" -out "${ROOT}/server.crt"; fi
chmod 0600 "${ROOT}/server.key" "${ROOT}/server.crt"; [[ -s "${ROOT}/users.json" ]] || printf '{}\n' >"${ROOT}/users.json"
[[ -s "${ROOT}/settings.json" ]] || printf '{"port":%s,"auth_port":%s,"stats_port":%s,"tls_mode":"pinned","domain":""}\n' "${PORT}" "${AUTH_PORT}" "${STATS_PORT}" >"${ROOT}/settings.json"
[[ -s "${CONFIG}" ]] || cat >"${CONFIG}" <<EOF
listen: :${PORT}
tls:
  cert: ${ROOT}/server.crt
  key: ${ROOT}/server.key
  sniGuard: disable
auth:
  type: http
  http:
    url: http://127.0.0.1:${AUTH_PORT}/auth
trafficStats:
  listen: 127.0.0.1:${STATS_PORT}
  secret: vps-control-local
masquerade:
  type: string
  string:
    content: Not Found
    statusCode: 404
EOF
chmod 0600 "${ROOT}"/*; install -m 0755 "$(dirname "$0")/user-api.py" /usr/local/lib/vps-control-hysteria2/user-api.py; install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-hysteria2/firewall.sh
cat >/etc/systemd/system/vps-control-hysteria2-auth.service <<'EOF'
[Unit]
Description=312.net Hysteria2 authentication
After=network.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-hysteria2/user-api.py
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=/etc/vps-control/hysteria2/users.json /etc/vps-control/hysteria2/settings.json
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/vps-control-hysteria2.service <<EOF
[Unit]
Description=312.net Hysteria2 server
After=network-online.target vps-control-hysteria2-auth.service
Requires=vps-control-hysteria2-auth.service
[Service]
ExecStartPre=/usr/local/lib/vps-control-hysteria2/firewall.sh add
ExecStart=${BIN} server -c ${CONFIG}
ExecStopPost=/usr/local/lib/vps-control-hysteria2/firewall.sh delete
Restart=on-failure
IPAccounting=true
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=${ROOT}
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable vps-control-hysteria2-auth.service vps-control-hysteria2.service; systemctl restart vps-control-hysteria2-auth.service vps-control-hysteria2.service; systemctl is-active --quiet vps-control-hysteria2.service
