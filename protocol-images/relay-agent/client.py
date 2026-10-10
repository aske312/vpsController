"""Portable server-side relay client: pin TLS before transmitting the token."""
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
    def __init__(self, agent_url, token, certificate_sha256, timeout=10):
        url = urlsplit(agent_url)
        if url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('Expected an HTTPS agent origin')
        address = ipaddress.ip_address(url.hostname)
        if not address.is_global or address.version != 4:
            raise ValueError('Expected a public IPv4 agent address')
        if url.port != 9443: raise ValueError('Expected agent port 9443')
        if not re.fullmatch(r'[0-9a-fA-F]{64}', certificate_sha256): raise ValueError('Invalid certificate fingerprint')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{40,128}', token): raise ValueError('Invalid agent token')
        self.host, self.port, self.token = str(address), url.port, token
        self.fingerprint, self.timeout = certificate_sha256.lower(), timeout

    def request(self, method, path, body=None, revision=None):
        if path not in ('/v1/status', '/v1/routes') and not re.fullmatch(r'/v1/routes/[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', path):
            raise ValueError('Invalid API path')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # This self-signed node is trusted by an explicit certificate pin, not a CA.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(self.host, self.port, context=context, timeout=self.timeout)
        try:
            connection.connect()
            fingerprint = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(fingerprint, self.fingerprint):
                raise AgentAPIError(495, 'Relay certificate fingerprint changed; token was not sent')
            headers = {'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'}
            if revision is not None: headers['If-Match'] = str(revision)
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536: raise AgentAPIError(502, 'Relay response exceeded the size limit')
            data = json.loads(raw)
            if response.status != 200: raise AgentAPIError(response.status, data.get('detail', 'Relay API error'))
            return data
        finally:
            connection.close()
