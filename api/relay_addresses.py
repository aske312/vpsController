"""Public Relay endpoint identities; no DNS or availability checks during export."""
import ipaddress
import re


def normalize_address(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError('Некорректный адрес Relay')
    value = value.strip().lower().rstrip('.')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:value = value.encode('idna').decode('ascii')
        except UnicodeError:raise ValueError('Некорректный домен Relay') from None
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
            raise ValueError('Нужен IP или домен Relay без порта')
        return value
    if not address.is_global or address.is_multicast or getattr(address, 'ipv4_mapped', None) or '%' in value:
        raise ValueError('Нужен публичный IP Relay')
    return str(address)


def normalize_addresses(value, fallback):
    if value is None:value = {}
    if not isinstance(value, dict):raise ValueError('Некорректный список адресов Relay')
    result = {}
    for key, version in (('ipv4',4),('ipv6',6),('domains',None)):
        items = value.get(key, [])
        if not isinstance(items,list) or len(items)>64:raise ValueError('Некорректный список адресов Relay')
        result[key] = []
        for item in items:
            normalized = normalize_address(item)
            try:address = ipaddress.ip_address(normalized)
            except ValueError:address = None
            if (version is None and address is not None) or (version is not None and (address is None or address.version!=version)):
                raise ValueError('Семейство адреса Relay не совпадает')
            if normalized not in result[key]:result[key].append(normalized)
    if fallback is None:return result
    address = ipaddress.ip_address(normalize_address(fallback))
    key = 'ipv4' if address.version==4 else 'ipv6'
    result[key] = [str(address),*[item for item in result[key] if item!=str(address)]][:64]
    return result


def all_addresses(value):
    return value['ipv4']+value['ipv6']+value['domains']


def authority(address, port):
    return f'[{address}]:{port}' if ':' in address else f'{address}:{port}'
