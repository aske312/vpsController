#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=/etc/vps-control-relay-agent
STATE=/var/lib/vps-control-relay-agent
LIB=/usr/local/lib/vps-control-relay-agent
SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive
[[ ${EUID} -eq 0 ]] || { echo 'Relay Agent необходимо устанавливать от root.' >&2; exit 1; }
if [[ "${RELAY_UPDATE_LOCKED:-0}" != 1 ]]; then
  mkdir -p /run/lock
  exec 9>/run/lock/vps-control-relay-update.lock
  flock -n 9 || { echo 'Relay Agent уже обновляется.' >&2; exit 1; }
fi
if ! command -v openssl >/dev/null || ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl openssl python3
fi
# Validate before changing services or firewall. Reinstall preserves identity and routes.
API_PORT="$(python3 - "${ROOT}/config.json" "${ENV_FILE:-/etc/vps-control.env}" "${SOURCE}" <<'PY_PORT'
import json, os, shlex, socket, subprocess, sys
from pathlib import Path
path, env_path = map(Path, sys.argv[1:3])
sys.path.insert(0, sys.argv[3])
from addresses import dual_stack_available, domain
config = json.loads(path.read_text()) if path.exists() else {}
domains = json.loads(os.environ.get('RELAY_DOMAINS_JSON', '[]')) or config.get('domains', [])
cdn_domains = json.loads(os.environ.get('RELAY_CDN_DOMAINS_JSON', '[]')) or config.get('cdn_domains', [])
if not isinstance(cdn_domains, list) or len(cdn_domains) > 64 or any(not isinstance(value, str) or not domain(value) for value in cdn_domains):
    raise SystemExit('Invalid Relay CDN domain list')
if not isinstance(domains, list) or len(domains) > 64 or any(not isinstance(value, str) or not domain(value) for value in domains):
    raise SystemExit('Invalid Relay domain list')
values = {}
for line in (env_path.read_text().splitlines() if env_path.exists() else []):
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ''
reserved = {int(values.get(name, '0') or '0') for name in ('AWG_PORT', 'HYSTERIA2_PORT', 'TUIC_PORT', 'XRAY_PORT', 'HTTP_PORT')}
ranges = []
for token in values.get('HYSTERIA2_PORTS', '').split(','):
    bounds = token.split('-')
    if all(item.isdigit() for item in bounds): ranges.append((int(bounds[0]), int(bounds[-1])))
data_dir = Path(values.get('DATA_DIR', '/var/lib/vps-control'))
for folder in [data_dir / 'awg-ports', *list((data_dir / 'protocol-ports').glob('*'))]:
    for entry in folder.glob('*.json'):
        port = json.loads(entry.read_text()).get('port', 0)
        if type(port) is not int: raise SystemExit('Invalid protocol port reservation')
        reserved.add(port)
requested = os.environ.get('RELAY_API_PORT')
previous = config.get('api_port', 9443) if path.exists() else None
managed_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'vps-control-relay-agent.service']).returncode == 0
if managed_active and not path.exists(): raise SystemExit('Active Relay Agent has no saved identity')
candidates = [requested] if requested else [previous] if previous is not None else range(9443, 9500)
for port in candidates:
    if isinstance(port, str) and port.isascii() and port.isdigit(): port = int(port)
    if type(port) is not int or not 1024 <= port <= 65535 or 20000 <= port <= 20999:
        raise SystemExit('Relay API port must be 1024-65535 outside 20000-20999')
    if port in reserved or any(low <= port <= high for low, high in ranges):
        if requested or previous is not None: raise SystemExit(f'Relay API port {port} conflicts with a configured component')
        continue
    if port == previous and managed_active:
        print(port)
        break
    ipv6 = dual_stack_available()
    with socket.socket(socket.AF_INET6 if ipv6 else socket.AF_INET) as probe:
        if ipv6: probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try: probe.bind(('::' if ipv6 else '0.0.0.0', port))
        except OSError:
            if requested or previous is not None: raise SystemExit(f'Relay API port {port} is already occupied; saved identity was not changed')
            continue
    print(port)
    break
else:
    raise SystemExit('No free Relay API port in 9443-9499; use --api-port PORT')
PY_PORT
)"
export RELAY_API_PORT="${API_PORT}"

