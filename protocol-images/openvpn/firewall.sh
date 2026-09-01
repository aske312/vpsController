#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"; [[ "$action" == add || "$action" == delete ]] || exit 2
read -r port proto subnet < <(python3 - <<'PY'
import json
try: s=json.load(open('/etc/vps-control/openvpn/settings.json'))
except Exception: s={}
print(int(s.get('port',1194)),s.get('protocol','udp'),s.get('subnet','10.74.0.0/24'))
PY
)
uplink="$(ip -4 route show default | awk 'NR==1{print $5}')"; [[ -n "$uplink" ]] || exit 1
input=(-p "$proto" --dport "$port" -m comment --comment vps-control-openvpn -j ACCEPT)
forward=(-s "$subnet" -m comment --comment vps-control-openvpn -j ACCEPT); nat=(-s "$subnet" -o "$uplink" -m comment --comment vps-control-openvpn -j MASQUERADE)
if [[ "$action" == add ]]; then
  iptables -C INPUT "${input[@]}" 2>/dev/null || iptables -I INPUT "${input[@]}"; iptables -C FORWARD "${forward[@]}" 2>/dev/null || iptables -I FORWARD "${forward[@]}"; iptables -t nat -C POSTROUTING "${nat[@]}" 2>/dev/null || iptables -t nat -A POSTROUTING "${nat[@]}"
else iptables -D INPUT "${input[@]}" 2>/dev/null || true; iptables -D FORWARD "${forward[@]}" 2>/dev/null || true; iptables -t nat -D POSTROUTING "${nat[@]}" 2>/dev/null || true; fi
