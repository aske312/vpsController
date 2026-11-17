"""Managed Snell v6 listeners, one private Mihomo process per credential."""
import json
import re
import secrets
import time

from fastapi import HTTPException

MODULE = 'transport-snell'
PREFIX = 'vps-control-mihomo-snell@'
UNIT = re.compile(r'vps-control-mihomo-snell@[A-Za-z0-9_-]{1,128}\.service')


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid Snell connection identity')
    return value


def configuration(instance, credential):
    identity(instance)
    mode = credential.get('mode', 'default')
    if mode not in ('default', 'unshaped'):
        raise ValueError('Unsupported Snell v6 mode')
    psk = credential.get('password', '')
    if not isinstance(psk, str) or not 12 <= len(psk.encode()) <= 255:
        raise ValueError('Invalid Snell v6 key')
    port = credential.get('port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid Snell listener port')
    return {'mode': 'rule', 'log-level': 'warning', 'ipv6': True,
            'external-controller-unix': f'/run/vps-control-mihomo-snell/{instance}/controller.sock',
            'listeners': [{'name': instance, 'type': 'snell', 'listen': '::', 'port': port,
                           'version': 6, 'mode': mode, 'psk': psk, 'udp': True}],
            'rules': ['MATCH,DIRECT']}


def add(api, profile_id, connection_id, settings):
    instance = identity(f'{profile_id}-{connection_id}')
    values = {**api.module_settings(MODULE), **(settings or {})}
    owner = f'mihomo:snell:{instance}'
    used = {int(row.get('credential', {}).get('port', 0)) for profile in api.profiles()
            for row in [*profile.get('connections', []), *profile.get('retiring_connections', [])]
            if row.get('component') == MODULE}
    try:
        port = api.free_module_port(int(values.get('port_start', 33000)), {'tcp'}, used=used, count=2000, owner=owner)
        credential = {'instance_id': instance, 'port': port, 'password': secrets.token_urlsafe(32),
                      'version': 6, 'mode': values.get('mode', 'default')}
        path = api.CONFIG_ROOT / 'snell' / f'{instance}.json'
        api.atomic_json(path, configuration(instance, credential))
        # The native parser validates the exact staged server configuration.
        api.CORE_HOME.mkdir(parents=True, exist_ok=True)
        api.run(str(api.core_binary()), '-t', '-d', str(api.CORE_HOME), '-f', str(path), check=True)
        api.run('systemctl', 'enable', '--now', f'{PREFIX}{instance}.service', check=True)
        if api.shutil.which('ufw') and api.run('ufw', 'status').stdout.startswith('Status: active'):
            api.run('ufw', 'allow', f'{port}/tcp', check=True)
        return credential
    finally:
        api.port_allocation.release(owner)


def remove(api, credential):
    instance = identity(credential.get('instance_id'))
    api.run('systemctl', 'disable', '--now', f'{PREFIX}{instance}.service', check=True)
    (api.CONFIG_ROOT / 'snell' / f'{instance}.json').unlink(missing_ok=True)
    api.port_allocation.release(f'mihomo:snell:{instance}')
    if credential.get('port') and api.shutil.which('ufw'):
        api.run('ufw', 'delete', 'allow', f"{int(credential['port'])}/tcp")


def stats(api, credential):
    instance = identity(credential.get('instance_id'))
    unit = f'{PREFIX}{instance}.service'
    result = api.run('curl', '--silent', '--show-error', '--max-time', '2', '--unix-socket',
                     f'/run/vps-control-mihomo-snell/{instance}/controller.sock', 'http://localhost/connections')
    unknown = {'active': None, 'rx_bytes': None, 'tx_bytes': None, 'stats_available': False,
               'activity_available': False, 'service_active': api.systemctl_active(unit)}
    if result.returncode: return unknown
    try:
        value = json.loads(result.stdout)
        upload, download = value['uploadTotal'], value['downloadTotal']
        if any(type(count) is not int or count < 0 for count in (upload, download)) or not isinstance(value.get('connections'), list):
            return unknown
        epoch = api.run('systemctl', 'show', unit, '--property=InvocationID', '--value')
        if epoch.returncode or not re.fullmatch(r'[a-f0-9]{32}', epoch.stdout.strip()): return unknown
        return {**unknown, 'active': bool(value['connections']), 'rx_bytes': upload, 'tx_bytes': download,
                'counter_epoch': epoch.stdout.strip(), 'stats_available': True, 'activity_available': True,
                'active_connections': len(value['connections']), 'activity_source': 'mihomo_connections',
                'traffic_observed_at': time.time()}
    except (ValueError, KeyError, TypeError): return unknown


def render(credential, name, server):
    try: configuration(identity(credential.get('instance_id', 'export')), credential)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    q = lambda value: json.dumps(value, ensure_ascii=False)
    return [f'  - name: {q(name)}', '    type: snell', f'    server: {q(server)}',
            f"    port: {int(credential['port'])}", f"    psk: {q(credential['password'])}",
            '    version: 6', f"    mode: {q(credential.get('mode', 'default'))}",
            '    udp: ' + str(bool(credential.get('udp', True))).lower(),
            '    reuse: ' + str(bool(credential.get('reuse', False))).lower()]


def main():
    """Resolve the selected core on every service start, including upgrades."""
    import os
    import sys
    from pathlib import Path
    if len(sys.argv) != 3 or sys.argv[1] != 'serve':
        raise SystemExit('Expected: serve INSTANCE')
    instance = identity(sys.argv[2])
    runtime = Path('/var/lib/vps-control/mihomo/bin/mihomo')
    core = str(runtime if runtime.is_file() else Path('/opt/vps-control/api/bin/mihomo'))
    os.execv(core, [core, '-d', f'/var/lib/vps-control/mihomo/snell/{instance}',
                    '-f', f'/etc/vps-control/mihomo/snell/{instance}.json'])


if __name__ == '__main__':
    main()
