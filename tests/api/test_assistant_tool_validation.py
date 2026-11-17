"""Reject malformed model calls before MCP/HTTP, without echoing input values."""
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from tests.api import test_assistant_control as fixtures
import assistant_control as control

runner = fixtures.runner
SETTINGS = {'modules': {'ai-agent': True}, 'accounts': [], 'mcp': {'vps-controller-mcp': True}, 'control_token': 'fixture',
            'provider_url': 'https://provider.example', 'model': 'fixture', 'api_key': 'secret'}


class ToolValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_error_is_unknown_and_upstream_body_is_not_echoed(self):
        actual = httpx.AsyncClient
        def transport(request): raise httpx.ReadTimeout('PRIVATE URL AND CREDENTIAL', request=request)
        with patch.object(runner.httpx, 'AsyncClient', lambda **kwargs: actual(transport=httpx.MockTransport(transport), **kwargs)):
            result = await runner.vps_call(SETTINGS, 'panel', 'diagnostic_alert_rules', body={})
        self.assertEqual(result['error'], 'tool_timeout')
        self.assertEqual(result['execution_state'], 'unknown')
        self.assertFalse(result['retryable'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_repeated_failed_call_is_not_replayed_and_model_can_explain_the_failure(self):
        feedback = {'error': 'tool_validation_error', 'fields': [{'field': 'expected_revision', 'issue': '64 hex'}], 'executed': False}
        session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(feedback))])))
        tool = SimpleNamespace(name='mihomo_api', description='Prepare', inputSchema={'type': 'object'})
        @asynccontextmanager
        async def connections(*_): yield {'mihomo_api': (session, tool)}
        seen = []
        async def provider(settings, messages, tools, **kwargs):
            seen.append((json.loads(json.dumps(messages)), tools))
            if not tools: return {'content': 'Ревизия имеет неверный формат. Нужно перечитать её и исправить expected_revision.'}
            return {'tool_calls': [{'id': str(len(seen)), 'type': 'function', 'function': {'name': 'mihomo_api', 'arguments': '{}'}}]}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = await runner.chat(SETTINGS, 'panel', 'Создай группу')
        session.call_tool.assert_awaited_once()
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('expected_revision', result['answer'])
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[-1][1], [])
        self.assertEqual(result['diagnostics'][-1]['error_code'], 'repeat_blocked')

    async def test_summary_failure_returns_a_useful_safe_fallback_without_repeating_tools(self):
        session = SimpleNamespace(call_tool=AsyncMock(side_effect=TimeoutError('PRIVATE CREDENTIAL')))
        tool = SimpleNamespace(name='mihomo_api', description='Prepare', inputSchema={'type': 'object'})
        @asynccontextmanager
        async def connections(*_): yield {'mihomo_api': (session, tool)}
        async def provider(settings, messages, tools, **kwargs):
            if not tools: raise RuntimeError('PRIVATE PROVIDER ERROR')
            return {'tool_calls': [{'id': 'call', 'type': 'function', 'function': {'name': 'mihomo_api', 'arguments': '{}'}}]}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider), patch.object(runner, 'MAX_ROUNDS', 1):
            result = await runner.chat(SETTINGS, 'panel', 'Создай группу')
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('не подтверждена', result['answer'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        session.call_tool.assert_awaited_once()

    def test_packaged_http_contract_matches_api_model(self):
        self.assertEqual(runner.control_tool_schema(), control.ToolCall.model_json_schema())

    async def test_bad_envelopes_never_open_an_http_client(self):
        with patch.object(runner.httpx, 'AsyncClient') as client:
            for name, arguments in [('shell', {}), ('diagnostic_alert_rules', {'body': None}),
                                    ('vps_logs', {'lines': True}), ('vps_logs', {'lines': 60.0}), ('vps_service_action', {'action': 'reboot'}),
                                    ('vps_operation', {'operation_id': 'secret-token'}), ('vps_overview', {'tool_name': 'secret-token'})]:
                with self.subTest(name=name, arguments=arguments):
                    result = await runner.vps_call(SETTINGS, 'panel', name, **arguments)
                    self.assertEqual(result['error'], 'tool_validation_error')
                    self.assertFalse(result['executed'])
                    self.assertNotIn('secret-token', json.dumps(result))
            client.assert_not_called()

    async def test_valid_request_is_sent_once_without_coercion(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'ok': True})
        client = httpx.AsyncClient
        with patch.object(runner.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            self.assertEqual(await runner.vps_call(SETTINGS, 'panel', 'diagnostic_alert_rules', method='GET', body={}), {'ok': True})
        self.assertEqual(requests, [{'name': 'diagnostic_alert_rules', 'method': 'GET', 'body': {}}])

    async def test_server_validation_feedback_excludes_rejected_values_and_is_not_replayed(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(422, json={'detail': [{'loc': ['body', 'body', 'SECRET'], 'msg': 'SECRET', 'input': 'SECRET', 'ctx': {'secret': 'SECRET'}}]})
        client = httpx.AsyncClient
        with patch.object(runner.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            result = await runner.vps_call(SETTINGS, 'panel', 'diagnostic_alert_rules', body={})
        self.assertEqual(len(requests), 1)
        self.assertEqual(result['source'], 'api')
        self.assertFalse(result['retryable'])
        self.assertNotIn('SECRET', json.dumps(result))

    async def test_business_rejection_does_not_claim_that_no_action_was_executed(self):
        client = httpx.AsyncClient
        with patch.object(runner.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(lambda request: httpx.Response(422, json={'detail': 'business rejection'})), **kwargs)):
            result = await runner.vps_call(SETTINGS, 'panel', 'diagnostic_alert_rules', body={})
        self.assertEqual(result['execution_state'], 'unknown')
        self.assertNotIn('executed', result)
        self.assertFalse(result['retryable'])

    async def test_model_receives_field_feedback_then_can_issue_one_corrected_call(self):
        schema = {'type': 'object', 'properties': {'service': {'type': 'string'}, 'action': {'enum': ['restart']}},
                  'required': ['service', 'action'], 'additionalProperties': False}
        for invalid in ('{"action":"restart"}', '["SECRET"]', '{"service":"api","action":"SECRET"}', 'not JSON SECRET'):
            session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type='text', text='Prepared, confirmation required')])) )
            tool = SimpleNamespace(name='vps_service_action', description='Prepare action', inputSchema=schema)
            @asynccontextmanager
            async def connections(*_): yield {'vps_service_action': (session, tool)}
            seen = []
            async def provider(settings, messages, tools):
                seen.append(json.loads(json.dumps(messages)))
                if len(seen) <= 2:
                    return {'tool_calls': [{'id': str(len(seen)), 'type': 'function', 'function': {'name': 'vps_service_action',
                            'arguments': invalid if len(seen) == 1 else '{"service":"api","action":"restart"}'}}]}
                return {'content': 'Подтвердите действие'}
            with self.subTest(invalid=invalid), patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
                await runner.chat(SETTINGS, 'panel', 'Prepare restart')
            feedback = json.loads(seen[1][-1]['content'])
            self.assertEqual(feedback['error'], 'tool_validation_error')
            self.assertNotIn('SECRET', json.dumps(feedback))
            self.assertFalse(feedback['executed'])
            session.call_tool.assert_awaited_once_with('vps_service_action', {'service': 'api', 'action': 'restart'})
