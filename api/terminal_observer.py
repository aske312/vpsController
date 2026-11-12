"""Read-only terminal profiles under a dedicated, sandboxed Linux identity."""
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

USER = 'vps-control-observer'
MAX_BYTES = 32768
PROFILES = ('help', 'uptime', 'uname', 'memory', 'disk', 'block_devices', 'processes', 'sockets', 'addresses', 'routes', 'service', 'read_file')
FILES = {'os_release': '/etc/os-release', 'load': '/proc/loadavg', 'memory': '/proc/meminfo', 'uptime': '/proc/uptime'}
PROPERTIES = ('NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=yes', 'PrivateDevices=yes', 'SupplementaryGroups=',
    'WorkingDirectory=/', 'UnsetEnvironment=ADMIN_PASSWORD ADMIN_USER API_KEY BOT_TOKEN CONTROL_TOKEN TELEGRAM_API_HASH',
    'CapabilityBoundingSet=', 'AmbientCapabilities=', 'ProtectKernelTunables=yes', 'ProtectKernelModules=yes',
    'ProtectControlGroups=yes', 'ProtectClock=yes', 'RestrictSUIDSGID=yes', 'RestrictNamespaces=yes',
    'LockPersonality=yes', 'MemoryDenyWriteExecute=yes', 'RestrictAddressFamilies=AF_UNIX AF_NETLINK',
    'IPAddressDeny=any', 'ReadOnlyPaths=/proc /sys',
    'InaccessiblePaths=-/etc/vps-control -/var/lib/vps-control -/var/lib/vps-control-ai-agent -/var/lib/vps-control-nodera-bot -/etc/ssh -/etc/wireguard -/run/credentials -/etc/shadow -/etc/gshadow',
    'TemporaryFileSystem=/tmp:ro /var/tmp:ro', 'SystemCallFilter=~@mount @reboot @swap @raw-io @privileged',
    'RuntimeMaxSec=8s', 'TimeoutStopSec=1s', 'KillMode=control-group', 'TasksMax=16', 'MemoryMax=64M',
    'CPUQuota=25%', 'LimitFSIZE=0', 'UMask=0077')


class Query(BaseModel):
    model_config = {'extra': 'forbid'}
    command: Literal['help', 'uptime', 'uname', 'memory', 'disk', 'block_devices', 'processes', 'sockets', 'addresses', 'routes', 'service', 'read_file'] = 'help'
    service: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    file: Literal['os_release', 'load', 'memory', 'uptime'] | None = None
    family: Literal['ipv4', 'ipv6'] = 'ipv4'
    limit: int = Field(default=60, strict=True, ge=1, le=200)


def identity():
    if os.name != 'posix': raise HTTPException(503, 'Read-only терминал требует Linux/systemd')
    import pwd
    import grp
    try:
        user = pwd.getpwnam(USER)
        group = grp.getgrnam(USER)
    except KeyError: raise HTTPException(409, 'Обновите компонент Nodera: диагностическая УЗ ещё не создана') from None
    if (user.pw_uid == 0 or group.gr_gid == 0 or user.pw_gid != group.gr_gid
            or user.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false')
            or user.pw_dir != '/nonexistent' or set(os.getgrouplist(USER, user.pw_gid)) != {user.pw_gid}):
        raise HTTPException(409, 'Диагностическая УЗ имеет неожиданные права; запуск запрещён')
    return user.pw_uid, user.pw_gid


def provision(api):
    """Called only by the explicit component installer, never by a diagnostic request."""
    if not api.run('getent', 'passwd', USER):
        api.run('groupadd', '--system', '--force', USER, check=True)
        api.run('useradd', '--system', '--gid', USER, '--home-dir', '/nonexistent', '--no-create-home',
                '--shell', '/usr/sbin/nologin', '--password', '!', USER, check=True)
    identity()


def argv(api, query):
    commands = {'uptime': ['uptime'], 'uname': ['uname', '-srmo'], 'memory': ['free', '-b'],
        'disk': ['df', '-P', '-B1', '/'], 'block_devices': ['lsblk', '-J', '-o', 'NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT'],
        'processes': ['ps', '-eo', 'pid,ppid,user,comm,pcpu,pmem', '--sort=-pcpu'],
        'sockets': ['ss', '-H', '-lntu'], 'addresses': ['ip', '-j', 'address', 'show'],
        'routes': ['ip', '-4' if query.family == 'ipv4' else '-6', '-j', 'route', 'show']}
    if query.command == 'service':
        definition = api.managed_services().get(query.service)
        if not definition or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9@_.-]{0,150}\.(?:service|socket|timer)', definition['unit']): raise HTTPException(422, 'Выберите service из vps_services')
        return ['systemctl', 'show', definition['unit'], '--no-pager',
                '--property=LoadState,ActiveState,SubState,Type,MainPID,Result,NRestarts']
    if query.command == 'read_file':
        if query.file not in FILES: raise HTTPException(422, 'Выберите file: os_release, load, memory, uptime')
        return ['cat', FILES[query.file]]
    return commands[query.command]


