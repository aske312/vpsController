#!/usr/bin/env bash
set -Eeuo pipefail
PORT="$(python3 - "${MIHOMO_SETTINGS_FILE:-}" <<'PY'
import json,sys
try: print(int(json.load(open(sys.argv[1],encoding='utf-8')).get('port',10443)))
except Exception: print(10443)
PY
)"
systemctl disable --now vps-control-mihomo-tuic.service 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then ufw delete allow "$PORT/udp" >/dev/null 2>&1 || true; fi
rm -f /etc/systemd/system/vps-control-mihomo-tuic.service
rm -rf /etc/vps-control/mihomo/quic/tuic
systemctl daemon-reload
