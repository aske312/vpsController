#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"; [[ "$action" == add || "$action" == delete ]] || exit 2
pool="$(python3 -c "import json;print(json.load(open('/etc/vps-control/ikev2/settings.json')).get('pool','10.75.0.0/24'))")"
uplink="$(ip -4 route show default | awk 'NR==1{print $5}')"; [[ -n "$uplink" ]] || exit 1
rules=("INPUT|-p udp --dport 500 -m comment --comment vps-control-ikev2 -j ACCEPT" "INPUT|-p udp --dport 4500 -m comment --comment vps-control-ikev2 -j ACCEPT" "FORWARD|-s $pool -m policy --pol ipsec --dir in -m comment --comment vps-control-ikev2 -j ACCEPT" "FORWARD|-d $pool -m policy --pol ipsec --dir out -m comment --comment vps-control-ikev2 -j ACCEPT")
for entry in "${rules[@]}"; do chain="${entry%%|*}"; read -r -a args <<<"${entry#*|}"; if [[ "$action" == add ]]; then iptables -C "$chain" "${args[@]}" 2>/dev/null || iptables -I "$chain" "${args[@]}"; else iptables -D "$chain" "${args[@]}" 2>/dev/null || true; fi; done
nat=(-s "$pool" -o "$uplink" -m policy --pol none --dir out -m comment --comment vps-control-ikev2 -j MASQUERADE)
if [[ "$action" == add ]]; then iptables -t nat -C POSTROUTING "${nat[@]}" 2>/dev/null || iptables -t nat -A POSTROUTING "${nat[@]}"; else iptables -t nat -D POSTROUTING "${nat[@]}" 2>/dev/null || true; fi
