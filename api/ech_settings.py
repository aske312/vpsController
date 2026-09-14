"""Manual DNS publication of Caddy-managed ECH public configurations."""
from __future__ import annotations

import base64
import fcntl
import ipaddress
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import cdn_security

STATE = Path('/etc/vps-control/ech.json')
STORAGE = Path('/var/lib/caddy/.local/share/caddy/ech/configs')
BEGIN = '# BEGIN GATE.312 ECH'
END = '# END GATE.312 ECH'


def capability() -> dict:
    try:
        result = subprocess.run(['caddy', 'version'], capture_output=True, text=True, check=True, timeout=5)
        match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', result.stdout.strip())
        if match:
            version = '.'.join(match.groups())
            supported = tuple(map(int, match.groups())) >= (2, 10, 0)
            return {'supported': supported, 'version': version, 'message': '' if supported else f'Установлен Caddy {version}. Для ECH требуется Caddy 2.10.0 или новее. Сначала обновите Caddy на сервере.'}
    except (OSError, subprocess.SubprocessError):
        pass
    return {'supported': False, 'version': None, 'message': 'Не удалось определить версию Caddy. Подготовка ECH недоступна.'}


def require_support() -> None:
    result = capability()
    if not result['supported']:
        raise ValueError(result['message'])


def hostname(value: str) -> str:
    value = value.strip().lower().rstrip('.')
    if len(value) > 253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')):
        raise ValueError('Укажите полное доменное имя без протокола и порта')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError('Для ECH требуется домен, а не IP-адрес')


def settings() -> dict:
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {'domains': [], 'public_name': ''}


def inject(text: str, state: dict | None = None) -> str:
    state = settings() if state is None else state
    text = re.sub(r'\s*# BEGIN GATE\.312 ECH.*?# END GATE\.312 ECH\n?', '\n', text, flags=re.S)
    if not state.get('domains'):
        return text
    name = hostname(state['public_name'])
    if not text.lstrip().startswith('{'):
        raise ValueError('В конфигурации шлюза отсутствует блок общих настроек')
    return text.replace('{', '{\n    ' + BEGIN + '\n    ech ' + name + '\n    ' + END + '\n', 1)


def public_name(config: bytes) -> str:
    """Parse only the public ECHConfig; never read key.bin."""
    if len(config) < 12 or config[:2] != b'\xfe\x0d' or int.from_bytes(config[2:4], 'big') != len(config) - 4:
        raise ValueError('Некорректная публичная конфигурация ECH')
    offset = 7
    key_length = int.from_bytes(config[offset:offset + 2], 'big')
    offset += 2 + key_length
    suites_length = int.from_bytes(config[offset:offset + 2], 'big')
    offset += 2 + suites_length + 1
    if offset >= len(config):
        raise ValueError('Неполная публичная конфигурация ECH')
    length = config[offset]
    name = config[offset + 1:offset + 1 + length]
    if len(name) != length or len(config) < offset + 1 + length + 2:
        raise ValueError('Неполное имя ECH')
    return name.decode('ascii')


def record(domain: str, state: dict | None = None) -> dict | None:
    domain = hostname(domain)
    state = settings() if state is None else state
    if domain not in state.get('domains', []):
        return None
    configs = []
    for path in sorted(STORAGE.glob('*/config.bin')):
        raw = path.read_bytes()
        if public_name(raw) == state['public_name']:
            configs.append(raw)
    if not configs:
        return None
    raw = b''.join(configs)
    encoded = base64.b64encode(len(raw).to_bytes(2, 'big') + raw).decode('ascii')
    parameters = f'alpn="h2,http/1.1" ech="{encoded}"'
    return {'domain': domain, 'public_name': state['public_name'], 'type': 'HTTPS', 'priority': 1,
            'target': '.', 'ttl': 300, 'parameters': parameters, 'content': f'1 . {parameters}'}


def check_domain(domain: str) -> str:
    domain = hostname(domain)
    if domain not in {r.get('domain', '').lower() for r in cdn_security.read_routes()}:
        raise ValueError('Сначала сохраните подключение с этим доменом')
    values = cdn_security.read_env(cdn_security.ENV)
    origin = {v for k in ('PUBLIC_IP', 'PUBLIC_IPV4', 'PUBLIC_IPV6') if (v := values.get(k))}
    try:
        resolved = {item[4][0] for item in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError('Домен пока не определяется в DNS') from exc
    if not resolved or not origin or not resolved.issubset(origin):
        raise ValueError('Для ECH на VPS домен должен указывать напрямую на этот сервер. При проксировании ECH настраивается у CDN-провайдера.')
    return domain


def prepare(domain: str, progress=lambda *_: None) -> dict:
    require_support()
    domain = check_domain(domain)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # Serialize against CF policy changes using the existing gateway lock.
    with cdn_security.SNIPPET.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous_state = STATE.read_bytes() if STATE.exists() else None
        previous = cdn_security.CADDY.read_bytes()
        state = settings()
        state['domains'] = sorted(set(state.get('domains', [])) | {domain})
        if not state.get('public_name'):
            # A common outer name protects all configured inner names.
            values = cdn_security.read_env(cdn_security.ENV)
            state['public_name'] = hostname(values.get('PUBLIC_DOMAIN') or domain)
        candidate = cdn_security.CADDY.with_suffix('.ech-candidate')
        candidate.write_text(inject(previous.decode(), state))
        os.chmod(candidate, 0o644)
        applied = False
        try:
            progress(20, 'Создание ключей ECH и проверка шлюза')
            # Provision with the service identity so validation and reload share keys.
            subprocess.run(['runuser', '-u', 'caddy', '--', 'env', 'HOME=/var/lib/caddy',
                            'XDG_DATA_HOME=/var/lib/caddy/.local/share', 'caddy', 'validate',
                            '--config', str(candidate), '--adapter', 'caddyfile'], check=True, capture_output=True, timeout=30)
            result = record(domain, state)
            if result is None:
                raise ValueError('Caddy не создал публичную конфигурацию ECH. Требуется Caddy с поддержкой ECH.')
            progress(65, 'Применение ECH на сервере')
            candidate.replace(cdn_security.CADDY)
            applied = True
            subprocess.run(['systemctl', 'reload', 'caddy.service'], check=True, capture_output=True, timeout=20)
            temporary = STATE.with_suffix('.tmp')
            temporary.write_text(json.dumps(state) + '\n')
            os.chmod(temporary, 0o600)
            temporary.replace(STATE)
            return result
        except Exception:
            if applied:
                cdn_security.CADDY.write_bytes(previous)
                subprocess.run(['systemctl', 'reload', 'caddy.service'], check=True, capture_output=True, timeout=20)
            if previous_state is None:
                STATE.unlink(missing_ok=True)
            else:
                STATE.write_bytes(previous_state)
            raise
        finally:
            candidate.unlink(missing_ok=True)
