"""Bounded OpenAI-compatible model discovery with pinned public TLS connections."""
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse


def base_url(value):
    parsed = urllib.parse.urlsplit(value.strip().rstrip('/'))
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Нужен HTTPS base URL провайдера без пароля, query и fragment')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/v1', '', ''))


def model_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and not any(ord(c) < 33 or ord(c) == 127 for c in value)


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError('Адрес AI-провайдера должен быть публичным')
        address = next((row[4][0] for row in addresses if row[0] == socket.AF_INET), addresses[0][4][0])
        sock = socket.create_connection((address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def catalog(url, key):
    parsed = urllib.parse.urlsplit(base_url(url))
    connection = PublicHTTPSConnection(parsed.hostname, timeout=15, context=ssl.create_default_context())
    try:
        connection.request('GET', parsed.path.rstrip('/') + '/models', headers={
            'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Каталог моделей: HTTP ' + str(response.status))
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Каталог моделей превышает допустимый размер')
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError('Провайдер вернул неверный формат каталога моделей')
        result = sorted({row['id'] for row in data['data'] if isinstance(row, dict) and model_id(row.get('id'))})
        if not result:
            raise ValueError('Каталог не содержит доступных моделей')
        if len(result) > 10000:
            raise ValueError('Каталог содержит слишком много моделей')
        return result
    finally:
        connection.close()
