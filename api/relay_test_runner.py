"""Standalone client-side media and UDP runner. Python stdlib + curl + a supplied core."""
import argparse
import copy
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import tempfile
from types import SimpleNamespace

MEDIA_URL = 'https://media.w3.org/2010/05/sintel/trailer.mp4'
MEDIA_SIZE = 4372373
MEDIA_SHA256 = 'b670602fa00934ca27c4351bb0efe7ea7a07fae57284e44226025eeed7c51254'

def client_config(plan, port):
    node = copy.deepcopy(plan.client)
    if plan.engine == 'mihomo':
        node['name'] = 'relay-media'
        return {**({'interface-name': plan.interface} if getattr(plan, 'interface', '') else {}), 'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False, 'mode': 'rule', 'log-level': 'silent',
                'proxies': [node], 'rules': ['MATCH,relay-media'], 'dns': {'enable': True, 'nameserver': ['https://1.1.1.1/dns-query'], 'proxy-server-nameserver': ['https://1.1.1.1/dns-query']}}
    node['tag'] = 'relay-media'
    if plan.engine == 'singbox':
        return {'log': {'disabled': True}, 'inbounds': [{'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': port}],
                'outbounds': [node], 'route': {'final': 'relay-media'}}
    return {'log': {'loglevel': 'none'}, 'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'auth': 'noauth'}}],
            'outbounds': [node], 'routing': {'rules': [{'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'relay-media'}]}}


def transfer(port, path, *, byte_range=None):
    command = ['curl', '--silent', '--show-error', '--fail', '--noproxy', '', '--proto', '=https',
               '--socks5-hostname', f'127.0.0.1:{port}', '--connect-timeout', '8', '--max-time', '25',
               '--max-filesize', str(MEDIA_SIZE), '--limit-rate', '524288', '--output', str(path)]
    if byte_range is not None:
        command += ['--range', f'{byte_range[0]}-{byte_range[1]}']
    result = subprocess.run([*command, MEDIA_URL], capture_output=True, timeout=30)
    if result.returncode:
        raise TimeoutError() if result.returncode == 28 else OSError('Media transport failed')
    return path.read_bytes()


def verify_content(body):
    return len(body) == MEDIA_SIZE and hashlib.sha256(body).hexdigest() == MEDIA_SHA256


def stop_client(process):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def measurement_packet(identity, key, number):
    body = b'RLY2' + bytes.fromhex(identity) + struct.pack('!H', number)
    body += bytes(1200 - 32 - len(body))
    return body + hmac.digest(bytes.fromhex(key), body, 'sha256')


def measurement_sequence(data, identity, key):
    if len(data) != 1200 or data[:20] != b'RLY2' + bytes.fromhex(identity): return None
    if not hmac.compare_digest(data[-32:], hmac.digest(bytes.fromhex(key), data[:-32], 'sha256')): return None
    number = struct.unpack('!H', data[20:22])[0]
    return number if number < 100 else None


def interface_index(name):
    try: return socket.if_nametoindex(name)
    except OSError:
        if os.name != 'nt': raise
        # Python exposes Windows' internal names; Mihomo uses friendly aliases such as Ethernet.
        import ctypes
        library = ctypes.WinDLL('iphlpapi')
        luid, index = ctypes.c_uint64(), ctypes.c_uint32()
        alias = library.ConvertInterfaceAliasToLuid
        alias.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint64)]; alias.restype = ctypes.c_uint32
        convert = library.ConvertInterfaceLuidToIndex
        convert.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint32)]; convert.restype = ctypes.c_uint32
        if alias(name, ctypes.byref(luid)) or convert(ctypes.byref(luid), ctypes.byref(index)):
            raise OSError('Physical interface unavailable')
        return index.value


