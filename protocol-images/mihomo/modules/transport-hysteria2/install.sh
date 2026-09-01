#!/usr/bin/env bash
set -Eeuo pipefail
MODULE="${QUIC_MODULE:-hysteria2}"
SERVICE="vps-control-mihomo-${MODULE}"
DEFAULT_PORT="${QUIC_DEFAULT_PORT:-8443}"
PORT="$(python3 - "${MIHOMO_SETTINGS_FILE:-}" "$DEFAULT_PORT" <<'PY'
import json,sys
try: d=json.load(open(sys.argv[1],encoding='utf-8'))
except Exception: d={}
print(d.get('port',int(sys.argv[2])))
PY
)"
source /etc/os-release
[[ "${ID:-}" == ubuntu || "${ID:-}" == debian ]] || { echo "Unsupported OS" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl python3 tar
arch="$(dpkg --print-architecture)"
[[ "$arch" == amd64 || "$arch" == arm64 ]] || { echo "Unsupported architecture" >&2; exit 1; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
release="$(curl -fsSL --retry 4 https://api.github.com/repos/SagerNet/sing-box/releases/latest)"
version="$(printf '%s' "$release" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
base="sing-box-${version}-linux-${arch}"
curl -fsSL --retry 4 "https://github.com/SagerNet/sing-box/releases/download/v${version}/${base}.tar.gz" -o "$tmp/sing-box.tgz"
curl -fsSL --retry 4 "https://github.com/SagerNet/sing-box/releases/download/v${version}/sing-box-${version}-checksums.txt" -o "$tmp/checksums"
(cd "$tmp"; grep " ${base}.tar.gz$" checksums | sha256sum -c -)
tar -xzf "$tmp/sing-box.tgz" -C "$tmp"
install -m 0755 "$tmp/$base/sing-box" /usr/local/bin/sing-box
[[ "${SINGBOX_UPDATE_ONLY:-0}" == 1 ]] && exit 0
root="/etc/vps-control/mihomo/quic"
install -d -m 0700 "$root/$MODULE/credentials"
if [[ ! -s "$root/server.crt" || ! -s "$root/server.key" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj '/CN=gate.312' -addext 'subjectAltName=DNS:gate.312' -keyout "$root/server.key" -out "$root/server.crt" >/dev/null 2>&1
  chmod 0600 "$root/server.key"
fi
if [[ ! -s "$root/$MODULE/config.json" ]]; then
  python3 - "$root/$MODULE/config.json" "$root" "$PORT" "$MODULE" <<'PY'
import json,sys
path,root,port,module=sys.argv[1:]
tls={"enabled":True,"server_name":"gate.312","certificate_path":f"{root}/server.crt","key_path":f"{root}/server.key"}
inbound={"type":module,"tag":f"{module}-in","listen":"::","listen_port":int(port),"users":[],"tls":tls}
if module == "tuic": inbound.update({"congestion_control":"bbr","auth_timeout":"3s","zero_rtt_handshake":False,"heartbeat":"10s"})
json.dump({"log":{"level":"warn"},"inbounds":[inbound],"outbounds":[{"type":"direct"}]},open(path,"w",encoding="utf-8"),indent=2)
PY
fi
/usr/local/bin/sing-box check -c "$root/$MODULE/config.json"
cat >"/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=GATE.312 Mihomo $MODULE
After=network-online.target
[Service]
ExecStart=/usr/local/bin/sing-box run -c $root/$MODULE/config.json
Restart=on-failure
User=root
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE.service"
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then ufw allow "$PORT/udp" comment "GATE.312 Mihomo $MODULE" >/dev/null || true; fi
