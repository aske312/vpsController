#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-ikev2.service 2>/dev/null || true
/usr/local/lib/vps-control-ikev2/firewall.sh delete 2>/dev/null || true
rm -f /etc/systemd/system/vps-control-ikev2.service /etc/sysctl.d/90-vps-control-ikev2.conf
[[ "${PRESERVE_COMPONENT_DATA:-1}" == 1 ]] || rm -rf /etc/vps-control/ikev2
rm -rf /usr/local/lib/vps-control-ikev2
# /etc/swanctl is system-wide; remove only files owned by this module.
[[ "${PRESERVE_COMPONENT_DATA:-1}" == 1 ]] || rm -f /etc/swanctl/swanctl.conf /etc/swanctl/users.conf \
  /etc/swanctl/private/caKey.pem /etc/swanctl/private/serverKey.pem \
  /etc/swanctl/x509ca/caCert.pem /etc/swanctl/x509/serverCert.pem
rmdir /etc/swanctl/private /etc/swanctl/x509ca /etc/swanctl/x509 /etc/swanctl 2>/dev/null || true
python3 - <<'PY'
import os, sys
if os.getenv("PRESERVE_COMPONENT_DATA", "1") == "1":
    sys.exit(0)
import json
from pathlib import Path
p=Path('/var/lib/vps-control/clients.json')
try: rows=json.loads(p.read_text())
except Exception: rows=[]
p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps([x for x in rows if x.get('protocol')!='ikev2'],ensure_ascii=False,indent=2))
PY
systemctl daemon-reload; sysctl --system >/dev/null
