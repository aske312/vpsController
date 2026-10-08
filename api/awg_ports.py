"""Reserved UDP aliases for AWG. Kernel NAT forwards traffic, not Python.

Each alias has a notify-type systemd guard: it holds an exclusive UDP socket,
installs only its own tagged REDIRECT rule and restores it after firewall reload.
Existing AWG listen port and peers are never changed.
"""
from contextlib import contextmanager
import argparse
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import selectors
import shlex
import shutil
import signal
import socket
import subprocess

MARKER = '# vpsController AWG UDP alias'
PORT_CHOICES = (39761, 47283, 53147, 58493, 61927, 443)


class PortError(ValueError):
    pass


def command(*args, check=True):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=35)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PortError('AWG UDP port: команда настройки порта недоступна или не завершилась вовремя.') from exc
    if check and result.returncode:
        raise PortError('AWG UDP port: не удалось настроить дополнительный порт; проверьте журнал службы.')
    return result


def bind_port(port):
    sockets = []
    try:
        for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM)
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno == errno.EAFNOSUPPORT:
                    continue
                raise
            sockets.append(sock)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, port))  # no SO_REUSEADDR/PORT: reserve exclusively
        return sockets
    except Exception:
        for sock in sockets: sock.close()
        raise


def rule_args(port, target, address):
    # Restrict redirects to this server, never forwarded packets for other hosts.
    ipaddress.ip_address(address)
    return ['-p', 'udp', '-d', address, '--dport', str(port), '-m', 'comment',
            '--comment', f'vps-control-awg-port-{port}', '-j', 'REDIRECT', '--to-ports', str(target)]


def nat_conflict(port, address):
    binary = 'ip6tables' if ':' in address else 'iptables'
    result = command(binary, '-w', '5', '-t', 'nat', '-S')
    for line in result.stdout.splitlines():
        tokens = shlex.split(line)
        if '-p' in tokens and tokens[tokens.index('-p') + 1] not in ('udp', 'all'): continue
        if '--comment' in tokens and tokens[tokens.index('--comment') + 1] == f'vps-control-awg-port-{port}': continue
        for key in ('--dport', '--dports', '--destination-port'):
            if key not in tokens: continue
            for value in tokens[tokens.index(key) + 1].split(','):
                bounds = value.split(':')
                if all(part.isdigit() for part in bounds):
                    low, high = int(bounds[0]), int(bounds[-1])
                    if low <= port <= high: return True
    return False


def unit_template(install_dir, state_dir, interface):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', interface): raise PortError('Invalid AWG interface')
    executable, script = Path(install_dir)/'venv/bin/python', Path(install_dir)/'api/awg_ports.py'
    return (f'{MARKER}\n[Unit]\nDescription=AWG UDP alias %i\n'
            f'Requires=awg-quick@{interface}.service\nAfter=network-online.target awg-quick@{interface}.service\n'
            f'PartOf=awg-quick@{interface}.service\n[Service]\nType=notify\n'
            f'ExecStart={json.dumps(str(executable))} {json.dumps(str(script))} serve --port %i --state-dir {json.dumps(str(state_dir))}\n'
            'Restart=on-failure\nRestartSec=2\nTimeoutStartSec=30\nTimeoutStopSec=15\n'
            '[Install]\nWantedBy=multi-user.target\n')


