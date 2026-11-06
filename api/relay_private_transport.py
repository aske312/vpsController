"""Managed opt-in encrypted Relay links. Generation IDs identify configuration, not flows.

An uncertain remote mutation is reconciled by exact ID, never replayed. A generation
becomes exportable only after an authenticated TCP and UDP round trip over both links.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import threading
import time
import uuid

from application_operation import atomic_json
from relay_client import AgentAPIError
import port_allocation
import relay_privacy as privacy
import re


MARKER = '# Managed by vps-control: encrypted Relay transport\n'


def root(control):
    return control.root/'private'


def record(control, host_id):
    if not isinstance(host_id, str) or not re.fullmatch('[0-9a-f]{32}', host_id):
        raise ValueError('Некорректный Relay')
    path = root(control)/(host_id+'.json')
    return json.loads(path.read_text()) if path.exists() else None


def save(control, host_id, value):
    root(control).mkdir(parents=True, mode=0o700, exist_ok=True)
    atomic_json(root(control)/(host_id+'.json'), value)


def observation(client, generation, profile=None):
    value = client.request('GET', '/v1/private-transports/'+generation)
    if value.get('generation') != generation or value.get('state') not in ('preparing', 'listening', 'failed', 'absent'):
        raise ValueError('Protected transport observation is not confirmed')
    if profile and value['state'] != 'absent' and (value.get('transport') != 'hysteria2' or value.get('listen_port') != profile['listen_port']):
        raise ValueError('Protected transport listener is not confirmed')
    return value


def annotate(control, hosts):
    for host in hosts:
        try:
            value = record(control, host['id'])
            if value:
                host['private_transport'] = {key: value[key] for key in ('state', 'generation', 'verified_at') if key in value}
        except (OSError, ValueError, KeyError):
            host['private_transport'] = {'state': 'unknown'}


def active(control, host_id):
    value = record(control, host_id)
    if not value or value['state'] == 'disabled': return None
    if value['state'] != 'ready':
        raise ValueError('Защищённый Relay ещё не проверен; открытая пересылка не подставляется')
    return privacy.validate(json.loads((root(control)/value['generation']/'request.json').read_text()))


def command(args, *, timeout=30):
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                            env={**os.environ, 'GOMAXPROCS': '2'}, check=False)
    if result.returncode:
        raise RuntimeError('Protected transport service command failed')


def certificate(folder, name, address):
    command(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
             '-nodes', '-days', '3650', '-subj', '/CN=endpoint', '-addext', 'subjectAltName=IP:'+address,
             '-keyout', str(folder/(name+'.key')), '-out', str(folder/(name+'.crt'))])
    for suffix in ('.key', '.crt'): (folder/(name+suffix)).chmod(0o600)


def runtime():
    for path in (Path('/usr/local/bin/sing-box'), Path('/usr/local/lib/vps-control-tuic/sing-box')):
        if path.is_file():
            result = subprocess.run([str(path), 'version'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.splitlines()[0] == 'sing-box version '+privacy.RUNTIME_VERSION:
                return path
    raise ValueError('Для защищённого Relay требуется установленный sing-box '+privacy.RUNTIME_VERSION)


def unit_name(generation):
    return 'vps-control-relay-private-'+uuid.UUID(hex=generation).hex+'.service'


def install_origin(folder, profile, binary):
    unit = unit_name(profile['generation'])
    path = Path('/etc/systemd/system')/unit
    if path.exists() and not path.read_text().startswith(MARKER):
        raise ValueError('Имя службы защищённого транспорта занято')
    credentials = Path('/run/credentials')/unit
    # Exact generation-scoped rule; a native restart removes only its own rule.
    # systemd's '+' runs these two firewall commands as root, not DynamicUser.
    # No persistent/global firewall setting is changed.
    firewall = ('INPUT -p udp -s '+profile['relay_ip']+' -d '+profile['origin_ip']+
                ' --dport '+str(profile['origin_port'])+' -m comment --comment vps-control-relay-private:'+
                profile['generation']+' -j ACCEPT')
    atomic_json(folder/'origin.json', privacy.render_origin(profile, credentials))
    # Parse the same config with real certificate paths before starting a unit.
    atomic_json(folder/'check.json', privacy.render_origin(profile, folder))
    command([str(binary), 'check', '-c', str(folder/'check.json')])
    path.write_text(MARKER+f'''[Unit]
Description=Encrypted Relay backhaul
After=network-online.target
Wants=network-online.target
[Service]
DynamicUser=yes
LoadCredential=config.json:{folder}/origin.json
LoadCredential=origin.crt:{folder}/origin.crt
LoadCredential=origin.key:{folder}/origin.key
ExecStartPre=-+/usr/sbin/iptables -w 5 -D {firewall}
ExecStartPre=+/usr/sbin/iptables -w 5 -I {firewall}
ExecStart={binary} run -c %d/config.json
ExecStopPost=-+/usr/sbin/iptables -w 5 -D {firewall}
Environment=GOMAXPROCS=2
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
CapabilityBoundingSet=
MemoryMax=256M
TasksMax=64
StandardOutput=null
StandardError=null
[Install]
WantedBy=multi-user.target
''')
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'enable', '--now', unit])


def receive(sock, size):
    result = bytearray()
    while len(result) < size:
        part = sock.recv(size-len(result))
        if not part: raise RuntimeError('Incomplete protected round trip')
        result.extend(part)
    return bytes(result)


@contextmanager
def nonce_peer(address, port):
    """Only the unpredictable per-test token is echoed; no public reflection service."""
    token = secrets.token_bytes(32)
    tcp, udp = socket.socket(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    stop = threading.Event()
    threads = []
    try:
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((address, port));tcp.listen(2);tcp.settimeout(.2)
        udp.bind((address, port));udp.settimeout(.2)
        def stream():
            while not stop.is_set():
                try: peer, _ = tcp.accept()
                except socket.timeout: continue
                except OSError: return
                with peer:
                    peer.settimeout(3)
                    try:
                        if receive(peer, 32) == token:
                            # Deterministic authenticated synthetic response, never user data.
                            peer.sendall(token*32768)
                    except OSError: pass
                    except RuntimeError: pass
        def datagram():
            while not stop.is_set():
                try: data, address = udp.recvfrom(8192)
                except socket.timeout: continue
                except OSError: return
                if data.startswith(token): udp.sendto(data, address)
        threads = [threading.Thread(target=stream, daemon=True), threading.Thread(target=datagram, daemon=True)]
        for thread in threads: thread.start()
        yield token
    finally:
        stop.set();tcp.close();udp.close()
        for thread in threads: thread.join(timeout=4)


def probe(folder, profile, binary, peer_port):
    """Private local SOCKS listener, exact-byte TCP and fragmented UDP verification."""
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0));local_port = reservation.getsockname()[1]
    outgoing = privacy.render_relay(profile, folder)['outbounds'][0]
    outgoing.update(tag='edge', server=profile['relay_ip'], server_port=profile['listen_port'], password=profile['ingress_password'])
    outgoing.pop('inet4_bind_address', None)
    outgoing['tls'].update(server_name=profile['relay_ip'], certificate_path=str(folder/'ingress.crt'))
    config = {'log': {'disabled': True}, 'inbounds': [{'type': 'socks', 'listen': '127.0.0.1', 'listen_port': local_port}],
              'outbounds': [outgoing], 'route': {'final': 'edge'}}
    atomic_json(folder/'probe.json', config)
    process = subprocess.Popen([str(binary), 'run', '-c', str(folder/'probe.json')],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, 'GOMAXPROCS': '2'})
    def connect(kind):
        sock = socket.create_connection(('127.0.0.1', local_port), timeout=15)
        try:
            sock.sendall(b'\x05\x01\x00')
            if receive(sock, 2) != b'\x05\x00': raise RuntimeError('SOCKS authentication rejected')
            sock.sendall(bytes([5, kind, 0, 1])+socket.inet_aton(profile['origin_ip'])+struct.pack('!H', peer_port))
            reply = receive(sock, 4)
            if reply[:2] != b'\x05\x00': raise RuntimeError('Protected connection rejected')
            if reply[3] == 1: address = socket.inet_ntoa(receive(sock, 4))
            elif reply[3] == 4: address = socket.inet_ntop(socket.AF_INET6, receive(sock, 16))
            else: raise RuntimeError('Unexpected SOCKS endpoint')
            port = struct.unpack('!H', receive(sock, 2))[0]
            return sock, (address, port)
        except BaseException: sock.close();raise
    try:
        deadline = time.monotonic()+10
        while True:
            if process.poll() is not None: raise RuntimeError('Protected probe runtime stopped')
            try:
                with socket.create_connection(('127.0.0.1', local_port), timeout=.1): break
            except OSError:
                if time.monotonic() >= deadline: raise RuntimeError('Protected probe listener unavailable') from None
                time.sleep(.1)
        with nonce_peer(profile['origin_ip'], peer_port) as token:
            sock, _ = connect(1)
            with sock:
                sock.sendall(token)
                if receive(sock, 1048576) != token*32768: raise RuntimeError('Protected TCP content mismatch')
            control, endpoint = connect(3)
            with control, socket.socket(socket.AF_INET6 if ':' in endpoint[0] else socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.settimeout(15)
                header = b'\x00\x00\x00\x01'+socket.inet_aton(profile['origin_ip'])+struct.pack('!H', peer_port)
                for size in (1200, 4096):
                    data = token+secrets.token_bytes(size-32)
                    udp.sendto(header+data, endpoint)
                    received, _ = udp.recvfrom(8192)
                    if received != header+data: raise RuntimeError('Protected UDP content mismatch')
    finally:
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        (folder/'probe.json').unlink(missing_ok=True)


def perform(api, payload, identity):
    from relay_manager import route_snapshot, status_snapshot
    control = api.relay_control()
    registry = control.checked(payload['expected_revision']);control.require_clear()
    host_id = payload['host_id'];host = control.host(registry, host_id);client = control.client(host)
    previous = record(control, host_id)
    if not payload['enabled']:
        if not previous or previous['state'] == 'disabled': return
        generation = previous['generation']
        # Stop exports first. Previously imported nodes remain fail-closed.
        previous['state'] = 'disabling';save(control, host_id, previous)
        control.write(registry)
        try: absent = observation(client, generation)['state'] == 'absent'
        except AgentAPIError as exc:
            if exc.status != 404: raise
            absent = False  # A tombstone must still fence a delayed create.
        if not absent:
            snapshot = route_snapshot(client.request('GET', '/v1/routes'))
            try:
                result = client.request('DELETE', '/v1/private-transports/'+generation, revision=snapshot['revision'])
                if result.get('generation') != generation or result.get('state') != 'absent':
                    raise ValueError('Unexpected retirement acknowledgement')
            except Exception:
                if observation(client, generation)['state'] != 'absent':
                    raise ValueError('Удаление защищённого Relay не подтверждено') from None
        unit = unit_name(generation);path = Path('/etc/systemd/system')/unit
        if path.exists():
            if not path.read_text().startswith(MARKER): raise ValueError('Служба Relay не принадлежит PRO')
            command(['systemctl', 'disable', '--now', unit]);path.unlink();command(['systemctl', 'daemon-reload'])
        port_allocation.release_prefix('relay-private:'+generation+':')
        # Keep no retired link credentials on disk.
        folder = root(control)/generation
        for path in folder.iterdir() if folder.exists() else ():
            if path.is_file(): path.unlink()
        if folder.exists(): folder.rmdir()
        save(control, host_id, {'state': 'disabled', 'generation': generation})
        return
    if previous and previous['state'] != 'disabled':
        if previous['state'] == 'disabling': raise ValueError('Сначала завершите отключение защищённого Relay')
        generation = previous['generation'];folder = root(control)/generation
        previous['state'] = 'preparing';save(control, host_id, previous)
        control.write(registry)
        try:
            profile = privacy.validate(json.loads((folder/'request.json').read_text()))
            # Recheck is read-only on the Agent: never resend an unknown PUT.
            observed = observation(client, generation, profile)
            if observed.get('state') != 'listening': raise ValueError('Protected transport unavailable')
            probe(folder, profile, runtime(), previous['peer_port'])
        except Exception:
            previous['state'] = 'unknown';save(control, host_id, previous)
            raise ValueError('Проверка защищённого Relay не завершена; открытая пересылка не подставляется') from None
        previous.update(state='ready', verified_at=int(time.time()));save(control, host_id, previous)
        return
    status = status_snapshot(client.request('GET', '/v1/status'))
    if 'private-transport-v1' not in status.get('capabilities', []): raise ValueError('Обновите Agent для защищённого транспорта')
    approved = privacy.targets([{'ip': row['target_ip'], 'port': row['target_port'], 'network': row['transport']}
                               for row in registry['routes'] if row['host_id'] == host_id])
    binary = runtime();snapshot = route_snapshot(client.request('GET', '/v1/routes'))
    busy = set(snapshot.get('private_ingress_ports', [])) | set(snapshot.get('reserved_udp_ports', [])) | {
        row['listen_port'] for row in snapshot['items'] if row['transport'] == 'udp'}
    ingress = next((port for port in range(20000, 21000) if port not in busy), None)
    if ingress is None: raise ValueError('На Relay нет свободного UDP-порта')
    generation = uuid.UUID(identity).hex;folder = root(control)/generation
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    # Intent is durable before port allocation, native processes or remote writes.
    intent = {'state': 'preparing', 'generation': generation}
    save(control, host_id, intent)
    control.write(registry)
    try:
        backhaul = port_allocation.allocate('relay-private:'+generation+':backhaul', 19000, {'udp'}, count=1000)
        peer = port_allocation.allocate('relay-private:'+generation+':probe', 34000, {'tcp', 'udp'}, count=1000)
        certificate(folder, 'ingress', client.host);certificate(folder, 'origin', control.origin)
        profile = privacy.validate({'schema': 1, 'transport': 'hysteria2', 'generation': generation,
            'relay_ip': client.host, 'listen_port': ingress, 'origin_ip': control.origin, 'origin_port': backhaul,
            'ingress_password': secrets.token_urlsafe(32), 'origin_password': secrets.token_urlsafe(32),
            'ingress_certificate': (folder/'ingress.crt').read_text(), 'ingress_private_key': (folder/'ingress.key').read_text(),
            'origin_certificate': (folder/'origin.crt').read_text(), 'bandwidth_mbps': 1000,
            'targets': approved+[{'ip': control.origin, 'port': peer, 'network': network} for network in ('tcp', 'udp')]})
        atomic_json(folder/'request.json', profile)
        intent['peer_port'] = peer;save(control, host_id, intent)
        install_origin(folder, profile, binary)
        intent['state'] = 'sending';save(control, host_id, intent)
        try: client.request('PUT', '/v1/private-transports/'+generation, profile, revision=snapshot['revision'])
        except Exception:
            # Only exact-ID observation; no second mutating request.
            observation(client, generation, profile)
        intent['state'] = 'preparing';save(control, host_id, intent)
        deadline = time.monotonic()+150
        while True:
            observed = observation(client, generation, profile)
            if observed.get('state') == 'listening': break
            if observed.get('state') != 'preparing' or time.monotonic() >= deadline:
                raise ValueError('Защищённый транспорт Agent не готов; проверьте состояние перед повтором')
            time.sleep(1)
        probe(folder, profile, binary, peer)
        intent.update(state='ready', verified_at=int(time.time()));save(control, host_id, intent)
    except Exception:
        intent['state'] = 'unknown';save(control, host_id, intent)
        raise ValueError('Защищённый Relay не подтверждён. Открытая пересылка не включена; используйте проверку или отключение режима') from None
