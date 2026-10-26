"""Real WG/AWG credential creation, traffic and revocation in disposable netns.

Run: sudo unshare --net python tests/kernel_tunnels_smoke.py
Requires both wg/awg tools and their kernel modules; never uses host interfaces.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "api"),
                str(Path(__file__).resolve().parents[1] / "protocol-images/mihomo")]


def run(*args, input=None, check=True):
    return subprocess.run(args, input=input, check=check, capture_output=True, text=True, timeout=15)


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        raise SystemExit('Requires root inside a disposable network namespace')
    import manager
    child = subprocess.Popen(['unshare', '--net', 'sleep', '180'])
    try:
        for _ in range(100):
            if os.readlink(f'/proc/{child.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                break
            time.sleep(.02)
        else:
            raise RuntimeError('Client namespace did not start')
        def client(*args, **kwargs):
            return run('nsenter', '-t', str(child.pid), '-n', *args, **kwargs)
        run('ip', 'link', 'set', 'lo', 'up')
        client('ip', 'link', 'set', 'lo', 'up')
        run('ip', 'link', 'add', 'audit-origin', 'type', 'veth', 'peer', 'name', 'audit-client')
        run('ip', 'link', 'set', 'audit-client', 'netns', str(child.pid))
        run('ip', 'addr', 'add', '198.18.39.1/30', 'dev', 'audit-origin')
        client('ip', 'addr', 'add', '198.18.39.2/30', 'dev', 'audit-client')
        run('ip', 'link', 'set', 'audit-origin', 'up')
        client('ip', 'link', 'set', 'audit-client', 'up')
        with tempfile.TemporaryDirectory(prefix='kernel-tunnel-audit-') as directory:
            root = Path(directory)
            for tool, kind, interface in [('wg', 'wireguard', 'mh-wg0'), ('awg', 'amneziawg', 'mh-awg0')]:
                module = 'transport-' + tool
                settings = {'port': 51891, 'subnet': '10.85.0.0/24', 'mtu': 1280,
                            'jc': 6, 'jmin': 8, 'jmax': 80, 's1': 64, 's2': 112,
                            'h1': 150000000, 'h2': 600000000, 'h3': 1000000000, 'h4': 1400000000}
                extras = ''.join(f'{key.capitalize()} = {settings[key]}\n' for key in
                                 ('jc', 'jmin', 'jmax', 's1', 's2', 'h1', 'h2', 'h3', 'h4')) if tool == 'awg' else ''
                server_config = root / f'{tool}-server.conf'
                server_config.write_text('[Interface]\nPrivateKey = ' + run(tool, 'genkey').stdout.strip() + '\nListenPort = 51891\n' + extras)
                server_config.chmod(0o600)
                run('ip', 'link', 'add', interface, 'type', kind)
                client('ip', 'link', 'add', 'audit-tunnel', 'type', kind)
                try:
                    run(tool, 'setconf', interface, str(server_config))
                    run('ip', 'addr', 'add', '10.85.0.1/24', 'dev', interface)
                    run('ip', 'link', 'set', interface, 'mtu', '1280', 'up')
                    with ExitStack() as context:
                        context.enter_context(patch.object(manager, 'WG_CONFIG_BY_MODULE', {module: server_config}))
                        context.enter_context(patch.object(manager, 'module_settings', return_value=settings))
                        context.enter_context(patch.object(manager, 'next_tunnel_address', return_value=('10.85.0.2', None)))
                        credential = manager.add_wg_credential('audit', module, 'fixture')
                        config = root / f'{tool}-client.conf'
                        config.write_text('[Interface]\nPrivateKey = ' + credential['private_key'] + '\n' + extras +
                                          '[Peer]\nPublicKey = ' + credential['server_public_key'] +
                                          '\nAllowedIPs = 10.85.0.1/32\nEndpoint = 198.18.39.1:51891\nPersistentKeepalive = 1\n')
                        config.chmod(0o600)
                        client(tool, 'setconf', 'audit-tunnel', str(config))
                        client('ip', 'addr', 'add', credential['ip'], 'dev', 'audit-tunnel')
                        client('ip', 'link', 'set', 'audit-tunnel', 'mtu', '1280', 'up')
                        client('ping', '-c', '2', '-W', '3', '10.85.0.1')
                        handshake = run(tool, 'show', interface, 'latest-handshakes').stdout.split()
                        assert int(handshake[-1]) > 0, 'Encrypted handshake was not observed'
                        manager.remove_wg_credential('audit', module, credential)
                        assert client('ping', '-c', '1', '-W', '1', '10.85.0.1', check=False).returncode != 0, 'Revoked peer still passes traffic'
                        assert credential['public_key'] not in server_config.read_text(), 'Revoked peer remains on disk'
                        print(json.dumps({'protocol': tool, 'credential_creation': 'passed', 'encrypted_traffic': 'passed', 'revocation': 'passed'}), flush=True)
                finally:
                    client('ip', 'link', 'del', 'audit-tunnel', check=False)
                    run('ip', 'link', 'del', interface, check=False)
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == '__main__':
    main()
