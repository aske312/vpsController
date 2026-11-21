"""Controller binding and authenticated, fixed-destination TCP ingress."""
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re


class SecurityError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def address(value):
    ip = ipaddress.ip_address(value)
    return str(getattr(ip, 'ipv4_mapped', None) or ip)


def authentication(value):
    if not isinstance(value, dict) or set(value) != {'type', 'username', 'password_sha256', 'credential_id'}:
        raise SecurityError('Expected a complete authenticated TCP ingress', 422)
    if value['type'] != 'tls-connect' or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(value['username'])):
        raise SecurityError('Invalid TCP ingress identity', 422)
    if not re.fullmatch(r'[0-9a-f]{64}', str(value['password_sha256'])) or not re.fullmatch(r'[0-9a-f]{32}', str(value['credential_id'])):
        raise SecurityError('Invalid TCP ingress credential', 422)
    return dict(value)


def public_route(config):
    result = dict(config)
    if 'auth' in result:
        result['auth'] = {key: value for key, value in result['auth'].items() if key != 'password_sha256'}
    return result


class ControllerPolicy:
    def __init__(self, state_dir, atomic):
        self.path = Path(state_dir) / 'controller.json'
        self.atomic = atomic

    def read(self):
        if self.path.is_symlink():
            raise SecurityError('Controller binding is unavailable', 503)
        try:
            result = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise SecurityError('Controller binding is unavailable', 503) from None
        try:
            valid = (isinstance(result, dict) and set(result) == {'schema', 'origin_ip'} and type(result['schema']) is int and result['schema'] == 1
                     and ipaddress.ip_address(result['origin_ip']).is_global and not ipaddress.ip_address(result['origin_ip']).is_multicast
                     and address(result['origin_ip']) == result['origin_ip'])
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise SecurityError('Controller binding is invalid', 503)
        return result

    def permits_peer(self, peer):
        if peer is None:
            return True  # In-process dispatch; the network entry point always supplies a peer.
        ip = ipaddress.ip_address(address(peer))
        binding = self.read()
        return ip.is_loopback or binding is None or str(ip) == binding['origin_ip']

    def bind(self, peer, body):
        if not isinstance(body, dict) or set(body) != {'origin_ip'} or peer is None:
            raise SecurityError('Expected the observed controller address', 422)
        try:
            observed = address(peer)
            valid = (ipaddress.ip_address(observed).is_global and not ipaddress.ip_address(observed).is_multicast
                     and address(body['origin_ip']) == observed)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise SecurityError('Controller origin must match its observed source address')
        previous = self.read()
        if previous and previous['origin_ip'] != observed:
            raise SecurityError('This Relay is already bound to another controller')
        value = {'schema': 1, 'origin_ip': observed}
        if previous is None:
            self.atomic(self.path, value)
            self.path.chmod(0o600)
        return value

    def authorize_route(self, config):
        binding = self.read()
        if binding is None:
            raise SecurityError('Bind the controller before publishing a new route', 409)
        if config.get('auth'):
            if config['transport'] != 'tcp':
                raise SecurityError('TLS CONNECT ingress requires TCP', 422)
            authentication(config['auth'])
        elif config['target_ip'] != binding['origin_ip']:
            raise SecurityError('External destinations require authenticated TLS CONNECT ingress')

    def snapshot(self, routes):
        binding = self.read()
        unsafe = [r['id'] for r in routes if not r.get('auth') and (binding is None or r['target_ip'] != binding['origin_ip'])]
        return {'state': 'unpaired' if binding is None else 'legacy_exposed' if unsafe else 'ready',
                'controller_ip': binding['origin_ip'] if binding else None,
                'unauthenticated_external_routes': unsafe,
                'api_source_restricted': binding is not None}


async def connect_request(reader, writer, config):
    """Authenticate before opening upstream; never accept arbitrary CONNECT targets."""
    try:
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
        if len(raw) > 8192:
            raise ValueError()
        lines = raw.decode('ascii').split('\r\n')
        method, target, version = lines[0].split(' ')
        fields = {}
        for line in lines[1:]:
            if not line:
                continue
            key, value = line.split(':', 1)
            key = key.lower()
            if key in fields:
                raise ValueError()
            fields[key] = value.strip()
        auth = fields.get('proxy-authorization', '')
        if not auth.startswith('Basic '):
            raise ValueError()
        decoded = base64.b64decode(auth[6:], validate=True).decode('ascii')
        username, password = decoded.split(':', 1)
        expected = config['auth']
        valid = hmac.compare_digest(username, expected['username']) & hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), expected['password_sha256'])
        ip = ipaddress.ip_address(config['target_ip'])
        destination = (f'[{ip}]' if ip.version == 6 else str(ip)) + ':' + str(config['target_port'])
        if (not valid or method != 'CONNECT' or version != 'HTTP/1.1' or target != destination
                or 'transfer-encoding' in fields or fields.get('content-length', '0') != '0'):
            raise ValueError()
    except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        await asyncio.wait_for(writer.drain(), 5)
        raise SecurityError('TCP ingress authentication or destination rejected') from None
