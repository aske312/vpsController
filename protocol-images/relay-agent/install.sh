#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control-relay-agent
STATE=/var/lib/vps-control-relay-agent
LIB=/usr/local/lib/vps-control-relay-agent
SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive
if ! command -v openssl >/dev/null || ! command -v python3 >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y openssl python3
fi
# Validate before changing services or firewall. Reinstall preserves identity and routes.
python3 - "${ENV_FILE:-/etc/vps-control.env}" <<'PY'
import ipaddress, json, os, shlex, socket, sys
from pathlib import Path
values = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ''
ip = values.get('PUBLIC_IP', '')
address = ipaddress.ip_address(ip)
if address.version != 4 or not address.is_global:
    raise SystemExit('Relay Agent requires a public IPv4 address')
for name in ('AWG_PORT', 'HYSTERIA2_PORT', 'TUIC_PORT', 'XRAY_PORT', 'HTTP_PORT'):
    port = int(values.get(name, '0') or '0')
    if port == 9443 or 20000 <= port <= 20999:
        raise SystemExit('Relay port range conflicts with an existing component: '+name)
for token in values.get('HYSTERIA2_PORTS', '').split(','):
    bounds = token.split('-')
    if all(item.isdigit() for item in bounds):
        low, high = int(bounds[0]), int(bounds[-1])
        if low <= 9443 <= high or low <= 20999 and high >= 20000:
            raise SystemExit('Relay port range conflicts with Hysteria2 listeners')
data_dir = Path(values.get('DATA_DIR', '/var/lib/vps-control'))
for folder in [data_dir / 'awg-ports', *list((data_dir / 'protocol-ports').glob('*'))]:
    for path in folder.glob('*.json'):
        try: port = int(json.loads(path.read_text()).get('port', 0))
        except (ValueError, TypeError): raise SystemExit('Invalid protocol port reservation')
        if port == 9443 or 20000 <= port <= 20999:
            raise SystemExit('Relay port range conflicts with an existing protocol alias')
if not Path('/etc/systemd/system/vps-control-relay-agent.service').exists():
    sock = socket.socket()
    try: sock.bind(('0.0.0.0', 9443))
    except OSError: raise SystemExit('Relay API port 9443 is already occupied')
    finally: sock.close()
PY
getent group vps-relay >/dev/null || groupadd --system vps-relay
id -u vps-relay >/dev/null 2>&1 || useradd --system --gid vps-relay --home-dir "${STATE}" --no-create-home --shell /usr/sbin/nologin vps-relay
install -d -o root -g vps-relay -m 0750 "${ROOT}"
install -d -o vps-relay -g vps-relay -m 0700 "${STATE}"
install -d -o root -g root -m 0755 "${LIB}"
python3 - "${ROOT}" "${ENV_FILE:-/etc/vps-control.env}" <<'PY'
import grp, hashlib, ipaddress, json, os, secrets, shlex, socket, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
values = {}
for line in Path(sys.argv[2]).read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ''
ip = str(ipaddress.ip_address(values['PUBLIC_IP']))
token_path = root / 'token'
if not token_path.exists():
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output: output.write(secrets.token_urlsafe(48))
os.chmod(token_path, 0o600)
token = token_path.read_text().strip()
gid = grp.getgrnam('vps-relay').gr_gid
if not (root / 'server.crt').exists():
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '825',
        '-subj', '/CN=vps-control-relay-agent', '-addext', f'subjectAltName=IP:{ip},IP:127.0.0.1',
        '-keyout', str(root / 'server.key'), '-out', str(root / 'server.crt')],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
own_ips = {ip}
addresses = subprocess.run(['hostname', '-I'],capture_output=True,text=True,check=True).stdout.split()
for address in addresses:
    try: own_ips.add(str(ipaddress.ip_address(address)))
    except ValueError: pass
config = root / 'config.json'
temporary = root / 'config.json.new'
temporary.write_text(json.dumps({'token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'own_ips': sorted(own_ips), 'public_ip': ip}))
os.chown(temporary, 0, gid); os.chmod(temporary, 0o640); os.replace(temporary, config)
for name in ('server.crt', 'server.key'):
    os.chown(root / name, 0, gid); os.chmod(root / name, 0o640)
PY
backup="$(mktemp -d)"
trap 'rm -rf -- "${backup}"' EXIT
[[ ! -f "${LIB}/agent.py" ]] || cp "${LIB}/agent.py" "${backup}/agent.py"
install -m 0755 "${SOURCE}/agent.py" "${LIB}/agent.py"
cat >/etc/systemd/system/vps-control-relay-agent.service <<'EOF'
[Unit]
Description=VPS Control Relay Agent
After=network-online.target
Wants=network-online.target
[Service]
User=vps-relay
Group=vps-relay
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-relay-agent/agent.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_UNIX
ReadWritePaths=/var/lib/vps-control-relay-agent
UMask=0077
LimitNOFILE=32768
MemoryMax=256M
TasksMax=32
IPAccounting=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable vps-control-relay-agent.service
systemctl restart vps-control-relay-agent.service
for attempt in {1..20}; do
  if curl --silent --fail --cacert "${ROOT}/server.crt" --max-time 2 \
    --config <(printf 'header = "Authorization: Bearer %s"\n' "$(cat "${ROOT}/token")") \
    https://127.0.0.1:9443/v1/status >/dev/null; then
    if command -v ufw >/dev/null; then
      ufw allow 9443/tcp comment 'vps-control relay API'
      ufw allow 20000:20999/tcp comment 'vps-control relay TCP'
      ufw allow 20000:20999/udp comment 'vps-control relay UDP'
    fi
    echo 'Relay Agent установлен; параметры подключения доступны в Light.'
    exit 0
  fi
  sleep 1
done
if [[ -f "${backup}/agent.py" ]]; then
  install -m 0755 "${backup}/agent.py" "${LIB}/agent.py"
  systemctl restart vps-control-relay-agent.service || true
else
  systemctl disable --now vps-control-relay-agent.service || true
fi
echo 'Relay Agent не подтвердил запуск; проверьте journalctl -u vps-control-relay-agent.' >&2
exit 1
