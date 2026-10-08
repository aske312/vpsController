"""Client-only CPS decoys. No server settings or VPN keys are changed.

Packet formats: RFC 9000/9001/9369 (QUIC), 1035/9460 (DNS), 8489
(STUN), 3261 (SIP), 6347 (DTLS). These are initial decoys, not complete
application sessions or a guarantee of DPI bypass. Each new profile gets
fresh packet identifiers. QUIC uses authenticated Initial packet protection.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PRESETS = (
    ("server", "Текущий профиль сервера", "Сохранить действующую последовательность и совместимость."),
    ("quic", "QUIC v1 / HTTP/3", "Защищённый Initial с TLS ClientHello и ALPN h3, 1200 байт."),
    ("quic-fragmented", "QUIC v1 / фрагменты", "Тот же ClientHello в нескольких CRYPTO-фреймах с изменённым порядком."),
    ("quic-v2", "QUIC v2 / HTTP/3", "Initial версии 2 с её ключами и защитой заголовка."),
    ("dns-a", "DNS / IPv4", "Запрос A с новым transaction ID перед каждым handshake."),
    ("dns-aaaa", "DNS / IPv6", "Запрос AAAA; тип сигнатуры не меняет маршрутизацию VPN."),
    ("dns-https", "DNS / HTTPS", "DNS-запрос записи HTTPS, не HTTP-соединение."),
    ("stun", "STUN / WebRTC", "Binding Request с новым transaction ID перед каждым handshake."),
    ("sip", "SIP / телефония", "OPTIONS-запрос с индивидуальными Call-ID и branch."),
    ("dtls", "DTLS 1.2", "ClientHello защищённого UDP-транспорта; не HTTPS поверх TCP."),
)
PRESET_IDS = tuple(row[0] for row in PRESETS)


def preset_catalog() -> list[dict]:
    return [{"id": key, "label": label, "description": detail,
             "requires_cps": key != "server", "availability": "network-dependent"}
            for key, label, detail in PRESETS]


def normalize_domain(value: str) -> str:
    domain = value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    if len(domain) > 253 or '.' not in domain or not all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
        for label in domain.split('.')
    ):
        raise ValueError('Invalid AWG signature domain')
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ValueError('AWG signature domain must not be an IP address')


def u16(value: int) -> bytes:
    return struct.pack('!H', value)


def varint(value: int) -> bytes:
    if value < 64:
        return bytes([value])
    if value < 16384:
        return u16(value | 0x4000)
    raise ValueError('QUIC varint is too large')


def expand_label(secret: bytes, label: str, size: int) -> bytes:
    name = ('tls13 ' + label).encode()
    info = u16(size) + bytes([len(name)]) + name + b'\0'
    output, block = b'', b''
    for counter in range(1, (size + 31) // 32 + 1):
        block = hmac.new(secret, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
    return output[:size]


def initial_keys(dcid: bytes, version: int = 1) -> tuple[bytes, bytes, bytes]:
    salt = bytes.fromhex('38762cf7f55934b34d179ae6a4c80cadccbb7f0a' if version == 1 else
                         '0dede3def700a6db819381be6e269dcbf9bd2ed9')
    initial = hmac.new(salt, dcid, hashlib.sha256).digest()
    secret = expand_label(initial, 'client in', 32)
    prefix = 'quic' if version == 1 else 'quicv2'
    return (expand_label(secret, prefix + ' key', 16),
            expand_label(secret, prefix + ' iv', 12),
            expand_label(secret, prefix + ' hp', 16))


def extension(kind: int, data: bytes) -> bytes:
    return u16(kind) + u16(len(data)) + data


def quic_initial(domain: str, fragmented: bool = False, version: int = 1) -> bytes:
    dcid, scid = secrets.token_bytes(8), secrets.token_bytes(8)
    hostname = domain.encode('ascii')
    sni = b'\0' + u16(len(hostname)) + hostname
    share = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    keyshare = u16(29) + u16(len(share)) + share
    params = varint(15) + varint(len(scid)) + scid
    if version == 2:
        params += varint(17) + varint(12) + struct.pack('!III', 0x6b3343cf, 0x6b3343cf, 1)
    extensions = b''.join((
        extension(0, u16(len(sni)) + sni),
        extension(10, b'\0\2\0\x1d'),
        extension(13, b'\0\6\x04\x03\x08\x04\x08\x07'),
        extension(16, b'\0\3\2h3'),
        extension(43, b'\2\3\4'),
        extension(51, u16(len(keyshare)) + keyshare),
        extension(57, params),
    ))
    hello_body = (b'\3\3' + secrets.token_bytes(32) + b'\0' +
                  b'\0\6\x13\1\x13\2\x13\3' + b'\1\0' + u16(len(extensions)) + extensions)
    hello = b'\1' + len(hello_body).to_bytes(3, 'big') + hello_body
    if fragmented:
        split = len(hello) // 2
        parts = ((split, hello[split:]), (0, hello[:split]))
    else:
        parts = ((0, hello),)
    frames = b''.join(b'\6' + varint(offset) + varint(len(part)) + part for offset, part in parts)
    wire_version = 1 if version == 1 else 0x6b3343cf
    prefix = bytes([0xc3 if version == 1 else 0xd3]) + struct.pack('!I', wire_version)
    prefix += bytes([len(dcid)]) + dcid + bytes([len(scid)]) + scid + b'\0'
    # The Initial packet is exactly 1200 bytes, including AEAD tag and header.
    protected_length = 1200 - len(prefix) - 2
    header = prefix + varint(protected_length) + b'\0' * 4
    plaintext = frames + b'\0' * (1200 - len(header) - 16 - len(frames))
    key, iv, hp = initial_keys(dcid, version)
    encrypted = AESGCM(key).encrypt(iv, plaintext, header)  # packet number 0
    encryptor = Cipher(algorithms.AES(hp), modes.ECB()).encryptor()
    mask = encryptor.update(encrypted[:16]) + encryptor.finalize()
    protected = bytearray(header)
    protected[0] ^= mask[0] & 0x0f
    for index in range(4):
        protected[-4 + index] ^= mask[1 + index]
    return bytes(protected) + encrypted


def literal(data: bytes) -> str:
    return '<b 0x' + data.hex() + '>'


def signature(preset: str, domain: str) -> str:
    if preset.startswith('quic'):
        return literal(quic_initial(domain, preset == 'quic-fragmented', 2 if preset == 'quic-v2' else 1))
    if preset.startswith('dns-'):
        qtype = {'dns-a': 1, 'dns-aaaa': 28, 'dns-https': 65}[preset]
        qname = b''.join(bytes([len(label)]) + label.encode() for label in domain.split('.')) + b'\0'
        return '<r 2>' + literal(bytes.fromhex('01000001000000000000') + qname + u16(qtype) + u16(1))
    if preset == 'stun':
        return literal(bytes.fromhex('000100002112a442')) + '<r 12>'
    if preset == 'sip':
        branch, call_id, tag = (secrets.token_hex(8) for _ in range(3))
        text = (f'OPTIONS sip:{domain} SIP/2.0\r\n'
                f'Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bK{branch};rport\r\n'
                f'Max-Forwards: 70\r\nTo: <sip:{domain}>\r\n'
                f'From: <sip:probe@{domain}>;tag={tag}\r\nCall-ID: {call_id}@{domain}\r\n'
                'CSeq: 1 OPTIONS\r\nContent-Length: 0\r\n\r\n')
        return literal(text.encode('ascii'))
    if preset == 'dtls':
        # RFC 6347: unfragmented ClientHello, no cookie, DTLS 1.2 body.
        tail = b'\0\0\0\4\xc0\x2b\xc0\x2f\1\0'
        ext = extension(10, b'\0\2\0\x17') + extension(11, b'\1\0') + extension(13, b'\0\4\4\3\4\1')
        tail += u16(len(ext)) + ext
        size = 2 + 32 + len(tail)
        handshake = b'\1' + size.to_bytes(3, 'big') + b'\0' * 5 + size.to_bytes(3, 'big')
        record = bytes.fromhex('16feff0000000000000000') + u16(12 + size)
        return literal(record + handshake + b'\xfe\xfd') + '<t><r 28>' + literal(tail)
    raise ValueError('Unknown AWG signature preset')


def client_profile(base: dict, preset: str, domain: str) -> dict:
    if preset not in PRESET_IDS:
        raise ValueError('Unknown AWG signature preset')
    result = dict(base)
    if preset != 'server':
        domain = normalize_domain(domain) if preset not in ('stun', 'dtls') else 'example.com'
        for key in ('I1', 'I2', 'I3', 'I4', 'I5'):
            result.pop(key, None)
        result['I1'] = signature(preset, domain)
    return result