python3 - "${ENV_FILE:-/etc/vps-control.env}" <<'PY'
import ipaddress, json, os, shlex, socket, subprocess, sys
from pathlib import Path
values = {}
env_path = Path(sys.argv[1])
for line in (env_path.read_text().splitlines() if env_path.exists() else []):
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ''
ip = os.environ.get('RELAY_PUBLIC_IP') or values.get('PUBLIC_IP', '')
if not ip and Path('/etc/vps-control-relay-agent/config.json').exists():
    ip = json.loads(Path('/etc/vps-control-relay-agent/config.json').read_text())['public_ip']
api_port = int(os.environ["RELAY_API_PORT"])
address = ipaddress.ip_address(ip)
if address.version != 4 or not address.is_global:
    raise SystemExit('Relay Agent requires a public IPv4 address')
config_path = Path('/etc/vps-control-relay-agent/config.json')
if config_path.exists() and json.loads(config_path.read_text())['public_ip'] != str(address):
    raise SystemExit('Relay Agent public IP differs from its existing identity; refusing to reuse the certificate')
for name in ('AWG_PORT', 'HYSTERIA2_PORT', 'TUIC_PORT', 'XRAY_PORT', 'HTTP_PORT'):
    port = int(values.get(name, '0') or '0')
    if port == api_port or 20000 <= port <= 20999:
        raise SystemExit('Relay port range conflicts with an existing component: '+name)
for token in values.get('HYSTERIA2_PORTS', '').split(','):
    bounds = token.split('-')
    if all(item.isdigit() for item in bounds):
        low, high = int(bounds[0]), int(bounds[-1])
        if low <= api_port <= high or low <= 20999 and high >= 20000:
            raise SystemExit('Relay port range conflicts with Hysteria2 listeners')
data_dir = Path(values.get('DATA_DIR', '/var/lib/vps-control'))
for folder in [data_dir / 'awg-ports', *list((data_dir / 'protocol-ports').glob('*'))]:
    for path in folder.glob('*.json'):
        try: port = int(json.loads(path.read_text()).get('port', 0))
        except (ValueError, TypeError): raise SystemExit('Invalid protocol port reservation')
        if port == api_port or 20000 <= port <= 20999:
            raise SystemExit('Relay port range conflicts with an existing protocol alias')
previous = json.loads(config_path.read_text()).get('api_port', 9443) if config_path.exists() else None
managed_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'vps-control-relay-agent.service']).returncode == 0
if previous != api_port or not managed_active:
    sock = socket.socket()
    try: sock.bind(('0.0.0.0', api_port))
    except OSError: raise SystemExit(f'Relay API port {api_port} is already occupied')
    finally: sock.close()
PY
getent group vps-relay >/dev/null || groupadd --system vps-relay
id -u vps-relay >/dev/null 2>&1 || useradd --system --gid vps-relay --home-dir "${STATE}" --no-create-home --shell /usr/sbin/nologin vps-relay
install -d -o root -g vps-relay -m 0750 "${ROOT}"
install -d -o vps-relay -g vps-relay -m 0700 "${STATE}"
install -d -o root -g root -m 0755 "${LIB}"
python3 - "${ROOT}" "${ENV_FILE:-/etc/vps-control.env}" "${SOURCE}" <<'PY'
import grp, hashlib, ipaddress, json, os, secrets, shlex, socket, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, sys.argv[3])
from addresses import domain
values = {}
env_path = Path(sys.argv[2])
for line in (env_path.read_text().splitlines() if env_path.exists() else []):
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ''
existing = json.loads((root / 'config.json').read_text()) if (root / 'config.json').exists() else {}
ip = str(ipaddress.ip_address(os.environ.get('RELAY_PUBLIC_IP') or values.get('PUBLIC_IP') or existing.get('public_ip')))
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
previous = json.loads(config.read_text()) if config.exists() else {}
domains = json.loads(os.environ.get('RELAY_DOMAINS_JSON', '[]')) or previous.get('domains', [])
cdn_domains = json.loads(os.environ.get('RELAY_CDN_DOMAINS_JSON', '[]')) or previous.get('cdn_domains', [])
if not isinstance(cdn_domains, list) or len(cdn_domains) > 64 or any(not isinstance(value, str) or not domain(value) for value in cdn_domains):
    raise SystemExit('Invalid Relay CDN domain list')
if not isinstance(domains, list) or len(domains) > 64 or any(not isinstance(value, str) or not domain(value) for value in domains):
    raise SystemExit('Invalid Relay domain list')
