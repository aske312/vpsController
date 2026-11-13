import asyncio
import json
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
    async def test_structured_extraction_resolves_links_and_ignores_executable_content(self):
        html = b'<title>Docs</title><meta name="description" content="About docs"><h1>Heading</h1><script><h2>hidden</h2></script><a href="/guide">Guide</a><a href="javascript:alert(1)">bad</a><table><tr><th>Name</th><td>Value</td></tr></table>'
        with patch.object(runner, 'web_get', new=AsyncMock(return_value=('https://example.com/docs', 'text/html', html, 'utf-8'))):
            headings = await runner.internet_extract('https://example.com/docs')
            links = await runner.internet_extract('https://example.com/docs', 'links')
            tables = await runner.internet_extract('https://example.com/docs', 'tables')
        self.assertEqual(headings['items'], [{'level': 1, 'text': 'Heading '}])
        self.assertEqual(links['items'], [{'url': 'https://example.com/guide', 'text': 'Guide '}])
        self.assertEqual(tables['items'], [[['Name ', 'Value ']]])
        self.assertTrue(tables['untrusted_source'])
        with self.assertRaises(ValueError): await runner.internet_extract('https://example.com', 'shell')

    async def test_extract_permission_and_read_limits_apply_to_actual_tool_calls(self):
        settings = {'accounts': [], 'mcp': {}, 'web_enabled': True, 'web_extract_enabled': False, 'web_read_chars': 2000}
        tools = runner.native_connections(settings, 'panel')
        self.assertNotIn('web_extract', tools)
        with self.assertRaises(PermissionError): await runner.NativeTools(settings, 'panel').call_tool('web_extract', {'url': 'https://example.com'})
        with patch.object(runner, 'internet_read', new=AsyncMock(return_value={})) as read:
            await runner.NativeTools(settings, 'panel').call_tool('web_read', {'url':'https://example.com','focus':'topic'})
            read.assert_awaited_once_with('https://example.com', 'topic', 2000)

    async def test_research_obeys_source_limit_and_extraction_discloses_truncation(self):
        hits = {'results': [{'url':'https://example.com/' + str(index)} for index in range(5)]}
        with patch.object(runner, 'internet_search', new=AsyncMock(return_value=hits)), patch.object(runner, 'internet_read', new=AsyncMock(return_value={'url':'https://example.com','text':'source','truncated':False})) as read:
            result = await runner.internet_research('topic', max_sources=2, max_chars=2000)
        self.assertEqual(len(result['sources']), 2)
        self.assertEqual(read.await_count, 2)
        html = ('<h1>' + 'x' * 300 + '</h1>') * 60
        with patch.object(runner, 'web_get', new=AsyncMock(return_value=('https://example.com', 'text/html', html.encode(), 'utf-8'))):
            result = await runner.internet_extract('https://example.com', max_chars=2000)
        self.assertTrue(result['truncated'])
        self.assertLess(len(json.dumps(result['items'], ensure_ascii=False)), 2001)

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

    async def test_site_scope_filters_results_and_research_distinguishes_read_pages(self):
        xml = b'<rss><channel><item><link>https://fake-example.com/no</link></item><item><link>https://docs.example.com/yes</link></item></channel></rss>'
        with patch.object(runner, 'web_get', new=AsyncMock(return_value=('', 'text/xml', xml, 'utf-8'))) as get:
            result = await runner.internet_search('product docs', ['example.com'])
        self.assertEqual([item['url'] for item in result['results']], ['https://docs.example.com/yes'])
        self.assertTrue(result['snippets_only'])
        self.assertIn('site%3Aexample.com', get.call_args.args[0])
        for sites in (['example.com/path'], ['https://example.com'], ['localhost'], ['example.com OR site:evil.com']):
            with self.assertRaises(ValueError): runner.search_domains(sites)
        with patch.object(runner, 'internet_search', new=AsyncMock(return_value={'results': [{'url': 'https://example.com/one'}, {'url': 'https://example.com/two'}]})), patch.object(runner, 'internet_read', new=AsyncMock(side_effect=[{'url': 'https://example.com/one', 'text': 'Verified source text', 'truncated': False}, RuntimeError('HTTP 403')])):
            result = await runner.internet_research('question')
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['text'], 'Verified source text')
        self.assertEqual(result['unavailable'][0]['error'], 'HTTP 403')

    async def test_native_tools_follow_toggles_and_explicit_memory_write_permits(self):
        settings = {'web_enabled': True, 'accounts': [], 'mcp': {}, 'personalization_enabled': True}
        tools = runner.native_connections(settings, 'panel')
        self.assertIn('web_research', tools)
        self.assertIn('context_read', tools)
        self.assertNotIn('context_remember', tools)
        settings['context_permits'] = {'remember': 'signed-proof'}
        self.assertIn('context_remember', runner.native_connections(settings, 'panel'))
        settings.update(web_enabled=False, personalization_enabled=False)
        self.assertEqual(runner.native_connections(settings, 'panel'), {})

    async def test_html_search_decodes_results_and_keeps_site_scope(self):
        html = b'<html><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.com%2Fpage">Official <b>documentation</b></a><div class="result__snippet">Useful <b>summary</b></div></html>'
        with patch.object(runner, 'web_get', new=AsyncMock(return_value=('', 'text/html', html, 'utf-8'))):
            result = await runner.internet_search('documentation', ['example.com'])
        self.assertEqual(result['provider'], 'DuckDuckGo')
        self.assertEqual(result['results'][0]['url'], 'https://docs.example.com/page')
        self.assertEqual(result['results'][0]['title'], 'Official documentation')
        self.assertEqual(result['results'][0]['description'], 'Useful summary')
