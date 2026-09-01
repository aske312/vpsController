#!/usr/bin/env bash
set -Eeuo pipefail
port="$(python3 - <<'PY'
import json
try: print(int(json.load(open('/etc/vps-control/hysteria2/settings.json')).get('port',8443)))
except Exception: print(8443)
PY
)"
systemctl disable --now vps-control-hysteria2.service vps-control-hysteria2-auth.service 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-hysteria2.service /etc/systemd/system/vps-control-hysteria2-auth.service
iptables -D INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || true
command -v ip6tables >/dev/null && ip6tables -D INPUT -p udp --dport "${port}" -m comment --comment vps-control-hysteria2 -j ACCEPT 2>/dev/null || true
rm -rf /etc/vps-control/hysteria2 /var/lib/vps-control/hysteria2 /usr/local/lib/vps-control-hysteria2
python3 - <<'PY'
import json
from pathlib import Path
path=Path('/var/lib/vps-control/clients.json')
try: items=json.loads(path.read_text(encoding='utf-8'))
except (OSError,json.JSONDecodeError): items=[]
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps([item for item in items if item.get('protocol')!='hysteria2'],ensure_ascii=False,indent=2),encoding='utf-8')
PY
systemctl daemon-reload
