#!/usr/bin/env bash
set -Eeuo pipefail
PORT="$(python3 - "${MIHOMO_SETTINGS_FILE:-}" <<'PY'
import json,sys
try:
    config=json.load(open('/etc/vps-control/mihomo/quic/hysteria2/config.json',encoding='utf-8'))
    port=next(item['listen_port'] for item in config.get('inbounds',[]) if item.get('type')=='hysteria2')
except (OSError,ValueError,KeyError,StopIteration):
    try: port=json.load(open(sys.argv[1],encoding='utf-8')).get('port',18443)
    except (OSError,ValueError): port=18443
print(int(port))
PY
)"
systemctl disable --now vps-control-mihomo-hysteria2.service 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then ufw delete allow "$PORT/udp" >/dev/null 2>&1 || true; fi
rm -f /etc/systemd/system/vps-control-mihomo-hysteria2.service
[[ "${PRESERVE_COMPONENT_DATA:-1}" == 1 ]] || rm -rf /etc/vps-control/mihomo/quic/hysteria2
systemctl daemon-reload
