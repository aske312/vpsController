#!/usr/bin/env python3
"""Unprivileged, bounded TCP/UDP relay with a separate TLS control plane."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import tempfile
import time
from addresses import read_inventory, dual_stack_available
import struct

VERSION = '1.4.0'
CONFIG_DIR = Path('/etc/vps-control-relay-agent')
STATE_DIR = Path('/var/lib/vps-control-relay-agent')
API_PORT = 9443
PORT_MIN, PORT_MAX = 20000, 20999
MAX_ROUTES, MAX_SESSIONS = 64, 128


class RelayError(ValueError):
    def __init__(self, detail, status=422):
        super().__init__(detail)
        self.status = status


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.relay-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_route(route_id, body, own_ips=()):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', route_id):
        raise RelayError('Invalid route id')
    if not isinstance(body, dict) or set(body) != {'transport', 'listen_port', 'target_ip', 'target_port'}:
        raise RelayError('Expected transport, listen_port, target_ip and target_port only')
    if body['transport'] not in ('tcp', 'udp'):
        raise RelayError('Transport must be tcp or udp')
    for key, low, high in [('listen_port', PORT_MIN, PORT_MAX), ('target_port', 1, 65535)]:
        if type(body[key]) is not int or not low <= body[key] <= high:
            raise RelayError(f'{key} must be an integer between {low} and {high}')
    try:
        address = ipaddress.ip_address(body['target_ip'])
    except (ValueError, TypeError):
        raise RelayError('target_ip must be a public IP literal; hostnames are not accepted')
    if not address.is_global or address.is_multicast or address.is_unspecified or address.is_loopback:
        raise RelayError('Private, local, multicast and reserved targets are forbidden')
    if getattr(address, 'ipv4_mapped', None) or address.version != 4:
        raise RelayError('This API version supports IPv4 targets only')
    if str(address) in own_ips:
        raise RelayError('The relay itself cannot be a target')
    return {'id': route_id, **body, 'target_ip': str(address)}


class UpstreamUDP(asyncio.DatagramProtocol):
    def __init__(self, route, client):
        self.route, self.client, self.transport = route, client, None
        self.touched = time.monotonic()
        self.awaiting_since = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, _addr):
        self.touched = time.monotonic()
        self.awaiting_since = None
        if not self.route.closed and self.route.transport.get_write_buffer_size() < 262144:
            self.route.transport.sendto(data, self.client)
            self.route.received += len(data)
            self.route.packet_received()
        elif not self.route.closed:
            self.route.failure('udp_buffer_full', dropped=True)

    def error_received(self, _exc):
        self.route.failure('udp_upstream_error')


class RelayRoute(asyncio.DatagramProtocol):
    def __init__(self, config, listen_host='0.0.0.0'):
        self.config, self.listen_host = config, listen_host
        self.server = self.transport = None
        self.sessions, self.pending = {}, {}
        self.tasks, self.writers = set(), set()
        self.sent = self.received = self.errors = 0
        self.sent_packets = self.received_packets = self.dropped_packets = 0
        self.error_counts = {}
        self.last_error = self.last_error_at = None
        self.last_sent_at = self.last_received_at = None
        self.tcp_waiting = {}
        self.closed = False

    def failure(self, code, *, dropped=False):
        self.errors += 1
        self.error_counts[code] = self.error_counts.get(code, 0) + 1
        self.last_error, self.last_error_at = code, int(time.time())
        if dropped: self.dropped_packets += 1

    def packet_received(self):
        self.received_packets += 1
        self.last_received_at = int(time.time())

    def error_received(self, _exc):
        self.failure('udp_listener_error')

    async def start(self):
        # Exclusive reservation; do not share a port with an existing protocol.
        ipv6 = ':' in self.listen_host
        sock = socket.socket(socket.AF_INET6 if ipv6 else socket.AF_INET, socket.SOCK_STREAM if self.config['transport'] == 'tcp' else socket.SOCK_DGRAM)
        if ipv6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif self.config['transport'] == 'tcp':
            # Rebind after restart despite TIME_WAIT; no SO_REUSEPORT or shared listener.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.listen_host, self.config['listen_port']))
            sock.setblocking(False)
            if self.config['transport'] == 'tcp':
                sock.listen(128)
                self.server = await asyncio.start_server(self.tcp_client, sock=sock)
            else:
                await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
                self.track(self.expire_sessions())
        except BaseException:
            sock.close()
            raise

    def track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def tcp_client(self, reader, writer):
        if self.closed or len(self.tasks) >= MAX_SESSIONS:
            if not self.closed: self.failure('session_limit')
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        upstream = None
        pumps = []
        phase = 'tcp_connect'
        try:
            incoming, upstream = await asyncio.wait_for(asyncio.open_connection(self.config['target_ip'], self.config['target_port']), 10)
            self.writers.add(upstream)
            phase = 'tcp_transfer'

            async def pump(source, destination, direction):
                while True:
                    data = await asyncio.wait_for(source.read(65536), 300)
                    if not data:
                        if destination.can_write_eof():
                            destination.write_eof()
                        return
                    destination.write(data)
                    await asyncio.wait_for(destination.drain(), 30)
                    if direction == 'sent':
                        self.sent += len(data)
                        self.last_sent_at = int(time.time())
                        self.tcp_waiting.setdefault(task, time.monotonic())
                    else:
                        self.received += len(data)
                        self.last_received_at = int(time.time())
                        self.tcp_waiting.pop(task, None)

            pumps = [asyncio.create_task(pump(reader, upstream, 'sent')), asyncio.create_task(pump(incoming, writer, 'received'))]
            # Preserve half-close: a client can finish its request before the response.
            await asyncio.gather(*pumps)
        except (OSError, asyncio.TimeoutError) as exc:
            self.failure(phase + ('_timeout' if isinstance(exc, asyncio.TimeoutError) else '_error'))
        finally:
            for item in pumps: item.cancel()
            if pumps: await asyncio.gather(*pumps, return_exceptions=True)
            for item in (writer, upstream):
                if item is not None:
                    self.writers.discard(item)
                    item.close()
            self.tasks.discard(task)
            self.tcp_waiting.pop(task, None)

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, client):
        if self.closed: return
        session = self.sessions.get(client)
        if session:
            session.touched = time.monotonic()
            if session.transport.get_write_buffer_size() < 262144:
                session.transport.sendto(data)
                self.sent += len(data)
                self.sent_packets += 1
                self.last_sent_at = int(time.time())
                if session.awaiting_since is None: session.awaiting_since = time.monotonic()
            else:
                self.failure('udp_buffer_full', dropped=True)
        elif client in self.pending:
            if len(self.pending[client]) < 8: self.pending[client].append(data)
            else: self.failure('udp_pending_full', dropped=True)
        elif client not in self.pending and len(self.sessions) + len(self.pending) < MAX_SESSIONS:
            self.pending[client] = [data]
            self.track(self.new_udp_session(client))
        else:
            self.failure('session_limit', dropped=True)

    async def new_udp_session(self, client):
        try:
            _transport, session = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: UpstreamUDP(self, client), remote_addr=(self.config['target_ip'], self.config['target_port']), family=socket.AF_INET)
            if self.closed:
                session.transport.close()
                return
            self.sessions[client] = session
            for data in self.pending[client]:
                session.transport.sendto(data)
                self.sent += len(data)
                self.sent_packets += 1
                self.last_sent_at = int(time.time())
                if session.awaiting_since is None: session.awaiting_since = time.monotonic()
        except OSError:
            self.failure('udp_session_error')
        finally:
            self.pending.pop(client, None)

    async def expire_sessions(self):
        while not self.closed:
            await asyncio.sleep(10)
            for client, session in list(self.sessions.items()):
                if time.monotonic() - session.touched > 60:
                    session.transport.close()
                    del self.sessions[client]

    async def close(self):
        self.closed = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.transport: self.transport.close()
        for session in self.sessions.values(): session.transport.close()
        for writer in self.writers: writer.close()
        tasks = list(self.tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        self.sessions.clear()

    def status(self):
        now = time.monotonic()
        waiting = list(self.tcp_waiting.values()) + [item.awaiting_since for item in self.sessions.values() if getattr(item, 'awaiting_since', None) is not None]
        unanswered = sum(now - value >= 5 for value in waiting)
        return_state = 'no_return' if unanswered else 'waiting' if waiting else 'flowing' if self.last_received_at and time.time() - self.last_received_at < 30 else 'idle'
        return {**self.config, 'state': 'listening', 'sent_bytes': self.sent, 'received_bytes': self.received,
                'sessions': len(self.sessions) if self.transport else len(self.writers) // 2, 'errors': self.errors,
                'target_verified': False,
                'diagnostics': {'sent_packets': self.sent_packets, 'received_packets': self.received_packets,
                    'return_state': return_state, 'no_return_sessions': unanswered,
                    'dropped_packets': self.dropped_packets, 'error_counts': dict(self.error_counts),
                    'last_error': self.last_error, 'last_error_at': self.last_error_at,
                    'last_sent_at': self.last_sent_at, 'last_received_at': self.last_received_at}}


MAGIC = b'RLY2'
COUNT = 100
SIZE = 1200


def packet(identity, key, sequence):
    body = MAGIC + bytes.fromhex(identity) + struct.pack('!H', sequence)
    body += bytes(SIZE - 32 - len(body))
    return body + hmac.digest(bytes.fromhex(key), body, 'sha256')


def sequence(data, identity, key):
    if len(data) != SIZE or data[:20] != MAGIC + bytes.fromhex(identity): return None
    if not hmac.compare_digest(data[-32:], hmac.digest(bytes.fromhex(key), data[:-32], 'sha256')): return None
    number = struct.unpack('!H', data[20:22])[0]
    return number if number < COUNT else None


class Replies(asyncio.DatagramProtocol):
    def __init__(self, owner): self.owner = owner
    def datagram_received(self, data, _address): self.owner.reply(data)
    def error_received(self, _error): self.owner.socket_errors += 1


class Measurement(asyncio.DatagramProtocol):
    def __init__(self, identity, settings):
        self.id, self.settings = identity, settings
        self.key = settings['key']
        self.deadline = time.monotonic() + settings['ttl_seconds']
        self.ingress, self.forwarded, self.returned, self.egress = set(), set(), set(), set()
        self.clients, self.started = {}, {}
        self.rtts = []
        self.local_drops = self.socket_errors = 0
        self.transport = self.upstream = None
        self.closed = False

    def connection_made(self, transport): self.transport = transport
    def error_received(self, _error): self.socket_errors += 1

    async def start(self, listen_host, ports):
        loop = asyncio.get_running_loop()
        for port in ports:
            sock = socket.socket(socket.AF_INET6 if ':' in listen_host else socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if ':' in listen_host: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                sock.bind((listen_host, port)); sock.setblocking(False)
                await loop.create_datagram_endpoint(lambda: self, sock=sock)
                self.port = port
                break
            except OSError: sock.close()
        else: raise OSError('No diagnostic port available')
        try:
            self.upstream, _ = await loop.create_datagram_endpoint(lambda: Replies(self),
                remote_addr=(self.settings['target_ip'], self.settings['target_port']), family=socket.AF_INET)
        except BaseException:
            self.close(); raise
        loop.call_later(settings_ttl(self), self.close)

    def datagram_received(self, data, client):
        if self.closed or time.monotonic() >= self.deadline: return
        number = sequence(data, self.id, self.key)
        if number is None or number in self.ingress: return
        self.ingress.add(number)
        self.clients[number] = client
        if self.upstream.get_write_buffer_size() >= 262144:
            self.local_drops += 1; return
        self.started[number] = time.monotonic()
        self.upstream.sendto(data)
        self.forwarded.add(number)

    def reply(self, data):
        if self.closed: return
        number = sequence(data, self.id, self.key)
        if number is None or number not in self.forwarded or number in self.returned: return
        self.returned.add(number)
        self.rtts.append(round((time.monotonic() - self.started[number]) * 1000, 2))
        if self.transport.get_write_buffer_size() >= 262144:
            self.local_drops += 1; return
        self.transport.sendto(data, self.clients[number])
        self.egress.add(number)

    def close(self):
        self.closed = True
        for transport in (self.transport, self.upstream):
            if transport: transport.close()
        self.clients.clear()
        self.key = ''
        self.settings.pop('key', None)

    def status(self):
        return {'id': self.id, 'route_id': self.settings['route_id'], 'listen_port': self.port,
            'state': 'expired' if self.closed else 'active', 'packet_count': COUNT, 'packet_size': SIZE,
            'received_sequences': sorted(self.ingress), 'forwarded_sequences': sorted(self.forwarded),
            'returned_sequences': sorted(self.returned), 'sent_sequences': sorted(self.egress),
            'local_drops': self.local_drops, 'socket_errors': self.socket_errors,
            'upstream_rtt_ms': round(sum(self.rtts) / len(self.rtts), 2) if self.rtts else None}


def settings_ttl(item): return max(0.1, item.deadline - time.monotonic())


def validate_measurement(identity, body, own_ips):
    if not re.fullmatch(r'[0-9a-f]{32}', identity) or not isinstance(body, dict) or set(body) != {'route_id', 'target_ip', 'target_port', 'key', 'ttl_seconds'}:
        raise ValueError('Invalid measurement')
    if not isinstance(body['target_ip'], str) or not isinstance(body['route_id'], str) or not isinstance(body['key'], str):
        raise ValueError('Invalid measurement field types')
    address = ipaddress.ip_address(body['target_ip'])
    if address.version != 4 or not address.is_global or address.is_multicast or str(address) in own_ips:
        raise ValueError('Invalid measurement target')
    if type(body['target_port']) is not int or not 1024 <= body['target_port'] <= 65535 or type(body['ttl_seconds']) is not int or not 10 <= body['ttl_seconds'] <= 1200:
        raise ValueError('Invalid measurement limits')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', str(body['route_id'])) or not re.fullmatch(r'[0-9a-f]{64}', str(body['key'])):
        raise ValueError('Invalid measurement identity')
    return {**body, 'target_ip': str(address)}


class Agent:
    def __init__(self, config_dir=CONFIG_DIR, state_dir=STATE_DIR, listen_host=None):
        listen_host = listen_host or ('::' if dual_stack_available() else '0.0.0.0')
        self.config_dir, self.state_dir, self.listen_host = Path(config_dir), Path(state_dir), listen_host
        self.routes, self.failed = {}, {}
        self.revision = 0
        self.lock = asyncio.Lock()
        self.http_connections = 0
        self.measurements = {}

    def settings(self):
        config = json.loads((self.config_dir / 'config.json').read_text())
        inventory = read_inventory(self.config_dir, config)
        config['own_ips'] = list(set(config.get('own_ips', []) + inventory['ipv4'] + inventory['ipv6']))
        return config

    def save(self, configs):
        revision = self.revision + 1
        atomic_json(self.state_dir / 'routes.json', {'revision': revision, 'items': configs})
        self.revision = revision

    def configs(self):
        return [route.config for route in self.routes.values()] + [item['config'] for item in self.failed.values()]

    async def restore(self):
        path = self.state_dir / 'routes.json'
        if not path.exists(): return
        stored = json.loads(path.read_text())
        self.revision = stored['revision']
        if len(stored['items']) > MAX_ROUTES: raise RelayError('Too many persisted routes')
        for config in stored['items']:
            route_id = config['id']
            config = validate_route(route_id, {k: v for k, v in config.items() if k != 'id'}, self.settings()['own_ips'])
            route = RelayRoute(config, self.listen_host)
            try:
                await route.start()
                self.routes[route_id] = route
            except OSError:
                self.failed[route_id] = {'config': config, 'error': 'Listener unavailable; check port conflicts'}

    def authorize(self, headers):
        token = headers.get('authorization', '')
        if not token.startswith('Bearer ') or len(token) > 256:
            raise RelayError('Unauthorized', 401)
        expected = self.settings()['token_sha256']
        actual = hashlib.sha256(token[7:].encode()).hexdigest()
        if not hmac.compare_digest(actual, expected): raise RelayError('Unauthorized', 401)

    async def dispatch(self, method, path, headers, body):
        self.authorize(headers)
        if method == 'GET' and path == '/v1/status':
            return {'api_version': 1, 'version': VERSION, 'revision': self.revision, 'routes': len(self.routes),
                    'failed_routes': len(self.failed), 'transports': ['tcp', 'udp'], 'address_family': 'ipv4',
                    'listen_ports': {'min': PORT_MIN, 'max': PORT_MAX}, 'max_routes': MAX_ROUTES,
                    'addresses': read_inventory(self.config_dir, self.settings()),
                    'updates': self.update_status(),
                    'capabilities': ['udp-measurement-v1', 'manual-update-v1'],
                    'address_checks': self.address_checks(),
                    'client_address_families': ['ipv4', 'ipv6'] if self.listen_host == '::' else ['ipv4']}
        if method == 'GET' and path == '/v1/routes':
            return {'revision': self.revision, 'reserved_udp_ports': sorted(item.port for item in self.measurements.values() if not item.closed), 'items': [route.status() for route in self.routes.values()] +
                    [{**item['config'], 'state': 'failed', 'error': item['error'], 'target_verified': False} for item in self.failed.values()]}
        update = re.fullmatch(r'/v1/updates/([0-9a-f]{32})', path)
        if update and method in ('PUT', 'GET'):
            async with self.lock:
                return self.update_request(method, update[1], body)
        measure = re.fullmatch(r'/v1/measurements/([0-9a-f]{32})', path)
        if measure:
            async with self.lock:
                return await self.measurement_request(method, measure[1], body)
        match = re.fullmatch(r'/v1/routes/([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})', path)
        if not match or method not in ('PUT', 'DELETE'): raise RelayError('Not found', 404)
        route_id = match[1]
        async with self.lock:
            existing = self.routes.get(route_id)
            failed = self.failed.get(route_id)
            if method == 'PUT':
                config = validate_route(route_id, body, self.settings()['own_ips'])
                if existing and config == existing.config:
                    return {'revision': self.revision, 'item': existing.status()}
                if existing or failed: raise RelayError('Route id already exists; delete before replacing', 409)
            elif not existing and not failed:
                return {'revision': self.revision, 'deleted': route_id}
            if 'if-match' not in headers: raise RelayError('If-Match revision is required', 428)
            if headers['if-match'] != str(self.revision): raise RelayError('Revision changed; read routes again', 409)
            if method == 'DELETE':
                self.save([item for item in self.configs() if item['id'] != route_id])
                if existing:
                    await existing.close()
                    del self.routes[route_id]
                self.failed.pop(route_id, None)
                return {'revision': self.revision, 'deleted': route_id}
            if len(self.routes) + len(self.failed) >= MAX_ROUTES: raise RelayError('Route limit reached', 409)
            if any(item['listen_port'] == config['listen_port'] and item['transport'] == config['transport'] for item in self.configs()):
                raise RelayError('Listener already reserved', 409)
            route = RelayRoute(config, self.listen_host)
            try:
                await route.start()
            except OSError:
                raise RelayError('Listener unavailable; check port conflicts', 409)
            try:
                self.save(self.configs() + [config])
            except BaseException:
                await route.close()
                raise
            self.routes[route_id] = route
            return {'revision': self.revision, 'item': route.status()}

    def update_status(self):
        try:
            enabled = json.loads((self.config_dir / 'updates.json').read_text()).get('enabled') is True
            path = self.config_dir / 'update-status.json'
            value = json.loads(path.read_text()) if path.exists() else {'state': 'unchecked'}
            result = {'enabled': enabled, 'interval_hours': 24,
                      **{key: value[key] for key in ('state', 'checked_at', 'version') if key in value}}
            manual = self.manual_update_status()
            if manual: result['manual'] = manual
            return result
        except (OSError, ValueError, TypeError):
            return {'enabled': False, 'interval_hours': 24, 'state': 'unknown'}

    def manual_update_status(self):
        path = self.state_dir/'update-request.json'
        if path.exists():
            value = json.loads(path.read_text())
            status = self.config_dir/'manual-update-status.json'
            if status.exists():
                observed = json.loads(status.read_text())
                if observed.get('id') == value.get('id'): return observed
            return {**value, 'state': 'queued' if time.time()-value['requested_at'] < 900 else 'unknown'}
        path = self.config_dir/'manual-update-status.json'
        return json.loads(path.read_text()) if path.exists() else None

    def update_request(self, method, identity, body):
        previous = self.manual_update_status()
        if method == 'GET':
            if not previous or previous.get('id') != identity: raise RelayError('Update request unavailable', 404)
            return previous
        if not isinstance(body, dict) or set(body) != {'version', 'digest'} or not isinstance(body['version'], str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', body['version']) or not isinstance(body['digest'], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', body['digest']):
            raise RelayError('Invalid update request')
        if previous and previous.get('id') == identity:
            if any(previous.get(key) != body[key] for key in body): raise RelayError('Update request changed', 409)
            return previous
        if (self.state_dir/'update-request.json').exists() or previous and previous.get('state') in ('queued', 'running', 'unknown'):
            raise RelayError('Previous update result must be confirmed', 409)
        if tuple(map(int, body['version'].split('.'))) <= tuple(map(int, VERSION.split('.'))):
            raise RelayError('Only a newer Agent version can be installed', 409)
        request = {**body, 'id': identity, 'requested_at': time.time()}
        atomic_json(self.state_dir/'update-request.json', request)
        return {**request, 'state': 'queued'}

    async def measurement_request(self, method, identity, body):
        existing = self.measurements.get(identity)
        if method == 'DELETE':
            if existing: existing.close(); self.measurements.pop(identity, None)
            return {'deleted': identity}
        if method == 'GET':
            if not existing: raise RelayError('Measurement unavailable', 404)
            return existing.status()
        if method != 'PUT': raise RelayError('Not found', 404)
        try: settings = validate_measurement(identity, body, self.settings()['own_ips'])
        except (ValueError, TypeError, KeyError): raise RelayError('Invalid measurement settings') from None
        if existing:
            if existing.closed: raise RelayError('Measurement expired; use a new identity', 409)
            if existing.settings != settings: raise RelayError('Measurement identity changed', 409)
            return existing.status()
        self.measurements = {key: item for key, item in self.measurements.items() if not item.closed}
        if len(self.measurements) >= 2 or settings['route_id'] not in self.routes:
            raise RelayError('Measurement limit or route unavailable', 409)
        used = {item['listen_port'] for item in self.configs() if item['transport'] == 'udp'} | {item.port for item in self.measurements.values()}
        item = Measurement(identity, settings)
        try: await item.start(self.listen_host, [port for port in range(PORT_MIN, PORT_MAX + 1) if port not in used])
        except OSError: raise RelayError('Measurement listener unavailable', 409) from None
        self.measurements[identity] = item
        return item.status()

    def address_checks(self):
        try:
            return json.loads((self.config_dir / 'addresses.json').read_text()).get('domain_checks', [])
        except (OSError, ValueError):
            return []

    async def http_client(self, reader, writer):
        if self.http_connections >= 32:
            writer.close()
            return
        self.http_connections += 1
        status, payload = 200, {}
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(raw) > 8192: raise RelayError('Headers too large', 431)
            lines = raw.decode('ascii').split('\r\n')
            method, path, version = lines[0].split(' ')
            if version != 'HTTP/1.1': raise RelayError('HTTP/1.1 required', 400)
            headers = {}
            for line in lines[1:]:
                if not line: continue
                key, value = line.split(':', 1)
                key = key.lower()
                if key in headers: raise RelayError('Duplicate headers', 400)
                headers[key] = value.strip()
            self.authorize(headers)  # reject before reading a body
            if 'transfer-encoding' in headers: raise RelayError('Chunked bodies are not supported', 400)
            size = int(headers.get('content-length', '0'))
            if not 0 <= size <= 16384: raise RelayError('Body too large', 413)
            data = await asyncio.wait_for(reader.readexactly(size), 5) if size else b'{}'
            payload = await self.dispatch(method, path, headers, json.loads(data))
        except RelayError as exc:
            status, payload = exc.status, {'detail': str(exc)}
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            status, payload = 400, {'detail': 'Malformed or incomplete request'}
        except Exception:
            status, payload = 503, {'detail': 'Agent storage or configuration unavailable'}
        finally:
            try:
                output = json.dumps(payload).encode()
                writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(output)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n'.encode() + output)
                await asyncio.wait_for(writer.drain(), 5)
            except (OSError, asyncio.TimeoutError): pass
            writer.close()
            self.http_connections -= 1

    async def serve(self):
        await self.restore()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.config_dir / 'server.crt', self.config_dir / 'server.key')
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(signum, stop.set)
        port = self.settings().get('api_port', API_PORT)
        if type(port) is not int or not 1024 <= port <= 65535 or PORT_MIN <= port <= PORT_MAX:
            raise RelayError('Invalid Relay API port')
        sock = socket.socket(socket.AF_INET6 if ':' in self.listen_host else socket.AF_INET, socket.SOCK_STREAM)
        if ':' in self.listen_host:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, port))
        sock.listen(128)
        sock.setblocking(False)
        server = await asyncio.start_server(self.http_client, sock=sock, ssl=context,
                                            ssl_handshake_timeout=5, limit=8192)
        async with server:
            await stop.wait()
        for item in self.measurements.values(): item.close()
        for route in self.routes.values(): await route.close()


if __name__ == '__main__':
    import sys
    if '--version' in sys.argv: print(VERSION)
    else: asyncio.run(Agent().serve())