temporary = root / 'config.json.new'
temporary.write_text(json.dumps({'token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'own_ips': sorted(own_ips), 'public_ip': ip, 'api_port': int(os.environ['RELAY_API_PORT']), 'domains': sorted({domain(value) for value in domains}), 'cdn_domains': sorted({domain(value) for value in cdn_domains})}))
os.chown(temporary, 0, gid); os.chmod(temporary, 0o640); os.replace(temporary, config)
for name in ('server.crt', 'server.key'):
    os.chown(root / name, 0, gid); os.chmod(root / name, 0o640)
PY
backup="$(mktemp -d)"
install_complete=0
rollback() {
  result=$?
  if [[ "${install_complete}" != 1 && -f "${backup}/agent.py" ]]; then
    for name in agent.py addresses.py credentials.py manifest.json update.py; do
      if [[ -f "${backup}/${name}" ]]; then cp -p "${backup}/${name}" "${LIB}/${name}"; else rm -f "${LIB}/${name}"; fi
    done
    systemctl restart vps-control-relay-agent.service || true
  fi
  rm -rf -- "${backup}"
  return "${result}"
}
trap rollback EXIT
for name in agent.py addresses.py credentials.py manifest.json update.py; do
  [[ ! -f "${LIB}/${name}" ]] || cp -p "${LIB}/${name}" "${backup}/${name}"
done
install -m 0755 "${SOURCE}/agent.py" "${LIB}/agent.py"
install -m 0700 "${SOURCE}/credentials.py" "${LIB}/credentials.py"
install -m 0644 "${SOURCE}/addresses.py" "${LIB}/addresses.py"
install -m 0644 "${SOURCE}/manifest.json" "${LIB}/manifest.json"
install -m 0700 "${SOURCE}/update.py" "${LIB}/update.py"
if [[ ! -f "${ROOT}/updates.json" ]]; then
  printf '{"enabled":true}\n' >"${ROOT}/updates.json"
fi
chown root:vps-relay "${ROOT}/updates.json"
chmod 0640 "${ROOT}/updates.json"
python3 "${LIB}/addresses.py" "${ROOT}"
cat >/etc/systemd/system/vps-control-relay-agent.service <<'EOF'
[Unit]
Description=312node.net Relay Agent
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
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=/var/lib/vps-control-relay-agent
UMask=0077
LimitNOFILE=32768
MemoryMax=256M
TasksMax=32
IPAccounting=true
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/vps-control-relay-addresses.service <<'EOF'
[Unit]
Description=Refresh public Relay addresses from local configuration
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-relay-agent/addresses.py
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/vps-control-relay-agent
UMask=0077
TimeoutStartSec=50
EOF
cat >/etc/systemd/system/vps-control-relay-addresses.timer <<'EOF'
[Unit]
Description=Refresh Relay address inventory
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
cat >/etc/systemd/system/vps-control-relay-update.service <<'EOF'
[Unit]
Description=Update Relay Agent from verified GitHub release
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/vps-control-relay-agent/update.py
UMask=0077
TimeoutStartSec=10min
EOF
cat >/etc/systemd/system/vps-control-relay-update.timer <<'EOF'
[Unit]
Description=Check Relay Agent updates daily
[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now vps-control-relay-addresses.timer
systemctl enable vps-control-relay-agent.service
systemctl restart vps-control-relay-agent.service || true
for attempt in {1..20}; do
  if curl --silent --fail --cacert "${ROOT}/server.crt" --max-time 2 \
    --config <(printf 'header = "Authorization: Bearer %s"\n' "$(cat "${ROOT}/token")") \
    "https://127.0.0.1:${API_PORT}/v1/status" | python3 -c 'import json,sys; value=json.load(sys.stdin); sys.exit(0 if value.get("failed_routes")==0 else 1)'; then
    if command -v ufw >/dev/null; then
      ufw allow "${API_PORT}/tcp" comment 'vps-control relay API'
      ufw allow 20000:20999/tcp comment 'vps-control relay TCP'
      ufw allow 20000:20999/udp comment 'vps-control relay UDP'
    fi
    echo 'Relay Agent установлен и готов к подключению.'
    systemctl enable --now vps-control-relay-update.timer
    install_complete=1
    exit 0
  fi
  sleep 1
done
if [[ ! -f "${backup}/agent.py" ]]; then
  systemctl disable --now vps-control-relay-agent.service || true
  systemctl disable --now vps-control-relay-addresses.timer || true
fi
echo 'Relay Agent не подтвердил запуск; проверьте journalctl -u vps-control-relay-agent.' >&2
exit 1
