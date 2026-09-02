#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/openvpn; PKI="$ROOT/pki"; PORT="${OPENVPN_PORT:-1194}"
PROTO=udp; DNS=1.1.1.1
if [[ -s "$ROOT/settings.json" ]]; then read -r PORT PROTO DNS < <(python3 - "$ROOT/settings.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1])); print(int(s.get('port',1194)),s.get('protocol','udp'),s.get('dns','1.1.1.1'))
PY
); fi
apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openvpn easy-rsa iptables
install -d -m 0700 "$ROOT/easy-rsa"; install -d -m 0755 /usr/local/lib/vps-control-openvpn; cp -a /usr/share/easy-rsa/. "$ROOT/easy-rsa/"; chmod -R go-rwx "$ROOT/easy-rsa"
cd "$ROOT/easy-rsa"; export EASYRSA_BATCH=1 EASYRSA_PKI="$PKI" EASYRSA_REQ_CN='312.net OpenVPN CA'
[[ -s "$PKI/ca.crt" ]] || { ./easyrsa init-pki; ./easyrsa build-ca nopass; }
[[ -s "$PKI/issued/server.crt" ]] || ./easyrsa build-server-full server nopass
./easyrsa gen-crl; install -m 0644 "$PKI/crl.pem" "$ROOT/crl.pem"
[[ -s "$ROOT/tls-crypt.key" ]] || openvpn --genkey secret "$ROOT/tls-crypt.key"
printf '{"port":%s,"protocol":"%s","subnet":"10.74.0.0/24","dns":"%s"}\n' "$PORT" "$PROTO" "$DNS" >"$ROOT/settings.json"
cat >"$ROOT/server.conf" <<EOF
port $PORT
proto $([[ "$PROTO" == tcp ]] && echo tcp-server || echo udp)
dev tun
topology subnet
server 10.74.0.0 255.255.255.0
ca $PKI/ca.crt
cert $PKI/issued/server.crt
key $PKI/private/server.key
dh none
crl-verify $ROOT/crl.pem
tls-crypt $ROOT/tls-crypt.key
auth SHA256
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
tls-version-min 1.2
verify-client-cert require
remote-cert-tls client
keepalive 10 120
persist-key
persist-tun
status /run/vps-control-openvpn/status 5
status-version 3
tmp-dir /run/vps-control-openvpn
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS $DNS"
verb 3
EOF
chmod 0600 "$ROOT"/*.key "$ROOT/settings.json" "$ROOT/server.conf"
printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/90-vps-control-openvpn.conf; sysctl --system >/dev/null
install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-openvpn/firewall.sh
cat >/etc/systemd/system/vps-control-openvpn.service <<EOF
[Unit]
Description=312.net OpenVPN server
After=network-online.target
[Service]
ExecStartPre=/usr/local/lib/vps-control-openvpn/firewall.sh add
ExecStart=/usr/sbin/openvpn --config $ROOT/server.conf
ExecStopPost=/usr/local/lib/vps-control-openvpn/firewall.sh delete
Restart=on-failure
RestartSec=2
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID CAP_SETPCAP CAP_DAC_OVERRIDE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID CAP_SETPCAP CAP_DAC_OVERRIDE
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=$ROOT
RuntimeDirectory=vps-control-openvpn
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable vps-control-openvpn.service; systemctl restart vps-control-openvpn.service; sleep 2; systemctl is-active --quiet vps-control-openvpn.service; [[ "$(systemctl show vps-control-openvpn.service --property=NRestarts --value)" == 0 ]]
