#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-ikev2.service 2>/dev/null || true
/usr/local/lib/vps-control-ikev2/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-ikev2.service /etc/sysctl.d/90-vps-control-ikev2.conf
rm -rf /etc/vps-control/ikev2 /usr/local/lib/vps-control-ikev2
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try: rows=json.loads(p.read_text())
except Exception: rows=[]
p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps([x for x in rows if x.get('protocol')!='ikev2'],ensure_ascii=False,indent=2))
PY
systemctl daemon-reload; sysctl --system >/dev/null