class AwgPorts:
    def __init__(self, primary, interface, install_dir, address, state_dir=Path('/var/lib/vps-control/awg-ports')):
        self.primary, self.interface, self.install_dir, self.address = primary, interface, Path(install_dir), address
        self.state_dir = Path(state_dir)

    def unit(self, port):
        return f'vps-control-awg-port@{port}.service'

    def content(self, port):
        return json.dumps({'managed_by': 'vpsController', 'port': port, 'target': self.primary, 'interface': self.interface, 'address': self.address})

    def path(self, port):
        return self.state_dir/f'{port}.json'

    def owned(self, port):
        path = self.path(port)
        return path.exists() and path.read_text() == self.content(port)

    def status(self, port):
        if port == self.primary:
            return {'port': port, 'status': 'awg', 'detail': 'Основной порт AWG'}
        if platform.system() != 'Linux':
            return {'port': port, 'status': 'unavailable', 'detail': 'Дополнительные порты требуют Linux'}
        if self.owned(port) and command('systemctl', 'is-active', self.unit(port), check=False).returncode == 0:
            return {'port': port, 'status': 'awg', 'detail': 'Уже используется для AWG'}
        try:
            sockets = bind_port(port)
            for sock in sockets: sock.close()
            if nat_conflict(port, self.address):
                return {'port': port, 'status': 'occupied', 'detail': 'Есть другое правило переадресации UDP'}
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return {'port': port, 'status': 'occupied', 'detail': 'UDP-порт занят другой службой'}
            return {'port': port, 'status': 'unavailable', 'detail': 'Не удалось проверить UDP-порт или правила firewall'}
        except PortError as exc:
            return {'port': port, 'status': 'unavailable', 'detail': str(exc)}
        path = self.path(port)
        if path.exists() and not self.owned(port):
            return {'port': port, 'status': 'occupied', 'detail': 'Конфликт конфигурации службы порта'}
        return {'port': port, 'status': 'available', 'detail': 'UDP-порт свободен'}

    def random_port(self):
        for _ in range(128):
            port = 20000 + secrets.randbelow(45536)
            if port != self.primary and self.status(port)['status'] == 'available': return port
        raise PortError('AWG UDP port: не удалось выбрать свободный порт.')

    @contextmanager
    def reserve(self, port):
        status = self.status(port)
        if status['status'] not in ('available', 'awg'):
            raise PortError(f'AWG UDP port {port}: {status["detail"]}. Выберите другой порт.')
        started = status['status'] != 'awg'
        created = started and not self.path(port).exists()
        if started:
            path = self.path(port)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                # Exclusive creation prevents replacing a foreign state file.
                with path.open('x', encoding='utf-8') as stream: stream.write(self.content(port))
            try:
                command('systemctl', 'enable', '--now', self.unit(port))
            except Exception:
                if created: self.release(port)
                raise
        try:
            yield port
        except Exception:
            if created: self.release(port)
            raise

    def release(self, port):
        if port == self.primary: return
        if not self.owned(port):
            raise PortError(f'AWG UDP port {port}: невозможно удалить чужую конфигурацию.')
        command('systemctl', 'disable', '--now', self.unit(port))
        self.path(port).unlink()


def serve(port, target, address):
    binary = shutil.which('ip6tables' if ':' in address else 'iptables')
    if not binary: raise PortError('iptables not installed')
    args = rule_args(port, target, address)
    try:
        sockets = bind_port(port)
    except OSError:
        # Remove only an exact stale rule after an unclean stop, so a newly
        # occupied UDP port cannot remain redirected to AWG.
        command(binary, '-w', '5', '-t', 'nat', '-D', 'PREROUTING', *args, check=False)
        raise
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selector = selectors.DefaultSelector()
    for sock in sockets:
        sock.setblocking(False); selector.register(sock, selectors.EVENT_READ)
    installed = False
    try:
        # Recheck after the exclusive bind, before altering any rules.
        if nat_conflict(port, address): raise PortError('UDP NAT conflict')
        while not stopping:
            if command(binary, '-w', '5', '-t', 'nat', '-C', 'PREROUTING', *args, check=False).returncode:
                command(binary, '-w', '5', '-t', 'nat', '-I', 'PREROUTING', '1', *args)
            if not installed:
                installed = True
                notify = os.environ.get('NOTIFY_SOCKET')
                if notify:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
                        notifier.connect('\0' + notify[1:] if notify.startswith('@') else notify)
                        notifier.sendall(b'READY=1')
            # Remote packets bypass these sockets via kernel REDIRECT. Discard
            # unrelated local probes, never proxy or log their contents.
            for key, _ in selector.select(timeout=5):
                try: key.fileobj.recv(65535)
                except BlockingIOError: pass
    finally:
        if installed:
            command(binary, '-w', '5', '-t', 'nat', '-D', 'PREROUTING', *args, check=False)
        selector.close()
        for sock in sockets: sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve', 'template'])
    parser.add_argument('--port', type=int)
    parser.add_argument('--state-dir', default='/var/lib/vps-control/awg-ports')
    parser.add_argument('--install-dir', default='/opt/vps-control')
    parser.add_argument('--interface', default='awg0')
    options = parser.parse_args()
    if options.action == 'template':
        print(unit_template(options.install_dir, options.state_dir, options.interface), end='')
    else:
        if options.port is None or not 1 <= options.port <= 65535: parser.error('Invalid UDP port')
        state = json.loads((Path(options.state_dir)/f'{options.port}.json').read_text())
        if state.get('managed_by') != 'vpsController' or state.get('port') != options.port or not 1 <= state.get('target', 0) <= 65535: parser.error('Invalid AWG port state')
        serve(options.port, state['target'], state['address'])
