#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -r /etc/vps-control.env ]]; then set -a; source /etc/vps-control.env; set +a; fi
ROOT=/etc/vps-control/ikev2; SWAN=/etc/swanctl; POOL="${IKEV2_POOL:-10.75.0.0/24}"; DNS="${IKEV2_DNS:-1.1.1.1}"
if systemctl is-active --quiet strongswan-starter.service || systemctl is-active --quiet strongswan.service || systemctl is-active --quiet charon-systemd.service; then
  echo 'Another strongSwan runtime is active; refusing to replace it.' >&2; exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends charon-systemd strongswan-pki strongswan-swanctl libcharon-extra-plugins libstrongswan-extra-plugins libstrongswan-standard-plugins iptables
systemctl disable --now charon-systemd.service strongswan.service strongswan-starter.service 2>/dev/null || true
install -d -m 0700 "$ROOT" "$SWAN/private" "$SWAN/x509" "$SWAN/x509ca"; install -d -m 0755 /usr/local/lib/vps-control-ikev2
ENDPOINT="${PUBLIC_DOMAIN:-${PUBLIC_IPV4:-${PUBLIC_IPV6:-}}}"; [[ -n "$ENDPOINT" ]] || { echo 'Public endpoint is required' >&2; exit 1; }
if [[ ! -s "$SWAN/x509ca/caCert.pem" ]]; then
  pki --gen --type rsa --size 3072 --outform pem >"$SWAN/private/caKey.pem"
  pki --self --ca --lifetime 3650 --in "$SWAN/private/caKey.pem" --dn 'CN=312.net IKEv2 CA' --outform pem >"$SWAN/x509ca/caCert.pem"
fi
if [[ ! -s "$SWAN/x509/serverCert.pem" ]]; then
  pki --gen --type rsa --size 3072 --outform pem >"$SWAN/private/serverKey.pem"
  pki --pub --in "$SWAN/private/serverKey.pem" | pki --issue --lifetime 1825 --cacert "$SWAN/x509ca/caCert.pem" --cakey "$SWAN/private/caKey.pem" --dn "CN=$ENDPOINT" --san "$ENDPOINT" --flag serverAuth --flag ikeIntermediate --outform pem >"$SWAN/x509/serverCert.pem"
fi
[[ -s "$ROOT/users.json" ]] || printf '{}\n' >"$ROOT/users.json"
printf '{"pool":"%s","dns":"%s","endpoint":"%s"}\n' "$POOL" "$DNS" "$ENDPOINT" >"$ROOT/settings.json"
if [[ ! -s "$SWAN/users.conf" ]]; then cat >"$SWAN/users.conf" <<'EOF'
secrets {
}
EOF
fi
cat >"$SWAN/swanctl.conf" <<EOF
connections {
  ikev2-eap {
    version = 2
    local_addrs = %any
    proposals = aes256-sha256-modp2048,aes128-sha256-modp2048
    pools = vpn-pool
    local {
      auth = pubkey
      certs = serverCert.pem
      id = $ENDPOINT
    }
    remote {
      auth = eap-dynamic
      eap_id = %any
    }
    children {
      net {
        local_ts = 0.0.0.0/0
        esp_proposals = aes256-sha256,aes128-sha256
        start_action = none
      }
    }
  }
}
pools {
  vpn-pool {
    addrs = $POOL
    dns = $DNS
  }
}
include users.conf
EOF
chmod 0600 "$ROOT"/*.json "$SWAN"/*.conf "$SWAN/private"/*
printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/90-vps-control-ikev2.conf; sysctl --system >/dev/null
install -m 0755 "$(dirname "$0")/firewall.sh" /usr/local/lib/vps-control-ikev2/firewall.sh
cat >/etc/systemd/system/vps-control-ikev2.service <<EOF
[Unit]
Description=312.net IKEv2 server
After=network-online.target
[Service]
Type=notify
RuntimeDirectory=vps-control-ikev2
ExecStartPre=/usr/local/lib/vps-control-ikev2/firewall.sh add
ExecStart=/usr/sbin/charon-systemd
ExecStartPost=/usr/sbin/swanctl --load-all --file $SWAN/swanctl.conf --noprompt
ExecReload=/usr/sbin/swanctl --load-all --file $SWAN/swanctl.conf --noprompt
ExecStopPost=/usr/local/lib/vps-control-ikev2/firewall.sh delete
Restart=on-failure
RestartSec=2
IPAccounting=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable vps-control-ikev2.service; systemctl restart vps-control-ikev2.service; systemctl is-active --quiet vps-control-ikev2.service
