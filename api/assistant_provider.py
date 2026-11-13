"""Bounded OpenAI-compatible model discovery with pinned public TLS connections."""
import http.client
import ipaddress
import json
import math
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


def token_quota(url, key):
    """Read optional New API quota on the same origin, without following redirects."""
    parsed = urllib.parse.urlsplit(base_url(url))
    if parsed.path != '/v1':
        return None
    connection = PublicHTTPSConnection(parsed.hostname, timeout=5, context=ssl.create_default_context())
    try:
        connection.request('GET', '/api/usage/token/', headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return None
        data = json.loads(raw).get('data', {})
        if data.get('object') != 'token_usage':
            return None
        remaining = data.get('total_available')
        result = {}
        if type(data.get('unlimited_quota')) is bool: result['unlimited'] = data['unlimited_quota']
        if type(remaining) is int and abs(remaining) <= 10**15: result['remaining'] = remaining
        for source, target in (('total_granted', 'granted'), ('total_used', 'used')):
            value = data.get(source)
            if type(value) is int and 0 <= value <= 10**15: result[target] = value
        expires = data.get('expires_at')
        if type(expires) is int and 0 <= expires <= 253402300799: result['expires_at'] = expires
        if type(data.get('model_limits_enabled')) is bool:
            result['model_limits_enabled'] = data['model_limits_enabled']
            limits = data.get('model_limits')
            if isinstance(limits, dict) and all(type(enabled) is bool for enabled in limits.values()):
                allowed = sorted(name for name, enabled in limits.items() if enabled is True and model_id(name) and key not in name)
                result['allowed_models'] = allowed[:100]
                result['allowed_models_count'] = len(allowed)
                result['allowed_models_truncated'] = len(allowed) > 100
        account = data.get('user_quota')
        if isinstance(account, dict) and type(account.get('total_available')) is int and abs(account['total_available']) <= 10**15:
            result['account_remaining'] = account['total_available']
        return dollar_balance(parsed.hostname, result) if result else None
    except (AttributeError, ValueError, OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def dollar_balance(host, quota):
    """New API publishes its quota conversion on the same-origin public status."""
    connection = PublicHTTPSConnection(host, timeout=5, context=ssl.create_default_context())
    try:
        connection.request('GET', '/api/status', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200: return quota
        raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024: return quota
        data = json.loads(raw).get('data', {})
        scale = data.get('quota_per_unit')
        if data.get('quota_display_type') != 'USD' or type(scale) not in (int, float) or not 0 < scale <= 10**12 or not math.isfinite(scale): return quota
        result = {**quota, 'quota_per_usd': scale}
        if type(quota.get('account_remaining')) is int: result['balance_usd'] = quota['account_remaining'] / scale
        if type(quota.get('remaining')) is int and not quota.get('unlimited'): result['remaining_usd'] = quota['remaining'] / scale
        return result
    except (AttributeError, ValueError, OSError, http.client.HTTPException):
        return quota
    finally:
        connection.close()
