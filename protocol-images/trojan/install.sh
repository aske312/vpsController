#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/trojan; BIN=/usr/local/lib/vps-control-trojan/sing-box; PORT="${TROJAN_PORT:-8445}"
case "$(dpkg --print-architecture)" in amd64) arch=amd64;; arm64) arch=arm64;; *) exit 2;; esac
apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl iptables openssl tar
install -d -m 0700 "$ROOT"; install -d -m 0755 "$(dirname "$BIN")"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 4 https://api.github.com/repos/SagerNet/sing-box/releases/latest -o "$tmp/release.json"
read -r version url digest < <(python3 - "$tmp/release.json" "$arch" <<'PY'
import json,re,sys
r=json.load(open(sys.argv[1])); v=str(r.get('tag_name','')).lstrip('v'); n=f'sing-box-{v}-linux-{sys.argv[2]}.tar.gz'; a=next((x for x in r.get('assets',[]) if x.get('name')==n),None); d=str((a or {}).get('digest',''))
if not a or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}',d): raise SystemExit('verified asset missing')
print(v,a['browser_download_url'],d.removeprefix('sha256:'))
PY
)
curl -fsSL --retry 4 "$url" -o "$tmp/a.tgz"; printf '%s  %s\n' "$digest" "$tmp/a.tgz" | sha256sum -c -; tar -xzf "$tmp/a.tgz" -C "$tmp"
install -m 0755 "$tmp/sing-box-$version-linux-$arch/sing-box" "$BIN.new"; [[ -s "$ROOT/config.json" ]] && "$BIN.new" check -c "$ROOT/config.json"; mv -f "$BIN.new" "$BIN"
if [[ ! -s "$ROOT/server.crt" ]]; then openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 -subj '/CN=trojan.local' -addext 'subjectAltName=DNS:trojan.local' -keyout "$ROOT/server.key" -out "$ROOT/server.crt"; fi; chmod 0600 "$ROOT"/server.*
[[ -s "$ROOT/settings.json" ]] || printf '{"port":%s}\n' "$PORT" >"$ROOT/settings.json"
[[ -s "$ROOT/config.json" ]] || python3 - "$ROOT/config.json" "$ROOT" "$PORT" <<'PY'
import json,os,sys
p,r,port=sys.argv[1],sys.argv[2],int(sys.argv[3]); c={'log':{'level':'warn'},'inbounds':[{'type':'trojan','tag':'trojan-in','listen':'::','listen_port':port,'users':[],'tls':{'enabled':True,'server_name':'trojan.local','certificate_path':f'{r}/server.crt','key_path':f'{r}/server.key'}}],'outbounds':[{'type':'direct','tag':'direct'}]}; open(p,'w').write(json.dumps(c,indent=2)); os.chmod(p,0o600)
PY
chmod 0600 "$ROOT"/*.json; install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-trojan/firewall.sh
cat >/etc/systemd/system/vps-control-trojan.service <<EOF
[Unit]
Description=312.net Trojan server
After=network-online.target
[Service]
ExecStartPre=/usr/local/lib/vps-control-trojan/firewall.sh add
ExecStart=$BIN run -c $ROOT/config.json
ExecStopPost=/usr/local/lib/vps-control-trojan/firewall.sh delete
Restart=on-failure
IPAccounting=true
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=$ROOT
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
"$BIN" check -c "$ROOT/config.json"; systemctl daemon-reload; systemctl enable --now vps-control-trojan.service; systemctl is-active --quiet vps-control-trojan.service
