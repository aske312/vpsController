"""Opt-in root/Linux test: two temporary netns, encrypted ping, automatic cleanup.

Usage: python3 tests/linux_tunnel_smoke.py awg  (or wg)
Does not modify the panel, its peers, firewall, or existing interfaces.
"""
import os
import shutil
import subprocess
import sys
import uuid


def run(*args, data=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def main():
    tool = sys.argv[1]
    assert tool in ('wg', 'awg') and sys.platform == 'linux' and os.geteuid() == 0
    assert shutil.which(tool) and shutil.which('ping')
    prefix = 'vpqa' + uuid.uuid4().hex[:6]
    names = [prefix + 's', prefix + 'c']
    created = []
    try:
        for name in names:
            run('ip', 'netns', 'add', name)
            created.append(name)
        run('ip', '-n', names[0], 'link', 'add', 'underlay', 'type', 'veth', 'peer', 'name', 'underlay', 'netns', names[1])
        private = [run(tool, 'genkey') for _ in names]
        public = [run(tool, 'pubkey', data=key + '\n') for key in private]
        extra = 'Jc = 6\nJmin = 8\nJmax = 80\nS1 = 64\nS2 = 112\nH1 = 150000000\nH2 = 600000000\nH3 = 1000000000\nH4 = 1400000000\n' if tool == 'awg' else ''
        for index, name in enumerate(names):
            peer = 1 - index
            run('ip', '-n', name, 'link', 'set', 'lo', 'up')
            run('ip', '-n', name, 'address', 'add', f'192.0.2.{index + 1}/30', 'dev', 'underlay')
            run('ip', '-n', name, 'link', 'set', 'underlay', 'up')
            run('ip', '-n', name, 'link', 'add', 'tunnel', 'type', 'amneziawg' if tool == 'awg' else 'wireguard')
            config = f'[Interface]\nPrivateKey = {private[index]}\nListenPort = 51999\n{extra}[Peer]\nPublicKey = {public[peer]}\nAllowedIPs = 198.18.0.{peer + 1}/32\nEndpoint = 192.0.2.{peer + 1}:51999\n'
            run('ip', 'netns', 'exec', name, tool, 'setconf', 'tunnel', '/dev/stdin', data=config)
            run('ip', '-n', name, 'address', 'add', f'198.18.0.{index + 1}/30', 'dev', 'tunnel')
            run('ip', '-n', name, 'link', 'set', 'tunnel', 'mtu', '1280', 'up')
        run('ip', 'netns', 'exec', names[1], 'ping', '-c', '3', '-W', '3', '198.18.0.1')
        for name in names:
            handshake = run('ip', 'netns', 'exec', name, tool, 'show', 'tunnel', 'latest-handshakes')
            assert int(handshake.split()[1]) > 0
        print(f'{tool}: encrypted ping and handshake passed in isolated network namespaces')
    finally:
        for name in reversed(created):
            run('ip', 'netns', 'delete', name)


if __name__ == '__main__':
    main()
