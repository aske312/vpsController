import json
import socket
import unittest
from unittest.mock import MagicMock, patch

from tests.api import support
import assistant_provider as provider


class ProviderCatalogTests(unittest.TestCase):
    def test_base_urls_preserve_custom_prefixes_and_never_duplicate_v1(self):
        for raw, expected in [('https://api.nyxos.shop/', 'https://api.nyxos.shop/v1'),
                              ('https://api.nyxos.shop/v1/', 'https://api.nyxos.shop/v1'),
                              ('https://example.com/gateway/v2', 'https://example.com/gateway/v2')]:
            with self.subTest(raw=raw): self.assertEqual(provider.base_url(raw), expected)
        for raw in ['http://example.com', 'https://example.com:8443', 'https://u:p@example.com', 'https://example.com/?secret=key']:
            with self.subTest(raw=raw), self.assertRaises(ValueError): provider.base_url(raw)

    def test_catalog_uses_bearer_get_and_only_returns_valid_unique_model_ids(self):
        response = MagicMock(status=200)
        response.read.return_value = json.dumps({'data': [{'id': 'b'}, {'id': 'a'}, {'id': 'b'},
            {'id': 'bad\nID'}, {}, {'id': 123}]}).encode()
        with patch.object(provider, 'PublicHTTPSConnection') as factory:
            connection = factory.return_value
            connection.getresponse.return_value = response
            self.assertEqual(provider.catalog('https://api.nyxos.shop', 'private-key'), ['a', 'b'])
            connection.request.assert_called_once_with('GET', '/v1/models', headers={
                'Authorization': 'Bearer private-key', 'Accept': 'application/json'})
            connection.close.assert_called_once()

    def test_redirects_errors_malformed_and_oversized_catalogs_are_rejected(self):
        for status, body in [(302, b'secret-key'), (401, b'secret-key'), (200, b'not-json'),
                             (200, b'{}'), (200, b'{"data":[]}'), (200, b'x' * (2 * 1024 * 1024 + 1))]:
            with self.subTest(status=status, size=len(body)), patch.object(provider, 'PublicHTTPSConnection') as factory:
                factory.return_value.getresponse.return_value = MagicMock(status=status, read=MagicMock(return_value=body))
                with self.assertRaises(ValueError) as caught: provider.catalog('https://example.com/v1', 'secret-key')
                self.assertNotIn('secret-key', str(caught.exception))
                factory.return_value.close.assert_called_once()

    def test_private_or_mixed_dns_answers_are_rejected_before_opening_socket(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))
        for answers in [[], [private], [public, private]]:
            with self.subTest(answers=answers), patch.object(provider.socket, 'getaddrinfo', return_value=answers), patch.object(provider.socket, 'create_connection') as connect:
                connection = provider.PublicHTTPSConnection('provider.example')
                with self.assertRaises(ValueError): connection.connect()
                connect.assert_not_called()

    def test_public_ip_is_pinned_and_original_hostname_is_used_for_tls(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch.object(provider.socket, 'getaddrinfo', return_value=answers) as dns, patch.object(provider.socket, 'create_connection') as connect:
            connection = provider.PublicHTTPSConnection('provider.example', timeout=15)
            with patch.object(connection._context, 'wrap_socket') as tls:
                connection.connect()
                dns.assert_called_once()
                connect.assert_called_once_with(('8.8.8.8', 443), 15)
                tls.assert_called_once_with(connect.return_value, server_hostname='provider.example')
