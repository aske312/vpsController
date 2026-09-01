#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-tuic.service 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-tuic.service
/usr/local/lib/vps-control-tuic/firewall.sh delete 2>/dev/null || true
rm -rf /etc/vps-control/tuic /usr/local/lib/vps-control-tuic
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try: items=json.loads(p.read_text(encoding='utf-8'))
except (OSError,json.JSONDecodeError): items=[]
p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps([x for x in items if x.get('protocol')!='tuic'],ensure_ascii=False,indent=2),encoding='utf-8')
PY
systemctl daemon-reload
