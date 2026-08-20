#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/vps-control"
MODULE_DIR="${APP_ROOT}/protocol-images/mihomo"
DATA_DIR="/var/lib/vps-control/mihomo"
CONFIG_DIR="/etc/vps-control/mihomo"
SERVICE="/etc/systemd/system/vps-control-mihomo-manager.service"
CORE="${APP_ROOT}/api/bin/mihomo"

[[ -x "${CORE}" ]] || {
  echo "В release отсутствует api/bin/mihomo. Используйте scripts/build-release.sh из этого обновления." >&2
  exit 1
}
[[ -x "${APP_ROOT}/venv/bin/uvicorn" ]] || {
  echo "Python runtime панели не найден: ${APP_ROOT}/venv/bin/uvicorn" >&2
  exit 1
}

install -d -m 0700 "${DATA_DIR}" "${DATA_DIR}/settings"
# 0711 on the parent: traversal only, no listing. Reality's own installer
# locks its subdir down further (0750 root:nogroup) since Xray there runs
# as nobody:nogroup and must be able to reach it through this directory.
install -d -m 0711 "${CONFIG_DIR}"
install -d -m 0700 "${CONFIG_DIR}/shadowsocks" "${CONFIG_DIR}/reality"

python3 - "${DATA_DIR}/state.json" <<'PY'
import json, os, sys, tempfile
path = sys.argv[1]
modules = {
    "transport-wg": False,
    "transport-awg": False,
    "transport-shadowsocks": False,
    "transport-reality": False,
}
try:
    with open(path, encoding="utf-8") as handle:
        current = json.load(handle)
except (OSError, ValueError):
    current = {}
if not isinstance(current, dict):
    current = {}
stored = current.get("modules")
if not isinstance(stored, dict):
    stored = {}
for key, value in modules.items():
    stored.setdefault(key, value)
current["modules"] = stored
directory = os.path.dirname(path)
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state.", suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(current, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY

[[ -f "${DATA_DIR}/profiles.json" ]] || {
  printf '[]\n' >"${DATA_DIR}/profiles.json"
  chmod 0600 "${DATA_DIR}/profiles.json"
}

cat >"${SERVICE}" <<EOF
[Unit]
Description=GATE.312 Mihomo connection manager
After=network-online.target vps-control-api.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/vps-control.env
WorkingDirectory=${MODULE_DIR}
ExecStart=${APP_ROOT}/venv/bin/uvicorn manager:app --host 127.0.0.1 --port 8791
Restart=on-failure
RestartSec=3
User=root
PrivateTmp=true
ProtectHome=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vps-control-mihomo-manager.service

for _ in {1..20}; do
  systemctl is-active --quiet vps-control-mihomo-manager.service && break
  sleep 0.25
done
systemctl is-active --quiet vps-control-mihomo-manager.service
"${CORE}" -v

echo "Mihomo Manager установлен. Внутренние каналы: 0/4 — устанавливаются отдельно в разделе Mihomo."
