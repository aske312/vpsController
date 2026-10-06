#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-xray.service 2>/dev/null || true
/usr/local/lib/vps-control-xray/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-xray.service
rm -rf /usr/local/lib/vps-control-xray
if [[ "${PRESERVE_COMPONENT_DATA:-1}" != 1 ]]; then
  rm -rf /etc/vps-control/xray
  python3 - <<'PY'
import json
from pathlib import Path
path = Path('/var/lib/vps-control/clients.json')
try: clients = json.loads(path.read_text(encoding='utf-8'))
except Exception: clients = []
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps([item for item in clients if item.get('protocol') != 'xray'], ensure_ascii=False, indent=2), encoding='utf-8')
PY
fi
systemctl daemon-reload
