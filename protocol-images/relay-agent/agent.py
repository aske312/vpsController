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

VERSION = '1.0.0'
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

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, _addr):
        self.touched = time.monotonic()
        if not self.route.closed and self.route.transport.get_write_buffer_size() < 262144:
            self.route.transport.sendto(data, self.client)
            self.route.received += len(data)

    def error_received(self, _exc):
        self.route.errors += 1


class RelayRoute(asyncio.DatagramProtocol):
    def __init__(self, config, listen_host='0.0.0.0'):
        self.config, self.listen_host = config, listen_host
        self.server = self.transport = None
        self.sessions, self.pending = {}, {}
        self.tasks, self.writers = set(), set()
        self.sent = self.received = self.errors = 0
        self.closed = False

    async def start(self):
        # Exclusive reservation; do not share a port with an existing protocol.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM if self.config['transport'] == 'tcp' else socket.SOCK_DGRAM)
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
            writer.close()
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        upstream = None
        pumps = []
        try:
            incoming, upstream = await asyncio.wait_for(asyncio.open_connection(self.config['target_ip'], self.config['target_port']), 10)
            self.writers.add(upstream)

            async def pump(source, destination, direction):
                while True:
                    data = await asyncio.wait_for(source.read(65536), 300)
                    if not data:
                        if destination.can_write_eof():
                            destination.write_eof()
                        return
                    destination.write(data)
                    await asyncio.wait_for(destination.drain(), 30)
                    if direction == 'sent': self.sent += len(data)
                    else: self.received += len(data)

            pumps = [asyncio.create_task(pump(reader, upstream, 'sent')), asyncio.create_task(pump(incoming, writer, 'received'))]
            # Preserve half-close: a client can finish its request before the response.
            await asyncio.gather(*pumps)
        except (OSError, asyncio.TimeoutError):
            self.errors += 1
        finally:
            for item in pumps: item.cancel()
            if pumps: await asyncio.gather(*pumps, return_exceptions=True)
            for item in (writer, upstream):
                if item is not None:
                    self.writers.discard(item)
                    item.close()
            self.tasks.discard(task)

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
        elif client in self.pending:
            if len(self.pending[client]) < 8: self.pending[client].append(data)
        elif client not in self.pending and len(self.sessions) + len(self.pending) < MAX_SESSIONS:
            self.pending[client] = [data]
            self.track(self.new_udp_session(client))

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
        except OSError:
            self.errors += 1
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
        return {**self.config, 'state': 'listening', 'sent_bytes': self.sent, 'received_bytes': self.received,
                'sessions': len(self.sessions) if self.transport else len(self.writers) // 2, 'errors': self.errors,
                'target_verified': False}


class Agent:
    def __init__(self, config_dir=CONFIG_DIR, state_dir=STATE_DIR, listen_host='0.0.0.0'):
        self.config_dir, self.state_dir, self.listen_host = Path(config_dir), Path(state_dir), listen_host
        self.routes, self.failed = {}, {}
        self.revision = 0
        self.lock = asyncio.Lock()
        self.http_connections = 0

    def settings(self):
        return json.loads((self.config_dir / 'config.json').read_text())

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
                    'listen_ports': {'min': PORT_MIN, 'max': PORT_MAX}, 'max_routes': MAX_ROUTES}
        if method == 'GET' and path == '/v1/routes':
            return {'revision': self.revision, 'items': [route.status() for route in self.routes.values()] +
                    [{**item['config'], 'state': 'failed', 'error': item['error'], 'target_verified': False} for item in self.failed.values()]}
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
        server = await asyncio.start_server(self.http_client, self.listen_host, API_PORT, ssl=context,
                                            ssl_handshake_timeout=5, limit=8192)
        async with server:
            await stop.wait()
        for route in self.routes.values(): await route.close()


if __name__ == '__main__':
    import sys
    if '--version' in sys.argv: print(VERSION)
    else: asyncio.run(Agent().serve())
