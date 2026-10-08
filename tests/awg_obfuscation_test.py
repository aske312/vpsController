import re
import struct
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from api import awg_obfuscation as awg


def materialize(cps):
    result = b''
    for tag in re.findall(r'<([^>]+)>', cps):
        kind, _, value = tag.partition(' ')
        if kind == 'b':
            result += bytes.fromhex(value[2:])
        elif kind == 'r':
            result += b'R' * int(value)
        elif kind == 't':
            result += struct.pack('!I', 1720000000)
        else:
            raise AssertionError(tag)
    return result


def read_varint(data, offset):
    size = 1 << (data[offset] >> 6)
    return int.from_bytes(data[offset:offset + size], 'big') & ((1 << (size * 8 - 2)) - 1), offset + size


class AwgObfuscationTests(unittest.TestCase):
    def test_rfc9001_initial_keys_vector(self):
        key, iv, hp = awg.initial_keys(bytes.fromhex('8394c8f03e515708'))
        self.assertEqual(key.hex(), '1f369613dd76d5467730efcbe3b1a22d')
        self.assertEqual(iv.hex(), 'fa044b2f42a3fd3b46fb255c')
        self.assertEqual(hp.hex(), '9f50449e04a0e810283a1e9933adedd2')

    def test_rfc9369_initial_keys_vector(self):
        key, iv, hp = awg.initial_keys(bytes.fromhex('8394c8f03e515708'), 2)
        self.assertEqual(key.hex(), '8b1a0bc121284290a29e0971b5cd045d')
        self.assertEqual(iv.hex(), '91f73e2351d8fa91660e909f')
        self.assertEqual(hp.hex(), '45b95e15235d6f45a6b19cbcb0294ba9')

    def test_quic_packets_decrypt_and_reassemble_clienthello(self):
        for preset in ('quic', 'quic-fragmented', 'quic-v2'):
            with self.subTest(preset=preset):
                packet = materialize(awg.signature(preset, 'example.com'))
                self.assertEqual(len(packet), 1200)
                version = 2 if preset == 'quic-v2' else 1
                self.assertEqual(int.from_bytes(packet[1:5], 'big'), 0x6b3343cf if version == 2 else 1)
                dcid = packet[6:6 + packet[5]]
                pos = 6 + packet[5]
                pos += 1 + packet[pos]
                token_length, pos = read_varint(packet, pos)
                pos += token_length
                length, pn_offset = read_varint(packet, pos)
                self.assertEqual(length, len(packet) - pn_offset)
                key, iv, hp = awg.initial_keys(dcid, version)
                cipher = Cipher(algorithms.AES(hp), modes.ECB()).encryptor()
                mask = cipher.update(packet[pn_offset + 4:pn_offset + 20]) + cipher.finalize()
                header = bytearray(packet[:pn_offset + 4])
                header[0] ^= mask[0] & 15
                self.assertEqual(header[0], 0xd3 if version == 2 else 0xc3)
                for i in range(4):
                    header[pn_offset + i] ^= mask[1 + i]
                self.assertEqual(header[pn_offset:], b'\0' * 4)
                plain = AESGCM(key).decrypt(iv, packet[pn_offset + 4:], bytes(header))
                pos, parts = 0, []
                while plain[pos] != 0:
                    self.assertEqual(plain[pos], 6)
                    offset, pos = read_varint(plain, pos + 1)
                    size, pos = read_varint(plain, pos)
                    parts.append((offset, plain[pos:pos + size]))
                    pos += size
                self.assertEqual(len(parts), 2 if preset == 'quic-fragmented' else 1)
                if len(parts) == 2:
                    self.assertGreater(parts[0][0], parts[1][0])
                hello = b''.join(part for _, part in sorted(parts))
                self.assertEqual(hello[0], 1)
                self.assertEqual(int.from_bytes(hello[1:4], 'big'), len(hello) - 4)
                self.assertIn(b'example.com', hello)
                self.assertIn(b'\0\x10\0\5\0\3\2h3', hello)
                if version == 2:
                    # RFC 9368: chosen version must also appear in available versions.
                    self.assertIn(b'\x11\x0c' + struct.pack('!III', 0x6b3343cf, 0x6b3343cf, 1), hello)
                self.assertTrue(all(value == 0 for value in plain[pos:]))

    def test_dns_stun_and_dtls_lengths(self):
        for preset, qtype in (('dns-a', 1), ('dns-aaaa', 28), ('dns-https', 65)):
            packet = materialize(awg.signature(preset, 'example.com'))
            self.assertEqual(struct.unpack('!6H', packet[:12])[1:], (256, 1, 0, 0, 0))
            self.assertEqual(packet[12:-4], b'\7example\3com\0')
            self.assertEqual(struct.unpack('!HH', packet[-4:]), (qtype, 1))
        stun = materialize(awg.signature('stun', 'example.com'))
        self.assertEqual(len(stun), 20)
        self.assertEqual(stun[:8], bytes.fromhex('000100002112a442'))
        dtls = materialize(awg.signature('dtls', 'example.com'))
        self.assertEqual(int.from_bytes(dtls[11:13], 'big'), len(dtls) - 13)
        self.assertEqual(int.from_bytes(dtls[14:17], 'big'), len(dtls) - 25)
        self.assertEqual(dtls[22:25], dtls[14:17])
        self.assertEqual(dtls[25:27], b'\xfe\xfd')

    def test_preserves_server_settings_and_has_fresh_identifiers(self):
        base = {'Jc': '6', 'S1': '64', 'H1': '12345', 'I1': 'old', 'I2': 'old2'}
        self.assertEqual(awg.client_profile(base, 'server', ''), base)
        for preset in awg.PRESET_IDS[1:]:
            profile = awg.client_profile(base, preset, 'example.com')
            self.assertEqual({k: profile[k] for k in ('Jc', 'S1', 'H1')}, {k: base[k] for k in ('Jc', 'S1', 'H1')})
            self.assertNotIn('I2', profile)
            self.assertGreater(len(materialize(profile['I1'])), 0)
        self.assertEqual(base['I2'], 'old2')
        self.assertNotEqual(awg.signature('quic', 'example.com'), awg.signature('quic', 'example.com'))
        self.assertNotEqual(awg.signature('sip', 'example.com'), awg.signature('sip', 'example.com'))

    def test_domain_validation(self):
        self.assertEqual(awg.normalize_domain(' Example.COM. '), 'example.com')
        for domain in ('127.0.0.1', 'https://example.com', 'a', '-bad.example', 'a.' + 'b' * 64):
            with self.assertRaises((ValueError, UnicodeError)):
                awg.client_profile({}, 'quic', domain)
        with self.assertRaises(ValueError):
            awg.client_profile({}, 'not-a-preset', 'example.com')


if __name__ == '__main__':
    unittest.main()
