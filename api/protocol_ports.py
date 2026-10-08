"""Exclusive TCP/UDP aliases for direct protocols; existing listeners stay intact."""
from contextlib import contextmanager
import argparse
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import secrets
import selectors
import shlex
import signal
import socket

from awg_ports import PortError, command

PROTOCOLS = {'hysteria2': 'udp', 'tuic': 'udp', 'xray': 'tcp'}
MARKER = '# vpsController direct protocol port alias'


def bind_port(port, transport):
    sockets = []
    try:
        for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM if transport == 'udp' else socket.SOCK_STREAM)
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno == errno.EAFNOSUPPORT: continue
                raise
            sockets.append(sock)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'): sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, port))
            if transport == 'tcp': sock.listen(8)
        return sockets
    except Exception:
        for sock in sockets: sock.close()
        raise


def rule_args(protocol, port, target, address):
    ipaddress.ip_address(address)
    return ['-p', PROTOCOLS[protocol], '-d', address, '--dport', str(port), '-m', 'comment',
            '--comment', f'vps-control-{protocol}-port-{port}', '-j', 'REDIRECT', '--to-ports', str(target)]


def nat_conflict(protocol, port, address):
    binary = 'ip6tables' if ':' in address else 'iptables'
    own = f'vps-control-{protocol}-port-{port}'
    for line in command(binary, '-w', '5', '-n', '-t', 'nat', '-S').stdout.splitlines():
        tokens = shlex.split(line)
        if '-p' in tokens and tokens[tokens.index('-p') + 1] not in (PROTOCOLS[protocol], 'all'): continue
        if '--comment' in tokens and tokens[tokens.index('--comment') + 1] == own: continue
        for key in ('--dport', '--dports', '--destination-port'):
            if key not in tokens: continue
            for value in tokens[tokens.index(key) + 1].split(','):
                bounds = value.split(':')
                if all(part.isdigit() for part in bounds) and int(bounds[0]) <= port <= int(bounds[-1]): return True
    return False


def unit_template(protocol, install_dir, state_dir):
    if protocol not in PROTOCOLS: raise PortError('Unknown protocol')
    base = f'vps-control-{protocol}.service'
    return (f'{MARKER}\n[Unit]\nDescription={protocol} {PROTOCOLS[protocol]} alias %i\n'
            f'Requires={base}\nAfter=network-online.target {base}\nPartOf={base}\n'
            '[Service]\nType=notify\n'
            f'ExecStart={json.dumps(str(Path(install_dir)/"venv/bin/python"))} '
            f'{json.dumps(str(Path(install_dir)/"api/protocol_ports.py"))} serve --protocol {protocol} '
            f'--port %i --state-dir {json.dumps(str(state_dir))}\n'
            'Restart=on-failure\nRestartSec=2\nTimeoutStartSec=30\nTimeoutStopSec=15\n'
            '[Install]\nWantedBy=multi-user.target\n')