def launch_command(arguments, uid, gid, *, unit):
    executable = shutil.which(arguments[0], path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not executable: raise HTTPException(503, 'Диагностическая утилита не установлена: ' + arguments[0])
    systemd = shutil.which('systemd-run', path='/usr/bin:/bin')
    if not systemd: raise HTTPException(503, 'Systemd sandbox недоступен; запуск без него запрещён')
    return [systemd, '--quiet', '--wait', '--pipe', '--collect', '--unit=' + unit, '--uid=' + str(uid), '--gid=' + str(gid),
            *['--property=' + value for value in PROPERTIES], '--setenv=PATH=/usr/sbin:/usr/bin:/sbin:/bin',
            '--setenv=LC_ALL=C', '--', executable, *arguments[1:]]


def bounded_run(command):
    output, truncated = bytearray(), threading.Event()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}, start_new_session=True)
    def read():
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk: break
                remaining = MAX_BYTES - len(output)
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated.set()
                    break
        except OSError: pass
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    timeout = False
    try:
        # A blocked/verbose process is bounded by both systemd and this outer deadline.
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        timeout = True
    finally:
        if process.poll() is None:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=2)
        reader.join(2)
        process.stdout.close()
    text = bytes(output).decode('utf-8', errors='replace')
    if truncated.is_set(): text = text.rsplit('\n', 1)[0] if '\n' in text else ''
    return text, process.returncode, timeout, truncated.is_set()


_active = threading.Lock()


def call(api, arguments, actor):
    import assistant_control
    import assistant_roles
    import assistant_diagnostics
    import diagnostic_tools
    settings = assistant_control.read(api)
    for permission in ('server_read', 'terminal_read'): assistant_roles.require(settings, actor, permission)
    if arguments.get('method', 'GET') != 'GET': raise HTTPException(422, 'Терминал поддерживает только чтение')
    try: query = Query.model_validate(arguments.get('body') or {})
    except ValidationError: raise HTTPException(422, 'Выберите разрешённый профиль команды; shell и произвольные аргументы недоступны') from None
    result = diagnostic_tools.report('vps_terminal')
    if query.command == 'help':
        result['commands'] = list(PROFILES)
        result['files'] = list(FILES)
        result['services'] = list(api.managed_services())[:64]
        result['coverage'] = {'scope': 'read_only_profiles; no_shell; no_arbitrary_paths_or_arguments', 'run_as': USER}
        return result
    arguments = argv(api, query)
    uid, gid = identity()
    unit = 'vps-control-observer-' + uuid.uuid4().hex
    command = launch_command(arguments, uid, gid, unit=unit)
    if not _active.acquire(blocking=False): raise HTTPException(429, 'Диагностический терминал уже занят')
    try:
        raw, code, timeout, truncated = bounded_run(command)
        safe = assistant_diagnostics.sanitize(api, raw, max_chars=MAX_BYTES, max_string=MAX_BYTES)
        lines = safe.splitlines()
        diagnostic_tools.observation(result, 'terminal', 'isolated_read_only_process', {
            'command': query.command, 'lines': lines[:query.limit], 'exit_code': code, 'timeout': timeout,
            'unit': unit, 'run_as': USER, 'uid': uid}, 'unknown' if timeout else 'observed' if code == 0 else 'failed')
        result['coverage'] = {'sandbox': 'systemd_read_only_no_capabilities_no_IP_sockets', 'truncated': truncated or len(lines) > query.limit or len(safe) < len(raw),
            'max_bytes': MAX_BYTES, 'max_lines': query.limit, 'runtime_limit_s': 8, 'scope': 'selected_command_only; not_application_functional_health'}
        if timeout or code != 0: result['missing_data'].append({'source': 'terminal', 'reason': 'command_or_sandbox_did_not_complete_successfully'})
    except OSError: raise HTTPException(503, 'Не удалось запустить изолированный терминал; fallback на root запрещён') from None
    finally: _active.release()
    return result
