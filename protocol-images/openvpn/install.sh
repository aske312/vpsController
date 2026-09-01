#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control/openvpn; PKI="$ROOT/pki"; PORT="${OPENVPN_PORT:-1194}"
apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openvpn easy-rsa iptables
install -d -m 0700 "$ROOT/easy-rsa"; cp -a /usr/share/easy-rsa/. "$ROOT/easy-rsa/"; chmod -R go-rwx "$ROOT/easy-rsa"
cd "$ROOT/easy-rsa"; export EASYRSA_BATCH=1 EASYRSA_PKI="$PKI" EASYRSA_REQ_CN='312.net OpenVPN CA'
[[ -s "$PKI/ca.crt" ]] || { ./easyrsa init-pki; ./easyrsa build-ca nopass; }
[[ -s "$PKI/issued/server.crt" ]] || ./easyrsa build-server-full server nopass
[[ -s "$PKI/dh.pem" ]] || ./easyrsa gen-dh
./easyrsa gen-crl; install -m 0644 "$PKI/crl.pem" "$ROOT/crl.pem"
[[ -s "$ROOT/tls-crypt.key" ]] || openvpn --genkey secret "$ROOT/tls-crypt.key"
printf '{"port":%s,"protocol":"udp","subnet":"10.74.0.0/24","dns":"1.1.1.1"}\n' "$PORT" >"$ROOT/settings.json"
cat >"$ROOT/server.conf" <<EOF
port $PORT
proto udp
dev tun
topology subnet
server 10.74.0.0 255.255.255.0
ca $PKI/ca.crt
cert $PKI/issued/server.crt
key $PKI/private/server.key
dh $PKI/dh.pem
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
user nobody
group nogroup
status /run/vps-control-openvpn/status 5
status-version 3
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 1.1.1.1"
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
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID CAP_DAC_OVERRIDE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID CAP_DAC_OVERRIDE
NoNewPrivileges=true
ProtectSystem=strict
ReadOnlyPaths=$ROOT
RuntimeDirectory=vps-control-openvpn
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable --now vps-control-openvpn.service; systemctl is-active --quiet vps-control-openvpn.service
