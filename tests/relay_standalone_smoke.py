"""Linux root smoke test in isolated mount/network/PID namespaces; no host install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen


def run(*command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        print(result.stderr[-2000:], file=sys.stderr)
        result.check_returncode()
    return result


def inside(source, sandbox):
    sandbox.chmod(0o755)
    run('mount', '--make-rprivate', '/')
    for name in ('usr', 'dev', 'etc/ssl'):
        destination = sandbox / name
        destination.mkdir(parents=True, exist_ok=True)
        run('mount', '--bind', '/' + name, str(destination))
        run('mount', '-o', 'remount,bind,ro', str(destination))
    local = sandbox / 'local'
    local.mkdir()
    run('mount', '--bind', str(local), str(sandbox / 'usr/local'))
    for name in ('bin', 'sbin', 'lib', 'lib64'):
        if Path('/' + name).exists(): (sandbox / name).symlink_to('usr/' + name)
    for name in ('tmp', 'run', 'var/log', 'var/lib', 'etc/systemd/system', 'proc', 'qa/bin'):
        (sandbox / name).mkdir(parents=True, exist_ok=True)
    (sandbox / 'tmp').chmod(0o1777)
    run('mount', '-t', 'proc', 'proc', str(sandbox / 'proc'))
    shutil.copy('/etc/os-release', sandbox / 'etc/os-release')
    for name, value in {'passwd': 'root:x:0:0:root:/root:/bin/bash\n', 'group': 'root:x:0:\n',
                        'shadow': 'root:*:20000:0:99999:7:::\n', 'gshadow': 'root:*::\n'}.items():
        (sandbox / 'etc' / name).write_text(value)
    staged = sandbox / 'qa/source'
    for name in ('install.sh', 'editions.json', 'scripts/install-agent.sh',
                 'protocol-images/relay-agent/install.sh', 'protocol-images/relay-agent/agent.py',
                 'protocol-images/relay-agent/credentials.py', 'protocol-images/relay-agent/uninstall.sh'):
        target = staged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    with tarfile.open(sandbox / 'qa/source.tar.gz', 'w:gz') as archive:
        archive.add(staged, arcname='vpsController-installer')
    # Bootstrap downloads are deterministic fixtures; HTTPS health probes use real curl.
    (sandbox / 'qa/bin/curl').write_text('''#!/usr/bin/env bash
set -eu
case "${*: -1}" in
  https://raw.githubusercontent.com/*/scripts/install-panel.sh)
    while [[ "$1" != --output ]]; do shift; done
    printf '#!/bin/sh\\nprintf "%%s|%%s\\n" "$VPS_CONTROL_BRANCH" "$*" > /qa/panel-route\\n' > "$2" ;;
  https://raw.githubusercontent.com/*/scripts/install-agent.sh)
    while [[ "$1" != --output ]]; do shift; done
    cp /qa/source/scripts/install-agent.sh "$2" ;;
  https://github.com/*/archive/*.tar.gz)
    while [[ "$1" != --output ]]; do shift; done
    cp /qa/source.tar.gz "$2" ;;
  https://api.ipify.org) printf '8.8.8.8' ;;
  *) exec /usr/bin/curl "$@" ;;
esac
''')
    (sandbox / 'qa/bin/apt-get').write_text('#!/bin/sh\necho "Unexpected dependency installation" >&2\nexit 99\n')
    (sandbox / 'qa/bin/ufw').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> /qa/ufw-calls\n')
    (sandbox / 'qa/bin/systemctl').write_text('''#!/usr/bin/python3
import grp, os, pwd, signal, subprocess, sys, time
from pathlib import Path
pid = Path('/run/relay-test.pid')
if sys.argv[1] in ('restart', 'disable'):
    if pid.exists():
        try: os.kill(int(pid.read_text()), signal.SIGTERM)
        except ProcessLookupError: pass
        time.sleep(0.3)
        pid.unlink()
    if sys.argv[1] == 'restart':
        user = pwd.getpwnam('vps-relay')
        def identity(): os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
        with open('/var/log/relay-test.log', 'a') as log:
            process = subprocess.Popen(['/usr/bin/python3', '/usr/local/lib/vps-control-relay-agent/agent.py'],
                stdout=log, stderr=log, preexec_fn=identity, start_new_session=True)
        pid.write_text(str(process.pid))
''')
    for path in (sandbox / 'qa/bin').iterdir(): path.chmod(0o755)
    run('ip', 'link', 'set', 'lo', 'up')
    os.chroot(sandbox)
    os.chdir('/')
    os.environ['PATH'] = '/qa/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    bootstrap = '/qa/source/install.sh'
    for edition in ('light', 'pro'):
        run('bash', bootstrap, '--edition', edition, '--domain', 'panel.example.com')
        assert Path('/qa/panel-route').read_text().strip() == edition + '|--domain panel.example.com'
    bad = subprocess.run(['bash', bootstrap, '--edition', 'agent', '--public-ip', '127.0.0.1'],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and not Path('/etc/vps-control-relay-agent').exists()
    result = run('bash', bootstrap, '--edition', 'agent')
    config_root = Path('/etc/vps-control-relay-agent')
    config = json.loads((config_root / 'config.json').read_text())
    connection = json.loads(run('python3', '/usr/local/lib/vps-control-relay-agent/credentials.py').stdout)
    assert connection['agent_url'] == 'https://8.8.8.8:9443'
    assert connection['token'] in result.stdout
    assert connection['certificate_sha256'] == hashlib.sha256(ssl.PEM_cert_to_DER_cert((config_root / 'server.crt').read_text())).hexdigest()
    assert config['token_sha256'] == hashlib.sha256(connection['token'].encode()).hexdigest()
    firewall = Path('/qa/ufw-calls').read_text()
    assert all(rule in firewall for rule in ('9443/tcp', '20000:20999/tcp', '20000:20999/udp'))
    assert (config_root / 'token').stat().st_mode & 0o777 == 0o600
    assert (config_root / 'server.key').stat().st_mode & 0o777 == 0o640
    context = ssl.create_default_context(cafile=str(config_root / 'server.crt'))
    request = Request('https://127.0.0.1:9443/v1/status', headers={'Authorization': 'Bearer ' + connection['token']})
    with urlopen(request, context=context, timeout=5) as response: assert response.status == 200
    from urllib.error import HTTPError
    try: urlopen('https://127.0.0.1:9443/v1/status', context=context, timeout=5)
    except HTTPError as error: assert error.code == 401
    else: raise AssertionError('Unauthenticated request was accepted')
    route = {'transport': 'tcp', 'listen_port': 20001, 'target_ip': '1.1.1.1', 'target_port': 443}
    create = Request('https://127.0.0.1:9443/v1/routes/smoke', data=json.dumps(route).encode(), method='PUT',
        headers={'Authorization': 'Bearer ' + connection['token'], 'If-Match': '0', 'Content-Type': 'application/json'})
    with urlopen(create, context=context, timeout=5) as response: assert response.status == 200
    before = {name: (config_root / name).read_bytes() for name in ('token', 'server.crt', 'server.key')}
    state = Path('/var/lib/vps-control-relay-agent/routes.json')
    saved_routes = state.read_bytes()
    run('bash', bootstrap, '--edition', 'agent', '--public-ip', '8.8.8.8')
    assert all((config_root / name).read_bytes() == value for name, value in before.items())
    assert state.read_bytes() == saved_routes
    with urlopen(request, context=context, timeout=5) as response: assert json.load(response)['routes'] == 1
    assert not Path('/opt/vps-control').exists()
    assert list(Path('/etc/systemd/system').glob('*.service')) == [Path('/etc/systemd/system/vps-control-relay-agent.service')]
    import pwd
    user = pwd.getpwnam('vps-relay')
    denied = subprocess.run(['python3', '/usr/local/lib/vps-control-relay-agent/credentials.py'],
        capture_output=True, text=True, user=user.pw_uid, group=user.pw_gid, extra_groups=[])
    assert denied.returncode != 0 and connection['token'] not in denied.stdout + denied.stderr
    changed = subprocess.run(['bash', bootstrap, '--edition', 'agent', '--public-ip', '1.1.1.1'], capture_output=True, text=True)
    assert changed.returncode != 0 and all((config_root / name).read_bytes() == value for name, value in before.items())
    run('systemctl', 'disable', 'vps-control-relay-agent.service')
    print(json.dumps({'standalone_bootstrap': True, 'no_panel': True, 'tls_authenticated': True,
        'anonymous_rejected': True, 'root_only_credentials': True, 'reinstall_preserves_identity_and_routes': True,
        'invalid_and_changed_ip_rejected': True, 'secrets_printed_only_on_success': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--inside', type=Path)
    options = parser.parse_args()
    if options.inside:
        inside(options.source.resolve(), options.inside.resolve())
    else:
        if os.geteuid() != 0: raise SystemExit('Run as root; this test uses isolated namespaces')
        with tempfile.TemporaryDirectory(prefix='relay-clean-smoke-') as folder:
            result = subprocess.run(['unshare', '--mount', '--net', '--pid', '--fork',
                'python3', str(Path(__file__).resolve()), str(options.source.resolve()), '--inside', folder],
                capture_output=True, text=True, timeout=90)
            if result.returncode:
                # Never print captured installer output: it can contain a generated token.
                print(result.stderr[-4000:], file=sys.stderr)
                raise SystemExit(result.returncode)
            print(result.stdout.strip())
