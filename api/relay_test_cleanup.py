"""Standalone idempotent cleanup, copied outside the replaceable application tree."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import shutil


def atomic(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.test-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o600); os.replace(name, path)
    finally: Path(name).unlink(missing_ok=True)


def firewall_rule(record):
    identity = record['id']
    address = ipaddress.ip_address(record['source'])
    if not re.fullmatch(r'[0-9a-f]{32}', identity) or address.version != 4 or not address.is_global:
        raise ValueError('Invalid test firewall record')
    port = record['port']
    if type(port) is not int or not 1024 <= port <= 65535: raise ValueError('Invalid test port')
    return ['INPUT', '-p', 'udp', '-s', str(address), '--dport', str(port), '-m', 'comment', '--comment', 'vps-control-relay-test:' + identity, '-j', 'ACCEPT']


def peer_current(record):
    tool, interface, public = record['tool'], record['interface'], record['public_key']
    if tool not in ('wg', 'awg') or not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', interface) or not re.fullmatch(r'[a-zA-Z0-9+/]{43}=', public):
        raise ValueError('Invalid test peer record')
    ipaddress.IPv4Interface(record['allowed_ip'])
    result = subprocess.run([tool, 'show', interface, 'allowed-ips'], capture_output=True, text=True, timeout=5)
    if result.returncode:
        interfaces = subprocess.run([tool, 'show', 'interfaces'], capture_output=True, text=True, timeout=5, check=True).stdout.split()
        if interface not in interfaces: return None
        raise OSError('Cannot inspect peer')
    return next((row.split(maxsplit=1)[1].strip() for row in result.stdout.splitlines() if row.split() and row.split()[0] == public), None)


def cleanup(path):
    path = Path(path)
    if not path.exists(): return True
    record = json.loads(path.read_text())
    errors = []
    if record.get('peer') and not record.get('peer_removed'):
        try:
            peer = record['peer']; current = peer_current(peer)
            if current is not None and current != peer['allowed_ip']: raise ValueError('Peer reservation changed')
            if current is not None:
                subprocess.run([peer['tool'], 'set', peer['interface'], 'peer', peer['public_key'], 'remove'], check=True, capture_output=True, timeout=5)
            record['peer_removed'] = True
        except (OSError, ValueError, subprocess.SubprocessError): errors.append('peer_cleanup_failed')
    if record.get('firewall') and not record.get('firewall_removed'):
        try:
            rule = firewall_rule(record['firewall'])
            result = subprocess.run(['iptables', '-w', '5', '-C', *rule], capture_output=True, timeout=8)
            if result.returncode == 0:
                subprocess.run(['iptables', '-w', '5', '-D', *rule], check=True, capture_output=True, timeout=8)
            elif result.returncode != 1: raise OSError('Cannot inspect firewall rule')
            record['firewall_removed'] = True
        except (OSError, ValueError, subprocess.SubprocessError): errors.append('firewall_cleanup_failed')
    # Credentials and scoped upload token are never retained in completed test history.
    for name in ('private.json', 'kit.zip', 'request.json', 'report.json'):
        try: (path.parent / name).unlink(missing_ok=True)
        except OSError: errors.append('private_cleanup_failed')
    runtime = path.parent/'runtime'
    try:
        if runtime.is_symlink(): runtime.unlink()
        elif runtime.exists():
            if runtime.resolve().parent != path.parent.resolve(): raise OSError('Unexpected runtime directory')
            shutil.rmtree(runtime)
    except OSError: errors.append('private_cleanup_failed')
    record['cleanup_errors'] = errors
    record['cleaned'] = not errors
    atomic(path, record)
    return not errors


if __name__ == '__main__':
    import sys
    if os.geteuid() != 0: raise SystemExit('Test cleanup requires root')
    raise SystemExit(0 if cleanup(sys.argv[1]) else 1)
