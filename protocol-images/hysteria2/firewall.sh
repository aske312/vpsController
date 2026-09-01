#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"
[[ "${action}" == add || "${action}" == delete ]] || exit 2
port="$(python3 - <<'PY'
import json
try: print(int(json.load(open('/etc/vps-control/hysteria2/settings.json')).get('port',8443)))
except Exception: print(8443)
PY
)"
if [[ "${action}" == add ]]; then
  iptables -C INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || iptables -I INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT
  command -v ip6tables >/dev/null && { ip6tables -C INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || ip6tables -I INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT; }
else
  iptables -D INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || true
  command -v ip6tables >/dev/null && ip6tables -D INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || true
fi
