"""Encrypted hardening state attached to the existing application recovery point."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

FILES = (
    '/etc/sysctl.d/99-vps-control-routing.conf',
    '/etc/ssh/sshd_config.d/99-vps-control-tunnels.conf',
    '/etc/fail2ban/jail.d/vps-control.local',
    '/etc/systemd/system/apt-daily-upgrade.service.d/vps-control.conf',
    '/etc/systemd/system/vps-control-api.service',
    '/etc/hosts', '/etc/default/ufw', '/etc/ufw/ufw.conf',
    '/etc/ufw/user.rules', '/etc/ufw/user6.rules',
    '/usr/local/sbin/vps-control',
)
UNITS = ('ssh.service', 'fail2ban.service', 'auditd.service', 'apparmor.service',
         'apt-daily-upgrade.timer', 'ufw.service', 'vps-control-api.service',
         'vps-control-web.service', 'caddy.service', 'vps-control-mihomo-manager.service')
LEGACY_FILES, LEGACY_UNITS = FILES, UNITS
FILES += ('/etc/systemd/system/vps-control-auto-update.service', '/etc/systemd/system/vps-control-auto-update.timer')
UNITS += ('vps-control-auto-update.timer',)
SYSCTLS = ('net.ipv4.ip_forward', 'net.ipv4.tcp_syncookies',
           'net.ipv4.conf.all.accept_redirects', 'net.ipv4.conf.default.accept_redirects',
           'net.ipv4.conf.all.send_redirects', 'net.ipv4.conf.default.send_redirects',
           'net.ipv4.conf.all.accept_source_route', 'net.ipv4.conf.default.accept_source_route',
           'kernel.dmesg_restrict')
ENABLED = {'enabled', 'enabled-runtime', 'disabled', 'masked', 'masked-runtime',
           'static', 'indirect', 'alias', 'linked', 'linked-runtime', 'generated', 'transient'}


def command(run, *args):
    result = run(list(args), capture_output=True, text=True, timeout=30, check=False,
                 env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError('Не удалось подтвердить состояние или восстановление защиты')
    return result.stdout.strip()


def service(run, unit):
    result = run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,UnitFileState'],
                 capture_output=True, text=True, timeout=10, check=False,
                 env={**os.environ, 'LC_ALL': 'C'})
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('LoadState') == 'not-found':
        return {'present': False, 'active': False, 'enabled': None}
    if result.returncode or values.get('LoadState') not in {'loaded', 'masked'} \
            or values.get('ActiveState') not in {'active', 'inactive', 'failed'} \
            or values.get('UnitFileState') not in ENABLED:
        raise ValueError('Состояние службы не подтверждено; защита не изменена')
    return {'present': True, 'active': values['ActiveState'] == 'active', 'enabled': values['UnitFileState']}


def snapshot(*, root=Path('/'), run=subprocess.run):
    files = {}
    for name in FILES:
        path = root / name.lstrip('/')
        if path.is_symlink():
            files[name] = {'kind': 'symlink', 'target': os.readlink(path)}
        elif not path.exists():
            files[name] = {'kind': 'absent'}
        elif path.is_file():
            details = path.stat()
            if details.st_size > 2 * 1024 * 1024:
                raise ValueError('Управляемый файл слишком велик для снимка защиты')
            files[name] = {'kind': 'file', 'data': base64.b64encode(path.read_bytes()).decode(),
                           'mode': stat.S_IMODE(details.st_mode), 'uid': details.st_uid, 'gid': details.st_gid}
        else:
            raise ValueError('Неожиданный тип управляемого файла защиты')
    states = {unit: service(run, unit) for unit in UNITS}
    values = {key: command(run, 'sysctl', '-n', key) for key in SYSCTLS}
    try:
        firewall = command(run, 'ufw', 'status')
    except FileNotFoundError:
        firewall = 'Status: inactive'
    if not re.search(r'^Status: (?:active|inactive)$', firewall, re.M):
        raise ValueError('Состояние firewall не подтверждено')
    value = {'schema': 1, 'files': files, 'services': states, 'sysctl': values,
             'ufw_active': bool(re.search(r'^Status: active$', firewall, re.M))}
    validate(value)
    return value


def validate(value):
    if not isinstance(value, dict) or value.get('schema') != 1 or type(value.get('ufw_active')) is not bool:
        raise ValueError('Неверный снимок защиты')
    legacy = isinstance(value.get('files'), dict) and isinstance(value.get('services'), dict) and set(value['files']) == set(LEGACY_FILES) and set(value['services']) == set(LEGACY_UNITS)
    for key, names in (('files', LEGACY_FILES if legacy else FILES), ('services', LEGACY_UNITS if legacy else UNITS), ('sysctl', SYSCTLS)):
        if not isinstance(value.get(key), dict) or set(value[key]) != set(names):
            raise ValueError('Неполный снимок защиты')
    for item in value['files'].values():
        if not isinstance(item, dict) or item.get('kind') not in {'file', 'absent', 'symlink'}:
            raise ValueError('Неверный файл снимка')
        if item['kind'] == 'file':
            if not isinstance(item.get('data'), str) or len(item['data']) > 3 * 1024 * 1024:
                raise ValueError('Неверное содержимое снимка')
            base64.b64decode(item['data'], validate=True)
            if any(type(item.get(key)) is not int or item[key] < 0 for key in ('mode', 'uid', 'gid')) or item['mode'] > 0o7777:
                raise ValueError('Неверные права файла снимка')
        if item['kind'] == 'symlink' and (not isinstance(item.get('target'), str) or not item['target'] or '\0' in item['target']):
            raise ValueError('Неверная ссылка снимка')
    for item in value['services'].values():
        if not isinstance(item, dict) or type(item.get('present')) is not bool or type(item.get('active')) is not bool \
                or (item['present'] and item.get('enabled') not in ENABLED) \
                or (not item['present'] and (item.get('enabled') is not None or item['active'])):
            raise ValueError('Неверное состояние службы снимка')
    if any(not isinstance(text, str) or not re.fullmatch(r'\d{1,10}', text) for text in value['sysctl'].values()):
        raise ValueError('Неверные значения sysctl')


def restore(value, *, root=Path('/'), run=subprocess.run):
    validate(value)  # Validate the complete snapshot before any write/command.
    errors = []

    def attempt(callback):
        try:
            callback()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as failure:
            errors.append(failure)

    def file(name, item):
        path = root / name.lstrip('/')
        if item['kind'] == 'absent':
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.hardening-recovery-', dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as stream:
                if item['kind'] == 'file':
                    stream.write(base64.b64decode(item['data'], validate=True))
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.chmod(temporary, item['mode'])
                    if os.name != 'nt':
                        os.chown(temporary, item['uid'], item['gid'])
            if item['kind'] == 'symlink':
                temporary.unlink()
                temporary.symlink_to(item['target'])
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    for name, item in value['files'].items():
        attempt(lambda name=name, item=item: file(name, item))
    attempt(lambda: command(run, 'systemctl', 'daemon-reload'))
    for key, text in value['sysctl'].items():
        attempt(lambda key=key, text=text: command(run, 'sysctl', '-w', f'{key}={text}'))

    def unit(name, before):
        current = service(run, name)
        if not current['present']:
            if before['active']:
                raise RuntimeError('Отсутствует ранее активная служба')
            return
        enabled = before['enabled'] if before['present'] else 'disabled'
        if current['enabled'] in {'masked', 'masked-runtime'} and enabled not in {'masked', 'masked-runtime'}:
            command(run, 'systemctl', 'unmask', *(['--runtime'] if current['enabled'] == 'masked-runtime' else []), name)
        if before['active']:
            if name == 'ssh.service':
                command(run, 'sshd', '-t')
                command(run, 'systemctl', 'reload' if current['active'] else 'start', name)
            elif name in {'auditd.service', 'apparmor.service', 'apt-daily-upgrade.timer', 'vps-control-auto-update.timer', 'ufw.service'}:
                if not current['active']:
                    command(run, 'systemctl', 'start', name)
            else:
                command(run, 'systemctl', 'restart', name)
        elif current['active']:
            command(run, 'systemctl', 'stop', name)
        if enabled in {'enabled', 'enabled-runtime'}:
            if current['enabled'] != enabled:
                command(run, 'systemctl', 'disable', name)
                command(run, 'systemctl', 'enable', *(['--runtime'] if enabled == 'enabled-runtime' else []), name)
        elif enabled in {'disabled', 'masked', 'masked-runtime'} and current['enabled'] != enabled:
            command(run, 'systemctl', 'disable', name)
            if enabled.startswith('masked'):
                command(run, 'systemctl', 'mask', *(['--runtime'] if enabled == 'masked-runtime' else []), name)
        after = service(run, name)
        if after['active'] != before['active'] or (before['present'] and after['enabled'] != enabled) \
                or (not before['present'] and after['enabled'] in {'enabled', 'enabled-runtime'}):
            raise RuntimeError('Восстановление службы не подтверждено')

    for name, before in value['services'].items():
        attempt(lambda name=name, before=before: unit(name, before))

    def firewall():
        try:
            observed = command(run, 'ufw', 'status')
        except FileNotFoundError:
            if not value['ufw_active']:
                return
            raise
        if not re.search(r'^Status: (?:active|inactive)$', observed, re.M):
            raise ValueError('Состояние firewall неизвестно')
        active = bool(re.search(r'^Status: active$', observed, re.M))
        if value['ufw_active']:
            command(run, 'ufw', 'reload') if active else command(run, 'ufw', '--force', 'enable')
        elif active:
            command(run, 'ufw', '--force', 'disable')
        observed = command(run, 'ufw', 'status')
        expected = 'Status: active' if value['ufw_active'] else 'Status: inactive'
        if not re.search(r'^' + expected + r'$', observed, re.M):
            raise RuntimeError('Восстановление firewall не подтверждено')

    attempt(firewall)

    def verify_sysctl(key, expected):
        if command(run, 'sysctl', '-n', key) != expected:
            raise RuntimeError('sysctl не восстановлен')

    for key, text in value['sysctl'].items():
        attempt(lambda key=key, text=text: verify_sysctl(key, text))
    if errors:
        raise RuntimeError('Восстановление защиты не подтверждено; снимок сохранён') from errors[0]


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def paths(archive):
    if not re.fullmatch(r'recovery-\d{8}T\d{6}Z\.tar\.gz\.enc', archive.name):
        raise ValueError('Неверный путь точки восстановления')
    return Path(str(archive) + '.hardening.enc'), archive.parent / 'recovery.key'


def prepare(archive, *, root=Path('/'), run=subprocess.run):
    sidecar, key = paths(archive)
    manifest = Path(str(archive) + '.sha256')
    if sidecar.exists():
        raise ValueError('Снимок защиты уже существует')
    value = snapshot(root=root, run=run)
    with tempfile.TemporaryDirectory(prefix='.hardening-', dir=archive.parent) as temporary:
        plaintext = Path(temporary) / 'state.json'
        plaintext.write_text(json.dumps(value), encoding='utf-8')
        os.chmod(plaintext, 0o600)
        ciphertext = Path(temporary) / 'state.enc'
        command(run, 'openssl', 'enc', '-aes-256-cbc', '-salt', '-pbkdf2', '-in', str(plaintext), '-out', str(ciphertext), '-pass', f'file:{key}')
        decoded = command(run, 'openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', str(ciphertext), '-pass', f'file:{key}')
        if json.loads(decoded) != value:
            raise ValueError('Проверка снимка защиты не пройдена')
        staged_manifest = Path(temporary) / 'manifest'
        staged_manifest.write_text(f'{digest(archive)}  {archive}\n{digest(ciphertext)}  {sidecar}\n', encoding='utf-8')
        for path in (ciphertext, staged_manifest):
            os.chmod(path, 0o600)
            with path.open('r+b') as stream:
                os.fsync(stream.fileno())
        ciphertext.replace(sidecar)
        try:
            staged_manifest.replace(manifest)
        except OSError:
            sidecar.unlink()
            raise


def read(archive, *, run=subprocess.run):
    sidecar, key = paths(archive)
    expected = dict((name, checksum) for checksum, name in (line.split(maxsplit=1) for line in Path(str(archive) + '.sha256').read_text().splitlines()))
    if any(expected.get(str(path)) != digest(path) for path in (archive, sidecar)):
        raise ValueError('Контрольная сумма снимка защиты не совпала')
    if sidecar.stat().st_size > 40 * 1024 * 1024:
        raise ValueError('Снимок защиты слишком велик')
    raw = command(run, 'openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', str(sidecar), '-pass', f'file:{key}')
    value = json.loads(raw)
    validate(value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'check', 'restore'))
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args.archive)
        else:
            state = read(args.archive)
            if args.action == 'restore':
                restore(state)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Не удалось подтвердить снимок или восстановление защиты; рабочий marker сохранён')
