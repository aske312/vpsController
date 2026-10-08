#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"; [[ "${action}" == add || "${action}" == delete ]] || exit 2
ports="$(python3 - <<'PY'
import json
try: print(str(json.load(open('/etc/vps-control/hysteria2/settings.json')).get('listen',8443)).replace('-', ':'))
except Exception: print(8443)
PY
)"; rule=(-p udp -m multiport --dports "${ports}" -m comment --comment vps-control-hysteria2 -j ACCEPT)
if [[ "${action}" == add ]]; then
  iptables -C INPUT "${rule[@]}" 2>/dev/null || iptables -I INPUT "${rule[@]}"
  command -v ip6tables >/dev/null && { ip6tables -C INPUT "${rule[@]}" 2>/dev/null || ip6tables -I INPUT "${rule[@]}"; }
else
  iptables -D INPUT "${rule[@]}" 2>/dev/null || true
  command -v ip6tables >/dev/null && ip6tables -D INPUT "${rule[@]}" 2>/dev/null || true
fi
