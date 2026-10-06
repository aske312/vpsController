#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-trojan.service 2>/dev/null || true
/usr/local/lib/vps-control-trojan/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-trojan.service; rm -rf /usr/local/lib/vps-control-trojan
if [[ "${PRESERVE_COMPONENT_DATA:-1}" != 1 ]]; then rm -rf /etc/vps-control/trojan; python3 - <<'PY'
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try: x=json.loads(p.read_text())
except Exception: x=[]
p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps([i for i in x if i.get('protocol')!='trojan'],ensure_ascii=False,indent=2))
PY
fi
systemctl daemon-reload
