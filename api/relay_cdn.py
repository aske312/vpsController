"""Pinned Cloudflare destinations for transparent, client-facing Relay sockets."""
import ipaddress
import socket

import cdn_security
from relay_addresses import normalize_address, normalize_addresses


def domain(value):
    value = normalize_address(value)
    normalize_addresses({'domains': [value]}, None)
    return value


def edge_ip(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global or not any(
        address in ipaddress.ip_network(network) for network in cdn_security.networks()
        if ipaddress.ip_network(network).version == 4
    ):
        raise ValueError('CDN должен разрешаться в публичный IPv4 Cloudflare с включённым proxy')
    return str(address)


def resolve(value):
    """Resolve once at provisioning, never while exporting a subscription."""
    value = domain(value)
    addresses = {row[4][0] for row in socket.getaddrinfo(value, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)}
    if not addresses:
        raise ValueError('Не удалось получить IPv4 CDN-домена')
    return sorted(edge_ip(address) for address in addresses)[0]


def before(settings):
    return settings.get('relay_cdn') is True and settings.get('relay_cdn_mode') == 'before_cdn'


def after(settings):
    # Missing mode belongs to the previously delivered CF → Relay → VPS chain.
    return settings.get('relay_cdn') is True and not before(settings)
