"""Image installation and durable NoderaBot service commands."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import assistant_control as control

MARKER = '# Managed by vps-control: '


def checked(command, *, timeout=60):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Assistant installation or service command failed')


def quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit_name(component):
    return control.AGENT_UNIT if component == 'ai-agent' else control.BOT_UNIT


def assert_unit_owner(component):
    path = Path('/etc/systemd/system', unit_name(component))
    if path.exists() and not path.read_text().startswith(MARKER + component + '\n'):
        raise ValueError('Existing service is not owned by this component image')
    return path


def require_idle(component):
    script = str(control.runtime_directory(component) / ('runner.py' if component == 'ai-agent' else 'bot.py')).encode()
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        if script in args and path.parent.name != str(os.getpid()):
            raise ValueError('Component has running requests; retry after they finish')


def install(api, component):
    if component not in control.MODULES:
        raise ValueError('Unknown component')
    unit_path = assert_unit_owner(component)
    if component == 'nodera-bot' and api.observe_service(control.BOT_UNIT).get('active'):
        raise ValueError('Stop NoderaBot before updating its image')
    require_idle(component)
    root = control.runtime_directory(component)
    root.mkdir(parents=True, exist_ok=True, mode=0o755); root.chmod(0o755)
    image = api.INSTALL_DIR / 'protocol-images' / component
    requirements = image / 'requirements.txt'
    python = root / 'venv/bin/python'
    if not python.is_file():
        checked([os.path.realpath(sys.executable), '-m', 'venv', str(root / 'venv')], timeout=120)
    checked([str(python), '-m', 'pip', '--disable-pip-version-check', 'install', '-r', str(requirements)], timeout=600)
    checked([str(python), '-m', 'pip', 'check'])
    script_name = 'runner.py' if component == 'ai-agent' else 'bot.py'
    shutil.copyfile(image / script_name, root / script_name)
    (root / script_name).chmod(0o644)
    checked([str(python), str(root / script_name), '--self-check'])
    for folder, _, files in os.walk(root / 'venv', followlinks=False):
        Path(folder).chmod(0o755)
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                path.chmod(0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644)
    control.migrate_settings(api)
    lines = [MARKER + component, '[Unit]', 'Description=' + ('AI-agent runtime (on-demand workers)' if component == 'ai-agent' else 'NoderaBot Telegram interface'),
             'After=network-online.target', 'Wants=network-online.target', '', '[Service]', 'DynamicUser=yes',
             'UMask=0077', 'NoNewPrivileges=yes', 'PrivateTmp=yes', 'PrivateDevices=yes',
             'ProtectSystem=strict', 'ProtectHome=yes', 'CapabilityBoundingSet=']
    if component == 'ai-agent':
        lines += ['Type=oneshot', 'RemainAfterExit=yes', f'ExecStart={quoted(python)} {quoted(root / script_name)} --self-check']
    else:
        credentials = control.directory(api) / 'nodera-bot' / 'runtime.json'
        lines += ['Type=simple', 'StateDirectory=vps-control-nodera', 'StateDirectoryMode=0700',
                  f'LoadCredential={quoted("settings:" + str(credentials))}',
                  f'ExecStart={quoted(python)} {quoted(root / script_name)} --bot', 'Restart=on-failure',
                  'RestartSec=10', 'TimeoutStopSec=15']
    lines += ['', '[Install]', 'WantedBy=multi-user.target', '']
    unit_path.write_text('\n'.join(lines), encoding='utf-8')
    checked(['systemctl', 'daemon-reload'])
    if component == 'ai-agent':
        checked(['systemctl', 'enable', control.AGENT_UNIT])
        checked(['systemctl', 'restart', control.AGENT_UNIT])
        if not api.observe_service(control.AGENT_UNIT).get('active'):
            raise ValueError('AI-agent runtime readiness is not confirmed')
    elif api.observe_service(control.BOT_UNIT).get('unit_present') is not True:
        raise ValueError('NoderaBot service registration is not confirmed')
    version = __import__('json').loads((image / 'manifest.json').read_text())['version']
    (root / 'version').write_text(version + '\n'); (root / 'version').chmod(0o644)
    if component == 'nodera-bot':
        saved = control.read(api)
        if saved['modules']['nodera-bot'] and control.ready(saved, 'nodera-bot'):
            perform(api, 'start', restoring=True)


def remove(api, component):
    unit_path = assert_unit_owner(component)
    if unit_path.exists(): checked(['systemctl', 'disable', '--now', unit_name(component)])
    require_idle(component)
    if unit_path.exists(): unit_path.unlink()
    checked(['systemctl', 'daemon-reload'])
    root = control.runtime_directory(component)
    if root.is_symlink() or root.resolve() != Path('/var/lib/vps-control-' + component):
        raise ValueError('Unexpected component runtime directory; removal stopped')
    if root.exists(): shutil.rmtree(root)
    # Private settings, sessions and bot offsets are explicitly retained.


def perform(api, action, *, restoring=False):
    from fastapi import HTTPException
    if action not in ('start', 'stop', 'restart'):
        raise ValueError('Use the image catalog to install a component')
    if restoring:
        # The image installer owns admission. A retained receipt becomes
        # installed only after this post-install startup has completed.
        control.ComponentRegistry(api.DATA_DIR).require_managed('nodera-bot', allow_retained=True)
        if not control.runtime_ready('nodera-bot'):
            raise HTTPException(409, 'Исполняемая часть NoderaBot не подтверждена')
    else:
        control.require_installed(api, 'nodera-bot')
    unit_path = assert_unit_owner('nodera-bot')
    if not unit_path.is_file(): raise HTTPException(409, 'Служба NoderaBot не установлена')
    if action != 'stop':
        settings = control.read(api)
        if not settings['modules']['nodera-bot'] or not control.ready(settings, 'nodera-bot'):
            raise HTTPException(409, 'Сохраните настройки NoderaBot перед запуском')
        legacy = api.observe_service('tg-agent-gateway.service')
        if legacy.get('active') or legacy.get('unit_present') is not False and legacy.get('state') not in ('inactive', 'failed'):
            raise HTTPException(409, 'Остановка прежнего Telegram-бота не подтверждена')
        credentials = {'modules': settings['modules'], 'mcp': settings['mcp'], 'bot_token': settings['bot_token'],
                       'control_token': settings['control_token'], 'accounts': [{k: v for k, v in a.items() if k != 'session'} for a in settings['accounts']]}
        control.operations.atomic_json(control.directory(api) / 'nodera-bot' / 'runtime.json', credentials)
    checked(['systemctl', 'disable' if action == 'stop' else 'enable', control.BOT_UNIT])
    checked(['systemctl', action, control.BOT_UNIT])
    observed = api.observe_service(control.BOT_UNIT)
    if (action == 'stop' and observed.get('state') != 'inactive') or (action != 'stop' and not observed.get('active')):
        raise HTTPException(409, 'Ожидаемое состояние NoderaBot не подтверждено')


if __name__ == '__main__':
    if sys.argv[1] in ('image-install', 'image-remove'):
        from service_state import observe_service
        api = SimpleNamespace(DATA_DIR=Path(os.getenv('DATA_DIR', '/var/lib/vps-control')),
                              INSTALL_DIR=Path(os.getenv('INSTALL_DIR', '/opt/vps-control')), observe_service=observe_service)
        (install if sys.argv[1] == 'image-install' else remove)(api, sys.argv[2])
    else:
        import main as api
        from service_worker import execute_operation
        action = sys.argv[1]
        succeeded = execute_operation(api, 'assistant:' + action, lambda: perform(api, action),
                                      os.environ['VPS_CONTROL_OPERATION_ID'], os.environ['VPS_CONTROL_OPERATION_UNIT'])
        sys.exit(0 if succeeded else 1)
