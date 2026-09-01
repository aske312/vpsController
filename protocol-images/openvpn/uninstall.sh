#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-openvpn.service 2>/dev/null || true; /usr/local/lib/vps-control-openvpn/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-openvpn.service /etc/sysctl.d/90-vps-control-openvpn.conf; rm -rf /etc/vps-control/openvpn /usr/local/lib/vps-control-openvpn
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try:x=json.loads(p.read_text())
except Exception:x=[]
p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps([i for i in x if i.get('protocol')!='openvpn'],ensure_ascii=False,indent=2))
PY
systemctl daemon-reload; sysctl --system >/dev/null
