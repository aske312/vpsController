import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

from tests.api.support import load_module

runner = load_module('assistant_web_runtime', 'protocol-images/ai-agent/runner.py')


class Response:
    def __init__(self, body=b'', status=200, mime='text/html', location=None):
        self.status_code = status
        self.headers = {'content-type': mime, **({'location': location} if location else {})}
        self.encoding = 'utf-8'
        self.body = body

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def aiter_bytes(self, chunk_size=8192):
        for at in range(0, len(self.body), chunk_size): yield self.body[at:at + chunk_size]


class Client:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    def stream(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        return self.responses.pop(0)


def dns(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_mixed_dns_and_non_https_targets_are_blocked_before_http(self):
        for url in ('http://example.com', 'https://user:password@example.com', 'https://example.com:8000', 'https://localhost', 'file:///etc/passwd'):
            with self.assertRaises(ValueError): await runner.web_get(url)
        for addresses in (dns('127.0.0.1'), dns('169.254.169.254'), dns('10.0.0.1'), dns('93.184.216.34') + dns('192.168.1.1')):
            with patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(return_value=addresses)), patch.object(runner.httpx, 'AsyncClient') as client:
                with self.assertRaises(ValueError): await runner.web_get('https://example.com')
                client.assert_not_called()

    async def test_html_is_text_only_dns_is_pinned_and_credentials_are_absent(self):
        client = Client([Response(b'<title>Source</title><h1>News</h1><script>secret_script()</script><style>hidden</style><p>Verified &amp; clear</p>')])
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(return_value=dns('93.184.216.34'))), patch.object(runner.httpx, 'AsyncClient', return_value=client) as factory:
            result = await runner.internet_read('https://example.com/news')
        self.assertEqual(result['title'], 'Source')
        self.assertIn('Verified & clear', result['text'])
        self.assertNotIn('secret_script', result['text'])
        self.assertNotIn('hidden', result['text'])
        args, kwargs = client.requests[0]
        self.assertEqual(args, ('GET', 'https://93.184.216.34/news'))
        self.assertEqual(kwargs['extensions']['sni_hostname'], 'example.com')
        self.assertEqual(kwargs['headers']['Host'], 'example.com')
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertNotIn('Cookie', kwargs['headers'])
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertTrue(result['untrusted_source'])

    async def test_redirect_is_revalidated_and_large_downloads_are_rejected(self):
        client = Client([Response(status=302, location='https://169.254.169.254/latest')])
        async def resolve(host, *_args, **_kwargs):
            return dns('93.184.216.34' if host == 'example.com' else '169.254.169.254')
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', side_effect=resolve), patch.object(runner.httpx, 'AsyncClient', return_value=client):
            with self.assertRaises(ValueError): await runner.web_get('https://example.com')
        self.assertEqual(len(client.requests), 1)
        client = Client([Response(b'x' * (runner.WEB_LIMIT + 1))])
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(return_value=dns('93.184.216.34'))), patch.object(runner.httpx, 'AsyncClient', return_value=client):
            with self.assertRaises(ValueError): await runner.web_get('https://example.com')

    async def test_search_returns_sources_and_web_can_be_disabled_independently(self):
        xml = b'<rss><channel><item><title>Docs</title><link>https://example.com/docs</link><description>Official docs</description></item></channel></rss>'
        with patch.object(runner, 'web_get', new=AsyncMock(return_value=('https://www.bing.com', 'text/xml', xml, 'utf-8'))):
            result = await runner.internet_search('documentation')
        self.assertEqual(result['results'][0]['url'], 'https://example.com/docs')
        settings = {'web_enabled': True, 'mcp': {}, 'accounts': [{'telegram_id': 11, 'bot_chat_access': True}]}
        self.assertTrue(runner.allowed(settings, '11', 'web'))
        self.assertFalse(runner.allowed(settings, '22', 'web'))
        settings['web_enabled'] = False
        self.assertFalse(runner.allowed(settings, '11', 'web'))
        self.assertFalse(runner.allowed(settings, 'panel', 'web'))
