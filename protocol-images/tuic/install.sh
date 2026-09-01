#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/tuic; BIN=/usr/local/lib/vps-control-tuic/sing-box; PORT="${TUIC_PORT:-8444}"
case "$(dpkg --print-architecture)" in amd64) arch=amd64;; arm64) arch=arm64;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl iptables openssl tar
install -d -m 0700 "${ROOT}"; install -d -m 0755 "$(dirname "${BIN}")"
tmp="$(mktemp -d)"; trap 'rm -rf -- "${tmp}"' EXIT
curl -fsSL --retry 4 https://api.github.com/repos/SagerNet/sing-box/releases/latest -o "${tmp}/release.json"
read -r version asset_url asset_digest < <(python3 - "${tmp}/release.json" "${arch}" <<'PY'
import json,re,sys
r=json.load(open(sys.argv[1],encoding='utf-8')); version=str(r.get('tag_name','')).lstrip('v'); name=f'sing-box-{version}-linux-{sys.argv[2]}.tar.gz'
a=next((x for x in r.get('assets',[]) if x.get('name')==name),None); digest=str((a or {}).get('digest',''))
if not a or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest): raise SystemExit('verified sing-box asset not found')
print(version,a['browser_download_url'],digest.removeprefix('sha256:'))
PY
)
curl -fsSL --retry 4 "${asset_url}" -o "${tmp}/sing-box.tgz"
printf '%s  %s\n' "${asset_digest}" "${tmp}/sing-box.tgz" | sha256sum -c -
tar -xzf "${tmp}/sing-box.tgz" -C "${tmp}"
install -m 0755 "${tmp}/sing-box-${version}-linux-${arch}/sing-box" "${BIN}.new"
[[ -s "${ROOT}/config.json" ]] && "${BIN}.new" check -c "${ROOT}/config.json"
mv -f "${BIN}.new" "${BIN}"
if [[ ! -s "${ROOT}/server.crt" || ! -s "${ROOT}/server.key" ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 -subj '/CN=tuic.local' -addext 'subjectAltName=DNS:tuic.local' -keyout "${ROOT}/server.key" -out "${ROOT}/server.crt"
fi
chmod 0600 "${ROOT}/server.key" "${ROOT}/server.crt"
[[ -s "${ROOT}/settings.json" ]] || printf '{"port":%s,"congestion_control":"bbr"}\n' "${PORT}" >"${ROOT}/settings.json"
[[ -s "${ROOT}/config.json" ]] || python3 - "${ROOT}/config.json" "${ROOT}" "${PORT}" <<'PY'
import json,os,sys
path,root,port=sys.argv[1],sys.argv[2],int(sys.argv[3])
config={'log':{'level':'warn'},'inbounds':[{'type':'tuic','tag':'tuic-in','listen':'::','listen_port':port,'users':[],'congestion_control':'bbr','auth_timeout':'3s','zero_rtt_handshake':False,'heartbeat':'10s','tls':{'enabled':True,'server_name':'tuic.local','certificate_path':f'{root}/server.crt','key_path':f'{root}/server.key'}}],'outbounds':[{'type':'direct','tag':'direct'}]}
open(path,'w',encoding='utf-8').write(json.dumps(config,indent=2)); os.chmod(path,0o600)
PY
chmod 0600 "${ROOT}/settings.json" "${ROOT}/config.json"
install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-tuic/firewall.sh
cat >/etc/systemd/system/vps-control-tuic.service <<EOF
[Unit]
Description=312.net TUIC v5 server
After=network-online.target
Wants=network-online.target
[Service]
ExecStartPre=/usr/local/lib/vps-control-tuic/firewall.sh add
ExecStart=${BIN} run -c ${ROOT}/config.json
ExecStopPost=/usr/local/lib/vps-control-tuic/firewall.sh delete
Restart=on-failure
RestartSec=2
LimitNOFILE=1048576
IPAccounting=true
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=${ROOT}
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
"${BIN}" check -c "${ROOT}/config.json"
systemctl daemon-reload; systemctl enable --now vps-control-tuic.service; systemctl is-active --quiet vps-control-tuic.service
