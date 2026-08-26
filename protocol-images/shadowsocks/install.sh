#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/vps-control.env}"

setting() {
  local key="$1" fallback="$2" value=""
  value="$(sed -n "s/^${key}=//p" "${ENV_FILE}" 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')"
  printf '%s' "${value:-${fallback}}"
}

PORT_START="$(setting SHADOWSOCKS_PORT_START 30000)"
[[ "${PORT_START}" =~ ^[0-9]+$ && "${PORT_START}" -ge 1024 && "${PORT_START}" -le 60000 ]] \
  || { echo "SHADOWSOCKS_PORT_START должен быть портом от 1024 до 60000" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y shadowsocks-libev
ss_installed_version="$(dpkg-query -W -f='${Version}' shadowsocks-libev 2>/dev/null || true)"
ss_candidate_version="$(apt-cache policy shadowsocks-libev | awk '/Candidate:/ {print $2; exit}')"
[[ -n "${ss_installed_version}" && "${ss_installed_version}" == "${ss_candidate_version}" ]] \
  || { echo "Shadowsocks не обновлён до актуальной версии репозитория" >&2; exit 1; }
command -v ss-server >/dev/null 2>&1 || { echo "ss-server не установлен" >&2; exit 1; }

install -d -m 0700 /etc/vps-control/shadowsocks/clients
# Keep UDP datagrams below the conservative tunnel MTU. This avoids EMSGSIZE
# drops on mobile and tunneled client paths while preserving TCP + UDP mode.
python3 - "${PORT_START}" <<'PY'
import glob, json, os
import sys

port_start = int(sys.argv[1])
next_port = port_start
used_ports = set()
for path in sorted(glob.glob("/etc/vps-control/shadowsocks/clients/*.json")):
    try:
        with open(path, encoding="utf-8") as source:
            config = json.load(source)
        # Migrate profiles produced by older builds.  An invalid port makes
        # ss-server fail before it can listen; allocate a valid one instead.
        try:
            port = int(config.get("server_port", 0))
        except (TypeError, ValueError):
            port = 0
        if not 1024 <= port <= 65535 or port in used_ports:
            while next_port <= 65535 and next_port in used_ports:
                next_port += 1
            if next_port > 65535:
                raise ValueError("no free Shadowsocks port during migration")
            config["server_port"] = next_port
            port = next_port
            next_port += 1
        used_ports.add(port)
        if config.get("method") == "chacha20-ietf-poly13005":
            config["method"] = "chacha20-ietf-poly1305"
        config["timeout"] = 300
        config["mode"] = "tcp_and_udp"
        config["mtu"] = 1200
        config["no_delay"] = True
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as target:
            json.dump(config, target, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except (OSError, ValueError):
        continue
PY
cat >/etc/systemd/system/vps-control-shadowsocks.target <<'EOF'
[Unit]
Description=312.net managed Shadowsocks instances
Wants=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/vps-control-shadowsocks@.service <<'EOF'
[Unit]
Description=312.net Shadowsocks connection %i
PartOf=vps-control-shadowsocks.target
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ss-server -c /etc/vps-control/shadowsocks/clients/%i.json
IPAccounting=true
LimitNOFILE=65536
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true

[Install]
WantedBy=vps-control-shadowsocks.target
EOF

systemctl daemon-reload
systemctl enable --now vps-control-shadowsocks.target
systemctl is-active --quiet vps-control-shadowsocks.target
echo "Shadowsocks установлен. Подключения и порты создаются панелью индивидуально."
