#!/usr/bin/env bash
set -Eeuo pipefail
setting(){ python3 - "${MIHOMO_SETTINGS_FILE:-}" "$1" "$2" <<'PY'
import json,sys
try:
 with open(sys.argv[1],encoding="utf-8") as h:d=json.load(h)
except (OSError,ValueError):d={}
print(d.get(sys.argv[2],sys.argv[3]) if isinstance(d,dict) else sys.argv[3])
PY
}
MODULE_DIR=/usr/local/lib/vps-control-mihomo-reality
CONFIG_DIR=/etc/vps-control/mihomo/reality
PORT_START="$(setting port_start 9443)"; CDN_PORT_START="$(setting cdn_port_start 10443)"
XRAY_DNS="$(setting dns '1.1.1.1, 1.0.0.1')"; LOGLEVEL="$(setting loglevel warning)"
[[ "${PORT_START}" =~ ^[0-9]+$ && "${PORT_START}" -ge 1024 && "${PORT_START}" -le 65535 ]] || exit 1
[[ "${CDN_PORT_START}" =~ ^[0-9]+$ && "${CDN_PORT_START}" -ge 1024 && "${CDN_PORT_START}" -le 65535 && "${CDN_PORT_START}" != "${PORT_START}" ]] || exit 1
[[ "${LOGLEVEL}" =~ ^(debug|info|warning|error|none)$ ]] || exit 1
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl unzip iptables
install -d -m 0755 "${MODULE_DIR}"
release_tag="$(curl -fsSL --retry 4 -o /dev/null -w '%{url_effective}' https://github.com/XTLS/Xray-core/releases/latest)"; release_tag="${release_tag##*/}"; version="${release_tag#v}"
[[ "${release_tag}" =~ ^v[0-9] ]] || { echo 'Не удалось определить версию Xray' >&2; exit 1; }
current=""; [[ ! -x "${MODULE_DIR}/xray" ]] || current="$("${MODULE_DIR}/xray" version 2>/dev/null | head -n1 || true)"
if [[ "${current}" != *"${version}"* ]]; then
 case "$(dpkg --print-architecture)" in amd64) asset=Xray-linux-64.zip;; arm64) asset=Xray-linux-arm64-v8a.zip;; *) exit 1;; esac
 tmp="$(mktemp -d)"; trap 'rm -rf "${tmp}"' EXIT; url="https://github.com/XTLS/Xray-core/releases/download/${release_tag}/${asset}"
 curl -fL --retry 4 -o "${tmp}/${asset}" "${url}"; curl -fL --retry 4 -o "${tmp}/digest" "${url}.dgst"
 expected="$(grep -Eio '[0-9a-f]{64}' "${tmp}/digest"|head -n1|tr A-F a-f)"; actual="$(sha256sum "${tmp}/${asset}"|awk '{print $1}')"; [[ -n "${expected}" && "${expected}" == "${actual}" ]] || exit 1
 unzip -q -o "${tmp}/${asset}" xray -d "${tmp}"; install -m 0755 "${tmp}/xray" "${MODULE_DIR}/xray"; rm -rf "${tmp}"; trap - EXIT
fi
[[ "${XRAY_UPDATE_ONLY:-}" != 1 ]] || { echo "Mihomo/VLESS: Xray ${version} обновлён"; exit 0; }
install -d -o root -g nogroup -m 0750 "${CONFIG_DIR}"; chmod 0711 "$(dirname "${CONFIG_DIR}")"
if [[ ! -s "${CONFIG_DIR}/reality.env" ]]; then
 keys="$("${MODULE_DIR}/xray" x25519)"; private="$(sed -n 's/^PrivateKey:[[:space:]]*//p'<<<"${keys}"|head -n1)"; public="$(sed -n -E 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p'<<<"${keys}"|head -n1)"
 printf 'PRIVATE_KEY=%s\nPUBLIC_KEY=%s\nSHORT_ID=%s\n' "${private}" "${public}" "$(openssl rand -hex 8)" >"${CONFIG_DIR}/reality.env"
fi
chmod 0600 "${CONFIG_DIR}/reality.env"; candidate="${CONFIG_DIR}/config.candidate.json"
python3 - "${CONFIG_DIR}/config.json" "${candidate}" "${XRAY_DNS}" "${LOGLEVEL}" <<'PY'
import json,os,sys
try:
 with open(sys.argv[1],encoding="utf-8") as h:old=json.load(h)
except (OSError,ValueError):old={}
ins=[x for x in old.get('inbounds',[]) if str(x.get('tag','')).startswith('mihomo-vless-')]
ins.append({'tag':'api','listen':'127.0.0.1','port':10086,'protocol':'dokodemo-door','settings':{'address':'127.0.0.1'}})
private_networks=['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8','169.254.0.0/16','::1/128','fc00::/7','fe80::/10']
c={'log':{'loglevel':sys.argv[4]},'api':{'tag':'api','services':['StatsService']},'stats':{},'policy':{'levels':{'0':{'statsUserUplink':True,'statsUserDownlink':True}}},'inbounds':ins,'dns':{'servers':[x.strip() for x in sys.argv[3].split(',') if x.strip()],'queryStrategy':'UseIP'},'routing':{'domainStrategy':'IPIfNonMatch','rules':[{'type':'field','inboundTag':['api'],'outboundTag':'api'},{'type':'field','ip':private_networks,'outboundTag':'blocked'}]},'outbounds':[{'protocol':'freedom','tag':'direct'},{'protocol':'blackhole','tag':'blocked'}]}
with open(sys.argv[2],'w',encoding='utf-8') as h:json.dump(c,h,ensure_ascii=False,indent=2)
os.chmod(sys.argv[2],0o640)
PY
chown root:nogroup "${candidate}"; "${MODULE_DIR}/xray" run -test -config "${candidate}"; mv -f "${candidate}" "${CONFIG_DIR}/config.json"
cat >/usr/local/sbin/vps-control-mihomo-vless-firewall <<'SH'
#!/usr/bin/env bash
set -eu
python3 - /etc/vps-control/mihomo/reality/config.json <<'PY' | while read -r port; do
import json,sys
with open(sys.argv[1],encoding='utf-8') as h:c=json.load(h)
for x in c.get('inbounds',[]):
 if str(x.get('tag','')).startswith('mihomo-vless-') and x.get('listen')=='::':print(int(x['port']))
PY
 if [[ "${1:-add}" == add ]];then iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT 2>/dev/null||iptables -I INPUT 1 -p tcp --dport "${port}" -j ACCEPT;else while iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT 2>/dev/null;do iptables -D INPUT -p tcp --dport "${port}" -j ACCEPT;done;fi
done
SH
chmod 0755 /usr/local/sbin/vps-control-mihomo-vless-firewall
cat >/etc/systemd/system/vps-control-mihomo-reality.service <<EOF
[Unit]
Description=GATE.312 Mihomo VLESS component
After=network-online.target
[Service]
Type=simple
ExecStartPre=+/usr/local/sbin/vps-control-mihomo-vless-firewall add
ExecStart=${MODULE_DIR}/xray run -config ${CONFIG_DIR}/config.json
ExecStopPost=+/usr/local/sbin/vps-control-mihomo-vless-firewall remove
Restart=on-failure
DynamicUser=yes
SupplementaryGroups=nogroup
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable vps-control-mihomo-reality.service >/dev/null; systemctl restart vps-control-mihomo-reality.service
systemctl is-active --quiet vps-control-mihomo-reality.service
echo "Mihomo/VLESS: ядро готово; подключения создаются в профилях (порты ${PORT_START}/${CDN_PORT_START})"