def udp_measurement(settings):
    """100 unique authenticated packets, no retransmits; round-trip samples at sender."""
    received, sent_at, rtt = set(), {}, []
    try:
        addresses = socket.getaddrinfo(settings['address'], settings['port'], type=socket.SOCK_DGRAM)
        family, _, _, _, address = addresses[0]
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            if settings.get('interface'):
                if os.name == 'nt': sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', interface_index(settings['interface'])))
                else: sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, settings['interface'].encode() + b'\0')
            sock.connect(address); sock.setblocking(False)
            started = time.monotonic(); number = 0
            deadline = started + 5
            while time.monotonic() < deadline:
                now = time.monotonic()
                if number < 100 and now >= started + number * 0.02:
                    sock.send(measurement_packet(settings['id'], settings['key'], number))
                    sent_at[number] = time.monotonic(); number += 1
                try:
                    data = sock.recv(2048)
                    found = measurement_sequence(data, settings['id'], settings['key'])
                    if found in sent_at and found not in received:
                        received.add(found); rtt.append((time.monotonic() - sent_at[found]) * 1000)
                except BlockingIOError: pass
                if len(received) == 100: break
                time.sleep(0.002)
        return {'state': 'completed', 'sent_sequences': sorted(sent_at), 'received_sequences': sorted(received),
                'rtt_ms': round(sum(rtt) / len(rtt), 2) if rtt else None}
    except (OSError, ValueError, KeyError):
        return {'state': 'unavailable', 'sent_sequences': sorted(sent_at), 'received_sequences': sorted(received), 'rtt_ms': None}


def main():
    parser = argparse.ArgumentParser(description='Test one Relay connection without changing system VPN or routes')
    parser.add_argument('--core', required=True, help='Path to Mihomo / Xray / sing-box matching the kit engine')
    parser.add_argument('--config', default=str(Path(__file__).with_name('test.json')))
    parser.add_argument('--output', default=str(Path(__file__).with_name('result.json')))
    parser.add_argument('--interface', default='', help='Physical interface, e.g. Ethernet; isolates Mihomo and UDP from another active VPN')
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    if time.time() > settings['expires_at']: raise SystemExit('Test expired; create a new kit in PRO')
    if args.interface and settings['engine'] != 'mihomo': raise SystemExit('--interface requires a Mihomo kit')
    plan = SimpleNamespace(engine=settings['engine'], client=settings['client'], interface=args.interface)
    settings['measurement']['interface'] = args.interface
    result = {'id': settings['id'], 'token': settings['token'], 'media': execute(plan, str(Path(args.core).resolve())),
              'measurement': udp_measurement(settings['measurement'])}
    path = Path(args.output)
    path.write_text(json.dumps(result), encoding='utf-8'); os.chmod(path, 0o600)
    print('Test finished. Upload result.json in PRO / ROUTES. Media:', result['media']['state'])


def execute(plan, binary, *, work_root=None):
    started = time.monotonic()
    process = None
    try:
        if not Path(binary).is_file():
            return {'state': 'unknown', 'reason': 'client_unavailable'}
        with ExitStack() as stack:
            runtime = Path(work_root)/'runtime' if work_root else None
            if runtime: runtime.mkdir(parents=True, mode=0o700, exist_ok=True)
            folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='relay-media-', dir=runtime))
            root = Path(folder)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            path = root / 'client.json'
            path.write_text(json.dumps(client_config(plan, port)), encoding='utf-8')
            os.chmod(path, 0o600)
            args = ['-f', str(path)] if plan.engine == 'mihomo' else ['run', '-c' if plan.engine == 'singbox' else '-config', str(path)]
            if plan.engine == 'mihomo': args += ['-d', str(root)]
            process = subprocess.Popen([binary, *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stack.callback(stop_client, process)
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.15): break
                except OSError: time.sleep(0.05)
            else:
                return {'state': 'unknown', 'reason': 'client_unavailable'}
            body = transfer(port, root / 'video.mp4')
            if not verify_content(body):
                return {'state': 'failed', 'reason': 'content_mismatch'}
            ranges = [(0, 65535), (MEDIA_SIZE // 2, MEDIA_SIZE // 2 + 131071), (MEDIA_SIZE - 131072, MEDIA_SIZE - 1)]
            def seek(index):
                first, last = ranges[index]
                return transfer(port, root / f'range-{index}', byte_range=(first, last)) == body[first:last + 1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                if not all(pool.map(seek, range(3))):
                    return {'state': 'failed', 'reason': 'range_mismatch'}
            return {'state': 'ready', 'reason': 'media_verified', 'verified_bytes': len(body), 'range_checks': 3,
                    'seconds': round(time.monotonic() - started, 2)}
    except (TimeoutError, subprocess.TimeoutExpired):
        return {'state': 'failed', 'reason': 'timeout'}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'failed' if process else 'unknown', 'reason': 'transport_error' if process else 'client_unavailable'}


if __name__ == '__main__': main()
