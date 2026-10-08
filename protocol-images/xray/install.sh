#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/xray
BIN=/usr/local/lib/vps-control-xray/xray
PORT="${XRAY_PORT:-8445}"
TARGET="${XRAY_REALITY_TARGET:-www.yahoo.com:443}"
SERVER_NAME="${XRAY_REALITY_SERVER_NAME:-www.yahoo.com}"
case "$(dpkg --print-architecture)" in
  amd64) machine=64 ;;
  arm64) machine=arm64-v8a ;;
  *) echo 'Unsupported architecture' >&2; exit 2 ;;
esac
apt-get -o DPkg::Lock::Timeout=300 update
DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends ca-certificates curl iptables openssl unzip
install -d -m 0700 "${ROOT}"
install -d -m 0755 "$(dirname "${BIN}")"
tmp="$(mktemp -d)"; trap 'rm -rf -- "${tmp}"' EXIT
curl -fsSL --retry 4 -H 'Accept: application/vnd.github+json' https://api.github.com/repos/XTLS/Xray-core/releases/latest -o "${tmp}/release.json"
version="$(python3 - "${tmp}/release.json" <<'PY'
import json, re, sys
value = str(json.load(open(sys.argv[1], encoding='utf-8')).get('tag_name', ''))
if not re.fullmatch(r'v[0-9][0-9A-Za-z.-]*', value): raise SystemExit('verified release tag missing')
print(value)
PY
)"
url="https://github.com/XTLS/Xray-core/releases/download/${version}/Xray-linux-${machine}.zip"
curl -fsSL --retry 4 "${url}" -o "${tmp}/xray.zip"
curl -fsSL --retry 4 "${url}.dgst" -o "${tmp}/xray.zip.dgst"
expected="$(awk -F '= ' '/256=/ {print $2; exit}' "${tmp}/xray.zip.dgst")"
[[ "${expected}" =~ ^[0-9a-fA-F]{64}$ ]] || { echo 'Verified SHA-256 is missing' >&2; exit 1; }
printf '%s  %s\n' "${expected}" "${tmp}/xray.zip" | sha256sum -c -
unzip -q "${tmp}/xray.zip" xray -d "${tmp}"
install -m 0755 "${tmp}/xray" "${BIN}.new"
if [[ -s "${ROOT}/config.json" ]]; then "${BIN}.new" run -test -config "${ROOT}/config.json"; fi
mv -f "${BIN}.new" "${BIN}"
if [[ ! -s "${ROOT}/settings.json" ]]; then
  key_output="$(${BIN} x25519)"
  private_key="$(awk -F ': ' '/PrivateKey:/ {print $2; exit}' <<<"${key_output}")"
  password="$(awk -F ': ' '/^(Password|PublicKey)/ {print $2; exit}' <<<"${key_output}")"
  [[ -n "${private_key}" && -n "${password}" ]] || { echo 'Unable to generate REALITY key pair' >&2; exit 1; }
  short_id="$(openssl rand -hex 8)"
  path="/$(openssl rand -hex 8)"
  python3 - "${ROOT}/settings.json" "${PORT}" "${TARGET}" "${SERVER_NAME}" "${private_key}" "${password}" "${short_id}" "${path}" <<'PY'
import json, os, sys
file, port, target, server_name, private_key, password, short_id, path = sys.argv[1:]
with open(file, 'w', encoding='utf-8') as output:
    json.dump({'port': int(port), 'target': target, 'server_name': server_name, 'private_key': private_key, 'password': password, 'short_id': short_id, 'path': path, 'managed_port_start': int(port) + 1, 'profiles': {}}, output, indent=2)
os.chmod(file, 0o600)
PY
fi
if [[ ! -s "${ROOT}/config.json" ]]; then
  python3 - "${ROOT}/config.json" "${ROOT}/settings.json" <<'PY'
import json, os, sys
output_path, settings_path = sys.argv[1:]
settings = json.load(open(settings_path, encoding='utf-8'))
config = {
    'log': {'loglevel': 'warning'},
    'inbounds': [{
        'listen': '0.0.0.0', 'port': settings['port'], 'protocol': 'vless', 'tag': 'vless-xhttp-reality',
        'settings': {'clients': [], 'decryption': 'none'},
        'streamSettings': {
            'network': 'xhttp', 'security': 'reality', 'xhttpSettings': {'path': settings['path'], 'mode': 'auto'},
            'realitySettings': {'show': False, 'target': settings['target'], 'xver': 0, 'serverNames': [settings['server_name']], 'privateKey': settings['private_key'], 'shortIds': [settings['short_id']]},
        },
    }],
    'outbounds': [{'protocol': 'freedom', 'tag': 'direct'}],
}
with open(output_path, 'w', encoding='utf-8') as output: json.dump(config, output, indent=2)
os.chmod(output_path, 0o600)
PY
fi
python3 - "${ROOT}/config.json" "${ROOT}/settings.json" <<'PY'
import json, os, sys
config_path, settings_path = sys.argv[1:]
config = json.load(open(config_path, encoding='utf-8'))
settings = json.load(open(settings_path, encoding='utf-8'))
settings.setdefault('managed_port_start', int(settings.get('port', 8445)) + 1)
settings.setdefault('profiles', {})
for inbound in config.get('inbounds', []):
    if inbound.get('protocol') != 'vless':
        continue
    stream = inbound.setdefault('streamSettings', {})
    if stream.get('network') == 'xhttp':
        xhttp = stream.setdefault('xhttpSettings', {})
        profile = next((value for value in settings['profiles'].values() if isinstance(value, dict) and value.get('tag') == inbound.get('tag')), None)
        xhttp['path'] = str(profile.get('path')) if profile else settings['path']
        xhttp.setdefault('mode', 'auto')
temporary = config_path + '.normalized'
with open(temporary, 'w', encoding='utf-8') as output:
    json.dump(config, output, indent=2)
os.chmod(temporary, 0o600)
os.replace(temporary, config_path)
temporary_settings = settings_path + '.normalized'
with open(temporary_settings, 'w', encoding='utf-8') as output:
    json.dump(settings, output, indent=2)
os.chmod(temporary_settings, 0o600)
os.replace(temporary_settings, settings_path)
PY
chmod 0600 "${ROOT}"/*.json
install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-xray/firewall.sh
cat >/etc/systemd/system/vps-control-xray.service <<EOF
[Unit]
Description=312.net Xray VLESS XHTTP REALITY server
After=network-online.target
Wants=network-online.target
[Service]
ExecStartPre=/usr/local/lib/vps-control-xray/firewall.sh add
ExecStart=${BIN} run -config ${ROOT}/config.json
ExecStopPost=/usr/local/lib/vps-control-xray/firewall.sh delete
Restart=on-failure
RestartSec=3s
IPAccounting=true
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=${ROOT}
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
"${BIN}" run -test -config "${ROOT}/config.json"
systemctl daemon-reload
systemctl enable vps-control-xray.service
systemctl restart vps-control-xray.service
systemctl is-active --quiet vps-control-xray.service
