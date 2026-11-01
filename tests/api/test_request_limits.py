import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api, manager
from request_limits import MAX_REQUEST_BYTES, RequestBodyLimit


class RequestLimitTests(unittest.TestCase):
    def invoke(self, chunks, headers=(), limit=8, disconnected=False):
        calls, sent = [], []
        messages = [{'type': 'http.request', 'body': part, 'more_body': index < len(chunks) - 1}
                    for index, part in enumerate(chunks)]
        if disconnected:
            messages = [{'type': 'http.disconnect'}]

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        async def endpoint(scope, receive, send):
            calls.append(await receive())

        asyncio.run(RequestBodyLimit(endpoint, max_bytes=limit)(
            {'type': 'http', 'headers': headers, 'method': 'POST'}, receive, send))
        return calls, sent, messages

    def test_length_rejected_before_reading_and_chunked_payload_never_reaches_endpoint(self):
        calls, sent, remaining = self.invoke([b'ignored'], [(b'content-length', b'9')])
        self.assertEqual(calls, [])
        self.assertEqual(sent[0]['status'], 413)
        self.assertTrue(remaining)
        for headers in ([], [(b'content-length', b'1')]):
            calls, sent, _ = self.invoke([b'1234', b'56789'], headers)
            self.assertEqual(calls, [])
            self.assertEqual(sent[0]['status'], 413)
        calls, sent, _ = self.invoke([b''], [(b'content-length', b'9' * 5000)])
        self.assertEqual(calls, [])
        self.assertEqual(sent[0]['status'], 413)

    def test_exact_boundary_and_empty_body_preserve_original_payload(self):
        for chunks, expected in (([b'1234', b'5678'], b'12345678'), ([b''], b'')):
            calls, sent, _ = self.invoke(chunks)
            self.assertEqual(calls[0]['body'], expected)
            self.assertEqual(sent, [])

    def test_invalid_length_and_disconnect_do_not_invoke_endpoint(self):
        for value in (b'-1', b'NaN', b''):
            calls, sent, _ = self.invoke([b''], [(b'content-length', value)])
            self.assertEqual(calls, [])
            self.assertEqual(sent[0]['status'], 400)
        calls, sent, _ = self.invoke([], disconnected=True)
        self.assertEqual((calls, sent), ([], []))

    def test_both_real_apps_reject_oversized_unauthenticated_json_without_parsing(self):
        payload = b'"' + b'x' * MAX_REQUEST_BYTES + b'"'
        for app, method, route in ((api.app, 'PUT', '/api/security/admin-password'), (manager.app, 'POST', '/api/mihomo/profiles')):
            with self.subTest(route=route), patch('starlette.requests.json.loads', side_effect=AssertionError('Must not parse')):
                response = TestClient(app).request(method, route, content=payload, headers={'content-type': 'application/json'})
                self.assertEqual(response.status_code, 413)

    def test_rejection_preserves_cors_and_ordinary_request_still_requires_auth(self):
        client = TestClient(api.app)
        response = client.post('/api/security/admin-password', content=b'',
            headers={'content-length': str(MAX_REQUEST_BYTES + 1), 'origin': api.CORS_ORIGINS[0]})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers['access-control-allow-origin'], api.CORS_ORIGINS[0])
        self.assertEqual(client.put('/api/security/admin-password', json={}).status_code, 401)
