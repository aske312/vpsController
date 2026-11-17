"""Exercise real httpx/httpcore tracing without contacting a provider."""
import asyncio
import json
import socket
import ssl
import unittest
from unittest.mock import AsyncMock, patch

import httpcore
import httpx
from tests.api.support import load_module
import assistant_trace as trace

runner = load_module('provider_transport_runner', 'protocol-images/ai-agent/runner.py')
SETTINGS = {'provider_url': 'https://provider.example/private', 'api_key': 'PRIVATE_KEY', 'model': 'test'}
DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


class Stream:
    def __init__(self, chunks, tls_error=None): self.chunks, self.tls_error = iter(chunks), tls_error
    async def read(self, *_args, **_kwargs):
        value = next(self.chunks, b'')
        if isinstance(value, Exception): raise value
        return value
    async def write(self, *_args, **_kwargs): pass
    async def aclose(self): pass
    async def start_tls(self, *_args, **_kwargs):
        if self.tls_error: raise self.tls_error
        return self
    def get_extra_info(self, _): return None


class Backend:
    def __init__(self, stream): self.stream, self.calls = stream, 0
    async def connect_tcp(self, *_args, **_kwargs):
        self.calls += 1
        return self.stream


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, stream, settings=None):
        transport = httpx.AsyncHTTPTransport()
        backend = Backend(stream)
        transport._pool._network_backend = backend
        client = httpx.AsyncClient
        events = []
        def factory(**kwargs): return client(transport=transport, **kwargs)
        def record(stage, **values): events.append(trace.safe_event({'stage': stage, **values}))
        with patch.object(runner.httpx, 'AsyncClient', factory), patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(return_value=DNS)), patch.object(runner, 'diagnostic', side_effect=record):
            try:
                result = await runner.provider_request(settings or SETTINGS, [{'role': 'user', 'content': 'PRIVATE_PROMPT'}], [])
            except Exception as error:
                return None, error, events, backend.calls
        return result, None, events, backend.calls

    async def test_read_timeout_after_tls_success_records_phase_chain_and_status(self):
        error = httpcore.ReadTimeout('PRIVATE_URL_KEY')
        error.__context__ = ssl.SSLWantReadError(ssl.SSL_ERROR_WANT_READ, 'PRIVATE_CERT')
        _, failure, events, calls = await self.request(Stream([b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nabc', error]))
        self.assertIsInstance(failure, httpx.ReadTimeout)
        self.assertEqual(runner.error_code(failure), 'provider_read_timeout')
        evidence = events[-1]
        self.assertEqual(evidence['stage'], 'provider_error')
        self.assertEqual(evidence['transport_phase'], 'response_body')
        self.assertEqual(evidence['transport_reason'], 'timeout')
        self.assertEqual(evidence['http_status'], 200)
        self.assertIn('ReadTimeout', evidence['exception_types'])
        self.assertTrue(any(row.get('transport_phase') == 'tls_handshake' and row['status'] == 'success' for row in events))
        self.assertGreaterEqual(evidence['duration_ms'], 0)
        self.assertEqual(calls, 1)
        self.assertNotIn('PRIVATE', json.dumps(events) + runner.safe_error(failure))

    async def test_certificate_failure_records_verify_code_and_never_logs_certificate_text(self):
        error = ssl.SSLCertVerificationError(1, 'PRIVATE certificate hostname path')
        error.verify_code = 10
        error.reason = 'CERTIFICATE_VERIFY_FAILED'
        _, failure, events, calls = await self.request(Stream([], tls_error=error))
        evidence = events[-1]
        self.assertEqual(runner.error_code(failure), 'provider_certificate_error')
        self.assertEqual(evidence['transport_phase'], 'tls_handshake')
        self.assertEqual(evidence['transport_reason'], 'certificate_verification')
        self.assertEqual(evidence['certificate_verify_code'], 10)
        self.assertEqual(evidence['tls_reason'], 'CERTIFICATE_VERIFY_FAILED')
        self.assertNotIn('os_errno', evidence)
        self.assertEqual(calls, 1)
        self.assertNotIn('PRIVATE', json.dumps(events) + runner.safe_error(failure))

    async def test_dns_failure_is_recorded_before_http_client_is_opened(self):
        events = []
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', new=AsyncMock(side_effect=socket.gaierror(-2, 'PRIVATE_HOST'))), patch.object(runner.httpx, 'AsyncClient') as client, patch.object(runner, 'diagnostic', side_effect=lambda stage, **values: events.append(trace.safe_event({'stage': stage, **values}))):
            with self.assertRaises(socket.gaierror): await runner.provider_request(SETTINGS, [], [])
        client.assert_not_called()
        self.assertEqual(events[-1]['transport_phase'], 'dns')
        self.assertEqual(events[-1]['error_code'], 'provider_dns_error')
        self.assertNotIn('PRIVATE', json.dumps(events))

    async def test_timeout_waiting_for_headers_and_tls_eof_during_stream_are_distinct(self):
        _, failure, events, _ = await self.request(Stream([httpcore.ReadTimeout('PRIVATE')]))
        self.assertEqual(events[-1]['transport_phase'], 'response_headers')
        self.assertEqual(runner.error_code(failure), 'provider_read_timeout')
        self.assertNotIn('http_status', events[-1])
        eof = httpcore.ReadError('PRIVATE')
        eof.__cause__ = ssl.SSLEOFError(8, 'PRIVATE')
        _, failure, events, calls = await self.request(Stream([b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\ndata: ', eof]), {**SETTINGS, 'streaming': True})
        self.assertEqual(events[-1]['transport_phase'], 'response_body')
        self.assertEqual(events[-1]['transport_reason'], 'unexpected_eof')
        self.assertEqual(events[-1]['http_status'], 200)
        self.assertEqual(runner.error_code(failure), 'provider_tls_error')
        self.assertEqual(calls, 1)
        self.assertNotIn('PRIVATE', json.dumps(events) + runner.safe_error(failure))

    async def test_http_rejection_and_invalid_json_preserve_http_status(self):
        for status, body, reason in [(503, b'PRIVATE', 'http_error'), (200, b'PRIVATE', 'invalid_response')]:
            _, failure, events, calls = await self.request(Stream([f'HTTP/1.1 {status} Status\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body]))
            self.assertIsNotNone(failure)
            self.assertEqual(events[-1]['transport_reason'], reason)
            self.assertEqual(events[-1]['http_status'], status)
            self.assertEqual(calls, 1)
            self.assertNotIn('PRIVATE', json.dumps(events) + runner.safe_error(failure))

    async def test_reset_evidence_is_bounded_and_suppressed_context_is_not_a_tls_failure(self):
        error = httpx.ReadError('PRIVATE')
        error.__cause__ = ConnectionResetError(104, 'PRIVATE')
        evidence = runner.transport_evidence(error)
        self.assertEqual(evidence['transport_reason'], 'connection_reset')
        self.assertEqual(evidence['os_errno'], 104)
        error.__cause__ = None
        error.__context__ = ssl.SSLError('PRIVATE')
        error.__suppress_context__ = True
        self.assertFalse(runner.tls_failure(error))
        self.assertEqual(runner.error_code(error), 'provider_transport_error')

    async def test_success_records_completed_stages_without_request_or_response_values(self):
        body = json.dumps({'choices': [{'message': {'content': 'PRIVATE_ANSWER'}}]}).encode()
        result, failure, events, calls = await self.request(Stream([b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nX-Private: PRIVATE_HEADER\r\n\r\n' + body]))
        self.assertIsNone(failure)
        self.assertEqual(result['content'], 'PRIVATE_ANSWER')
        self.assertEqual(calls, 1)
        phases = {row.get('transport_phase') for row in events if row.get('status') == 'success'}
        self.assertTrue({'dns', 'tcp_connect', 'tls_handshake', 'request_headers', 'request_body', 'response_headers', 'response_body', 'response_parse'} <= phases)
        self.assertNotIn('PRIVATE', json.dumps(events))

    async def test_trace_filter_rejects_injected_transport_fields_and_cyclic_causes_are_bounded(self):
        event = trace.safe_event({'stage': 'provider_error', 'transport_phase': 'PRIVATE_HOST', 'transport_reason': 'PRIVATE_KEY',
                                 'exception_types': ['ReadTimeout', 'PRIVATE_KEY'], 'certificate_verify_code': 'PRIVATE', 'os_errno': -1,
                                 'headers': {'Authorization': 'PRIVATE'}, 'exception': 'PRIVATE'})
        self.assertEqual(event, {'stage': 'provider_error', 'exception_types': ['ReadTimeout']})
        error = httpx.ReadTimeout('PRIVATE')
        error.__cause__ = error
        self.assertEqual(runner.transport_evidence(error)['exception_types'], ['ReadTimeout'])
