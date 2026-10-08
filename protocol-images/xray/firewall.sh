#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"; [[ "${action}" == add || "${action}" == delete ]] || exit 2
mapfile -t ports < <(python3 - <<'PY'
import json
try:
    settings = json.load(open('/etc/vps-control/xray/settings.json'))
    ports = {int(settings.get('port', 8445))}
    for profile in settings.get('profiles', {}).values():
        if isinstance(profile, dict): ports.add(int(profile['port']))
    print(*sorted(ports), sep='\n')
except Exception:
    print(8445)
PY
)
for port in "${ports[@]}"; do
  rule=(-p tcp --dport "${port}" -m comment --comment vps-control-xray -j ACCEPT)
  if [[ "${action}" == add ]]; then
    iptables -C INPUT "${rule[@]}" 2>/dev/null || iptables -I INPUT "${rule[@]}"
    command -v ip6tables >/dev/null && { ip6tables -C INPUT "${rule[@]}" 2>/dev/null || ip6tables -I INPUT "${rule[@]}"; }
  else
    iptables -D INPUT "${rule[@]}" 2>/dev/null || true
    command -v ip6tables >/dev/null && ip6tables -D INPUT "${rule[@]}" 2>/dev/null || true
  fi
done
