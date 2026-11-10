"""Durable, explicitly requested installation and NoderaBot service lifecycle."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import assistant_control as control


def checked(command, *, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # Package/transport output may contain credentials. Public history uses
        # execute_operation's fixed failure message, never this output.
        raise RuntimeError('Assistant installation or service command failed')


def quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def install(api):
    unit_path = Path('/etc/systemd/system', control.BOT_UNIT)
    if unit_path.exists() and not unit_path.read_text().startswith('# Managed by vps-control: NoderaBot\n'):
        raise ValueError('Existing NoderaBot service is not owned by this application')
    if api.observe_service(control.BOT_UNIT).get('active'):
        raise ValueError('Stop NoderaBot before installing its runtime')
    root = control.runtime_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    root.chmod(0o755)
    requirements = Path(__file__).with_name('assistant_runtime') / 'requirements.txt'
    python = root / 'venv/bin/python'
    if not python.is_file():
        checked([os.path.realpath(sys.executable), '-m', 'venv', str(root / 'venv')], timeout=120)
    checked([str(python), '-m', 'pip', '--disable-pip-version-check', 'install', '-r', str(requirements)], timeout=600)
    checked([str(python), '-m', 'pip', 'check'])
    checked([str(python), '-c', 'import mcp, telethon, httpx'])
    # The API's restrictive umask must not make this secret-free runtime
    # unreadable to NoderaBot's DynamicUser. Never chmod symlink targets.
    for folder, directories, files in os.walk(root / 'venv', followlinks=False):
        Path(folder).chmod(0o755)
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                path.chmod(0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644)
    shutil.copyfile(requirements, root / 'requirements.txt')
    root.joinpath('requirements.txt').chmod(0o644)
    script = Path(__file__).with_name('assistant_runtime') / 'runner.py'
    settings = control.directory(api) / 'settings.json'
    unit = '\n'.join([
        '# Managed by vps-control: NoderaBot', '[Unit]', 'Description=NoderaBot Telegram interface',
        'After=network-online.target vps-control-api.service', 'Wants=network-online.target',
        '', '[Service]', 'Type=simple', 'DynamicUser=yes', 'StateDirectory=vps-control-nodera',
        'StateDirectoryMode=0700', f'LoadCredential={quoted("settings:" + str(settings))}',
        f'ExecStart={quoted(python)} {quoted(script)} --bot', 'Restart=on-failure', 'RestartSec=10',
        'TimeoutStopSec=15', 'UMask=0077', 'NoNewPrivileges=yes', 'PrivateTmp=yes',
        'PrivateDevices=yes', 'ProtectSystem=strict', 'ProtectHome=yes',
        'ProtectKernelTunables=yes', 'ProtectKernelModules=yes', 'ProtectControlGroups=yes',
        'RestrictSUIDSGID=yes', 'CapabilityBoundingSet=',
        '', '[Install]', 'WantedBy=multi-user.target', '',
    ])
    Path('/etc/systemd/system', control.BOT_UNIT).write_text(unit, encoding='utf-8')
    checked(['systemctl', 'daemon-reload'])


def perform(api, action):
    from fastapi import HTTPException
    if action == 'install':
        install(api)
        return
    if action not in ('start', 'stop', 'restart'):
        raise ValueError('Unknown NoderaBot action')
    unit_path = Path('/etc/systemd/system', control.BOT_UNIT)
    if not unit_path.is_file() or not unit_path.read_text().startswith('# Managed by vps-control: NoderaBot\n'):
        raise HTTPException(409, 'Служба NoderaBot не установлена приложением; управление недоступно')
    if action != 'stop':
        settings = control.read(api)
        if not control.runtime_ready() or not settings['modules']['nodera-bot'] or not control.ready(settings, 'nodera-bot'):
            raise HTTPException(409, 'Установите среду выполнения и сохраните настройки NoderaBot')
        legacy = api.observe_service('tg-agent-gateway.service')
        if legacy.get('active'):
            raise HTTPException(409, 'На сервере работает прежний Telegram-бот. Автоматическое переключение не выполняется')
        if legacy.get('unit_present') is not False and legacy.get('state') not in ('inactive', 'failed'):
            raise HTTPException(409, 'Не удалось проверить прежний Telegram-бот; запуск NoderaBot отложен')
    checked(['systemctl', 'disable' if action == 'stop' else 'enable', control.BOT_UNIT])
    checked(['systemctl', action, control.BOT_UNIT])
    observed = api.observe_service(control.BOT_UNIT)
    if (action == 'stop' and observed.get('state') != 'inactive') or (action != 'stop' and not observed.get('active')):
        raise HTTPException(409, 'Ожидаемое состояние NoderaBot не подтверждено. Проверьте службу')


if __name__ == '__main__':
    import main as api
    from service_worker import execute_operation
    action = sys.argv[1]
    succeeded = execute_operation(api, 'assistant:' + action, lambda: perform(api, action),
                                  os.environ['VPS_CONTROL_OPERATION_ID'], os.environ['VPS_CONTROL_OPERATION_UNIT'])
    sys.exit(0 if succeeded else 1)
