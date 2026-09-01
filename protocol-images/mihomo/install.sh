#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="/opt/vps-control"
MODULE_DIR="${APP_ROOT}/protocol-images/mihomo"
DATA_DIR="/var/lib/vps-control/mihomo"
CONFIG_DIR="/etc/vps-control/mihomo"
SERVICE="/etc/systemd/system/vps-control-mihomo-manager.service"
CORE_DIR="${DATA_DIR}/bin"
CORE="${CORE_DIR}/mihomo"

[[ -x "${APP_ROOT}/venv/bin/uvicorn" ]] || {
  echo "Python runtime панели не найден: ${APP_ROOT}/venv/bin/uvicorn" >&2
  exit 1
}

install -d -m 0700 "${DATA_DIR}" "${DATA_DIR}/settings" "${CORE_DIR}"

tmp_dir="$(mktemp -d)"
candidate="${CORE_DIR}/.mihomo.$$.tmp"
trap 'rm -rf -- "${tmp_dir}"; rm -f -- "${candidate}"' EXIT
release_json="${tmp_dir}/release.json"
curl --fail --location --silent --show-error --retry 3 \
  https://api.github.com/repos/MetaCubeX/mihomo/releases/latest -o "${release_json}"
read -r core_version asset_url core_digest < <(python3 - "${release_json}" "$(uname -m)" <<'PY'
import json, re, sys
release = json.load(open(sys.argv[1], encoding="utf-8"))
version = str(release.get("tag_name", "")).lstrip("v")
architecture = sys.argv[2]
suffix = "amd64-compatible" if architecture == "x86_64" else "arm64" if architecture in {"aarch64", "arm64"} else ""
if not version or not suffix:
    raise SystemExit("Архитектура Mihomo не поддерживается")
name = f"mihomo-linux-{suffix}-v{version}.gz"
asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
if not asset:
    raise SystemExit(f"Asset {name} не найден")
digest = str(asset.get("digest", ""))
if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
    raise SystemExit(f"Официальный SHA-256 для {name} отсутствует")
print(version, asset["browser_download_url"], digest.removeprefix("sha256:"))
PY
)
archive="${tmp_dir}/mihomo.gz"
curl --fail --location --silent --show-error --retry 3 "${asset_url}" -o "${archive}"
printf '%s  %s\n' "${core_digest}" "${archive}" | sha256sum -c -
gzip -dc "${archive}" >"${candidate}"
chmod 0755 "${candidate}"
"${candidate}" -v | grep -F "Mihomo Meta v${core_version} " >/dev/null
mv -f -- "${candidate}" "${CORE}"

if [[ "${MIHOMO_UPDATE_ONLY:-0}" == "1" ]]; then
  echo "Mihomo core обновлён до v${core_version}."
  exit 0
fi
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
    "transport-hysteria2": False,
    "transport-tuic": False,
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

for _ in {1..40}; do
  if systemctl is-active --quiet vps-control-mihomo-manager.service \
    && ss -Hltn | grep -Eq '127\.0\.0\.1:8791[[:space:]]'; then
    break
  fi
  sleep 0.25
done
systemctl is-active --quiet vps-control-mihomo-manager.service
ss -Hltn | grep -Eq '127\.0\.0\.1:8791[[:space:]]'
"${CORE}" -v

echo "Mihomo Manager установлен. Транспортные модули независимы и устанавливаются отдельно в разделе Mihomo."
