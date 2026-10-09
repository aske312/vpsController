#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/hysteria2; STATE=/var/lib/vps-control/hysteria2; BIN=/usr/local/lib/vps-control-hysteria2/hysteria; CONFIG="${ROOT}/config.yaml"
PORT="${HYSTERIA2_PORT:-8443}"; AUTH_PORT="${HYSTERIA2_AUTH_PORT:-18081}"; STATS_PORT="${HYSTERIA2_STATS_PORT:-18082}"
PORTS_OVERRIDE="${HYSTERIA2_PORTS:-}"; OBFS_OVERRIDE="${HYSTERIA2_OBFS_MODE:-}"
GECKO_MIN="${HYSTERIA2_GECKO_MIN_PACKET_SIZE:-512}"; GECKO_MAX="${HYSTERIA2_GECKO_MAX_PACKET_SIZE:-1200}"
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
obfs_secret="$(openssl rand -hex 32)"
python3 - "${ROOT}/settings.json" "${CONFIG}" "${ROOT}" "${PORT}" "${AUTH_PORT}" "${STATS_PORT}" "${PORTS_OVERRIDE}" "${OBFS_OVERRIDE}" "${obfs_secret}" "${GECKO_MIN}" "${GECKO_MAX}" <<'PY'
import json, os, re, sys

settings_path, config_path, root, port, auth_port, stats_port, ports_override, obfs_override, generated_secret, gecko_min, gecko_max = sys.argv[1:]
existed = os.path.isfile(settings_path) and os.path.getsize(settings_path) > 0
settings = json.load(open(settings_path, encoding='utf-8')) if existed else {}
listen = ports_override or str(settings.get('listen', settings.get('port', port)))
if not re.fullmatch(r'[0-9]{1,5}(?:-[0-9]{1,5})?(?:,[0-9]{1,5}(?:-[0-9]{1,5})?)*', listen):
    raise SystemExit('HYSTERIA2_PORTS must contain ports or inclusive ranges separated by commas')
parts = [int(value) for token in listen.split(',') for value in token.split('-')]
if any(value < 1 or value > 65535 for value in parts):
    raise SystemExit('Hysteria2 ports must be between 1 and 65535')
if len(listen.split(',')) > 15 or any('-' in token and int(token.split('-')[0]) > int(token.split('-')[1]) for token in listen.split(',')):
    raise SystemExit('Hysteria2 accepts at most 15 ordered ports or ranges')
mode = obfs_override or str(settings.get('obfs', 'none' if existed else 'salamander'))
if mode not in {'none', 'salamander', 'gecko'}:
    raise SystemExit('HYSTERIA2_OBFS_MODE must be none, salamander or gecko')
minimum, maximum = int(gecko_min), int(gecko_max)
if not 1 <= minimum <= maximum <= 2048:
    raise SystemExit('Gecko packet sizes must satisfy 1 <= min <= max <= 2048')
settings.update({
    'port': int(listen.split(',', 1)[0].split('-', 1)[0]), 'listen': listen,
    'auth_port': int(auth_port), 'stats_port': int(stats_port), 'tls_mode': 'pinned',
    'domain': str(settings.get('domain', '')), 'obfs': mode,
    'obfs_password': str(settings.get('obfs_password') or generated_secret) if mode != 'none' else '',
    'gecko_min_packet_size': minimum, 'gecko_max_packet_size': maximum,
})
temporary = settings_path + '.new'
with open(temporary, 'w', encoding='utf-8') as output:
    json.dump(settings, output, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, settings_path)
lines = [
    f"listen: :{listen}", 'tls:', f"  cert: {root}/server.crt", f"  key: {root}/server.key", '  sniGuard: disable',
    'auth:', '  type: http', '  http:', f"    url: http://127.0.0.1:{auth_port}/auth",
    'trafficStats:', f"  listen: 127.0.0.1:{stats_port}", '  secret: vps-control-local',
]
if mode != 'none':
    lines.extend(['obfs:', f'  type: {mode}', f'  {mode}:', f"    password: {json.dumps(settings['obfs_password'])}"])
    if mode == 'gecko':
        lines.extend([f'    minPacketSize: {minimum}', f'    maxPacketSize: {maximum}'])
lines.extend(['masquerade:', '  type: string', '  string:', '    content: Not Found', '    statusCode: 404', ''])
temporary = config_path + '.new'
with open(temporary, 'w', encoding='utf-8') as output:
    output.write('\n'.join(lines))
os.chmod(temporary, 0o600)
os.replace(temporary, config_path)
PY
chmod 0600 "${ROOT}"/*; install -m 0755 "$(dirname "$0")/user-api.py" /usr/local/lib/vps-control-hysteria2/user-api.py; install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-hysteria2/firewall.sh
cat >/etc/systemd/system/vps-control-hysteria2-auth.service <<'EOF'
[Unit]
Description=312node.net Hysteria2 authentication
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
Description=312node.net Hysteria2 server
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
