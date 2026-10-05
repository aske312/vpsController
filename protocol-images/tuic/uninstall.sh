#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-tuic.service 2>/dev/null || true
/usr/local/lib/vps-control-tuic/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-tuic.service; rm -rf /usr/local/lib/vps-control-tuic
if [[ "${PRESERVE_COMPONENT_DATA:-1}" != 1 ]]; then rm -rf /etc/vps-control/tuic; python3 - <<'PY'
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try: x=json.loads(p.read_text())
except Exception: x=[]
p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps([i for i in x if i.get('protocol')!='tuic'],ensure_ascii=False,indent=2))
PY
fi
systemctl daemon-reload
