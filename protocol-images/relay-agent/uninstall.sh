#!/usr/bin/env bash
set -Eeuo pipefail
systemctl disable --now vps-control-relay-agent.service 2>/dev/null || true
if command -v ufw >/dev/null; then
  # Remove only rules tagged by this module, preserving rules created by the owner.
  python3 - <<'PY'
import re, subprocess
output = subprocess.run(['ufw', 'status', 'numbered'],capture_output=True,text=True,check=True).stdout
numbers = []
for line in output.splitlines():
    match = re.match(r'^\[\s*(\d+)\]', line)
    if match and re.search(r'# vps-control relay (API|TCP|UDP)\s*$', line): numbers.append(int(match[1]))
for number in sorted(numbers,reverse=True): subprocess.run(['ufw','--force','delete',str(number)],check=True)
PY
fi
rm -f /etc/systemd/system/vps-control-relay-agent.service
systemctl daemon-reload
rm -rf -- /usr/local/lib/vps-control-relay-agent
if [[ "${PRESERVE_COMPONENT_DATA:-0}" != 1 ]]; then
  rm -rf -- /etc/vps-control-relay-agent /var/lib/vps-control-relay-agent
fi
# Keep the system identity to avoid reassigning ownership of residual files.
echo 'Relay Agent остановлен и удалён.'