class ProtocolPorts:
    def __init__(self, protocol, primary, address, state_dir):
        if protocol not in PROTOCOLS or not 1 <= primary <= 65535: raise PortError('Invalid protocol listener')
        if address: ipaddress.ip_address(address)
        self.protocol, self.primary, self.address = protocol, primary, address
        self.state_dir = Path(state_dir)

    def unit(self, port): return f'vps-control-{self.protocol}-port@{port}.service'
    def path(self, port): return self.state_dir/f'{port}.json'
    def content(self, port):
        return json.dumps({'managed_by': 'vpsController', 'protocol': self.protocol, 'port': port, 'target': self.primary, 'address': self.address})
    def owned(self, port): return self.path(port).exists() and self.path(port).read_text() == self.content(port)

    def status(self, port):
        if not self.address: return {'port': port, 'status': 'unavailable', 'detail': 'Публичный IP сервера не настроен'}
        if port == self.primary: return {'port': port, 'status': 'protocol', 'detail': 'Основной порт выбранного профиля'}
        if platform.system() != 'Linux': return {'port': port, 'status': 'unavailable', 'detail': 'Дополнительные порты требуют Linux'}
        if self.owned(port) and command('systemctl', 'is-active', self.unit(port), check=False).returncode == 0:
            return {'port': port, 'status': 'protocol', 'detail': 'Уже используется для этого профиля'}
        if self.path(port).exists() and not self.owned(port):
            return {'port': port, 'status': 'occupied', 'detail': 'Порт закреплён за другим серверным профилем'}
        try:
            sockets = bind_port(port, PROTOCOLS[self.protocol])
            for sock in sockets: sock.close()
            if nat_conflict(self.protocol, port, self.address):
                return {'port': port, 'status': 'occupied', 'detail': 'Есть другое правило переадресации'}
        except (OSError, PortError) as exc:
            return {'port': port, 'status': 'occupied' if getattr(exc, 'errno', None) == errno.EADDRINUSE else 'unavailable',
                    'detail': f'{PROTOCOLS[self.protocol].upper()}-порт занят' if getattr(exc, 'errno', None) == errno.EADDRINUSE else 'Не удалось проверить порт или firewall'}
        return {'port': port, 'status': 'available', 'detail': f'{PROTOCOLS[self.protocol].upper()}-порт свободен'}

    def random_port(self):
        for _ in range(128):
            port = 20000 + secrets.randbelow(45536)
            if port != self.primary and self.status(port)['status'] == 'available': return port
        raise PortError('server_port: не удалось выбрать свободный порт')

    @contextmanager
    def reserve(self, port):
        status = self.status(port)
        if status['status'] not in ('available', 'protocol'): raise PortError(f'server_port {port}: {status["detail"]}')
        created = False
        if port != self.primary:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            if not self.path(port).exists():
                with self.path(port).open('x') as stream: stream.write(self.content(port))
                created = True
            try:
                command('systemctl', 'enable', '--now', self.unit(port))
            except Exception:
                if created: self.release(port)
                raise
        try: yield port
        except Exception:
            if created: self.release(port)
            raise

    def release(self, port):
        if port == self.primary: return
        if not self.owned(port): raise PortError('server_port: отказ удаления чужой конфигурации')
        command('systemctl', 'disable', '--now', self.unit(port))
        self.path(port).unlink()


def serve(manager, port):
    binary = 'ip6tables' if ':' in manager.address else 'iptables'
    args = rule_args(manager.protocol, port, manager.primary, manager.address)
    try: sockets = bind_port(port, PROTOCOLS[manager.protocol])
    except OSError:
        command(binary, '-w', '5', '-t', 'nat', '-D', 'PREROUTING', *args, check=False)
        raise
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    selector, installed = selectors.DefaultSelector(), False
    try:
        if nat_conflict(manager.protocol, port, manager.address): raise PortError('server_port: NAT conflict')
        for sock in sockets:
            sock.setblocking(False); selector.register(sock, selectors.EVENT_READ)
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
            for key, _ in selector.select(timeout=5):
                try:
                    if PROTOCOLS[manager.protocol] == 'tcp':
                        connection, _ = key.fileobj.accept(); connection.close()
                    else: key.fileobj.recv(65535)
                except BlockingIOError: pass
    finally:
        if installed: command(binary, '-w', '5', '-t', 'nat', '-D', 'PREROUTING', *args, check=False)
        selector.close()
        for sock in sockets: sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['template', 'serve', 'release-all'])
    parser.add_argument('--protocol', choices=PROTOCOLS, required=True)
    parser.add_argument('--port', type=int)
    parser.add_argument('--install-dir', default='/opt/vps-control')
    parser.add_argument('--state-dir', required=True)
    options = parser.parse_args()
    if options.action == 'template': print(unit_template(options.protocol, options.install_dir, options.state_dir), end='')
    else:
        paths = sorted(Path(options.state_dir).glob('*.json')) if options.action == 'release-all' else [Path(options.state_dir)/f'{options.port}.json']
        for path in paths:
            state = json.loads(path.read_text())
            if state.get('managed_by') != 'vpsController' or state.get('protocol') != options.protocol or not isinstance(state.get('port'), int) or not 1 <= state['port'] <= 65535 or path.name != f'{state["port"]}.json': parser.error('Invalid alias state')
            manager = ProtocolPorts(options.protocol, state['target'], state['address'], options.state_dir)
            if not manager.owned(state['port']): parser.error('Foreign alias state')
            if options.action == 'serve': serve(manager, state['port'])
            else: manager.release(state['port'])
