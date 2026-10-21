"""Pinned Relay Agent API v1 client. The token is sent only after pin validation."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import ssl
from urllib.parse import urlsplit


class AgentAPIError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(detail)


class RelayClient:
    def __init__(self, agent_url, token, certificate_sha256, timeout=8):
        url = urlsplit(agent_url)
        if url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('Expected an HTTPS agent origin')
        address = ipaddress.ip_address(url.hostname)
        if not address.is_global or address.is_multicast or getattr(address, 'ipv4_mapped', None) or '%' in str(url.hostname):
            raise ValueError('Expected a public IP agent address')
        if url.port is None or not 1024 <= url.port <= 65535 or 20000 <= url.port <= 20999:
            raise ValueError('Expected an explicit Relay API port outside the route range')
        if not re.fullmatch(r'[0-9a-fA-F]{64}', certificate_sha256):
            raise ValueError('Invalid certificate fingerprint')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{40,128}', token):
            raise ValueError('Invalid agent token')
        self.host, self.port, self.token = str(address), url.port, token
        self.fingerprint, self.timeout = certificate_sha256.lower(), timeout

    def request(self, method, path, body=None, revision=None):
        if method not in ('GET', 'PUT', 'DELETE') or (path not in ('/v1/status', '/v1/routes') and not re.fullmatch(r'/v1/routes/[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', path) and not re.fullmatch(r'/v1/measurements/[0-9a-f]{32}', path)):
            raise ValueError('Invalid agent request')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Trust the explicit DER pin, before HTTP.
        connection = http.client.HTTPSConnection(self.host, self.port, context=context, timeout=self.timeout)
        try:
            connection.connect()
            fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(fingerprint, self.fingerprint):
                raise AgentAPIError(495, 'Отпечаток сертификата агента изменился; токен не отправлен')
            headers = {'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'}
            if revision is not None:
                headers['If-Match'] = str(revision)
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise AgentAPIError(502, 'Слишком большой ответ агента')
            # Never reflect arbitrary remote text into UI, logs or operation history.
            if response.status != 200:
                raise AgentAPIError(response.status, {401: 'Токен агента отклонён', 409: 'Ревизия или маршрут агента изменились', 428: 'Агент требует ревизию'}.get(response.status, 'Запрос отклонён агентом'))
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Invalid agent response')
            return data
        finally:
            connection.close()
