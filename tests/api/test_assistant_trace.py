"""Private dialogue evidence survives failures without retaining tool values."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.api.support import api
import assistant_trace as trace
import assistant_control as control
import assistant_runtime as runtime


class AssistantTraceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)
        self.identity = 'a' * 32

    def test_trace_retains_safe_cause_and_shape_without_prompt_values_or_secrets(self):
        with trace.capture(self.env, self.identity, '11', 'bot'):
            trace.record({'stage': 'request_started', 'prompt_chars': 18, 'prompt': 'PRIVATE PROMPT'})
            trace.record({'stage': 'tool_start', 'tool': 'mihomo_api', 'fields': ['values.private_key', 'expected_revision'],
                          'arguments': {'password': 'PRIVATE PASSWORD'}, 'argument_types': {'body.values.structured_policy': 'dict', 'body.values.PRIVATEKEY': 'str'}})
            trace.record({'stage': 'tool_result', 'tool': 'mihomo_api', 'http_status': 422, 'error_code': 'tool_validation_error', 'fields': ['expected_revision'], 'message': 'PRIVATE ERROR INPUT'})
            trace.record({'stage': 'request_finished', 'status': 'incomplete'})
        output = trace.read(self.env, self.identity, '11')
        self.assertTrue(output['complete'])
        self.assertEqual(output['events'][1]['argument_types'], {'body.values.structured_policy': 'dict', 'body.values.*': 'str'})
        self.assertEqual(output['events'][2]['http_status'], 422)
        self.assertNotIn('PRIVATE', json.dumps(output))
        with self.assertRaises(HTTPException) as denied: trace.read(self.env, self.identity, '22')
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(HTTPException): trace.read(self.env, '../settings', 'panel')

    def test_retention_size_and_listing_are_bounded_and_partial_trace_is_visible(self):
        with patch.object(trace, 'MAX_FILES', 2), patch.object(trace, 'MAX_BYTES', 512):
            for identity in ('a' * 32, 'b' * 32, 'c' * 32):
                with trace.capture(self.env, identity, 'panel', 'panel'):
                    for _ in range(10): trace.record({'stage': 'model_start', 'round': 1})
            self.assertEqual(len(list(trace.directory(self.env).glob('*.jsonl'))), 2)
            self.assertTrue(all(file.stat().st_size <= 512 for file in trace.directory(self.env).glob('*.jsonl')))
        self.assertFalse(trace.read(self.env, 'c' * 32)['complete'])
        self.assertTrue(trace.read(self.env, 'c' * 32)['truncated'])
        self.assertEqual(len(trace.listing(self.env)['items']), 2)
        with patch.object(trace.time, 'time', return_value=time.time() + trace.RETENTION + 10):
            self.assertEqual(trace.listing(self.env)['items'], [])

    def test_runtime_diagnostic_events_share_context_and_do_not_replace_final_answer(self):
        script = 'import json\nprint(json.dumps({"event":"diagnostic","stage":"tool_result","tool":"mihomo_api","http_status":422,"fields":["expected_revision"]}),flush=True)\nprint(json.dumps({"ok":True,"result":{"answer":"Ready"}}),flush=True)'
        with trace.capture(self.env, self.identity, 'panel', 'panel'), subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
            result, code = runtime.communicate(process, {})
        self.assertEqual(code, 0)
        self.assertEqual(result['result']['answer'], 'Ready')
        self.assertEqual(trace.read(self.env, self.identity)['events'][0]['tool'], 'mihomo_api')

    def test_request_failure_still_finishes_trace_and_administrator_can_diagnose_it(self):
        with patch.object(api, 'DATA_DIR', self.root), patch.object(control, '_admitted_chat', side_effect=HTTPException(502, 'PRIVATE FAILURE')):
            with self.assertRaises(HTTPException): control.chat_request(api, control.Chat(prompt='PRIVATE QUESTION', request_id=self.identity))
            result = control.control_tool(api, 'ai_dialog_trace', {'body': {'request_id': self.identity}})
        self.assertEqual(result['request_id'], self.identity)
        self.assertEqual(result['events'][-1]['status'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_trace_endpoint_requires_login_and_respects_selected_actor(self):
        with trace.capture(self.env, self.identity, '11', 'panel'):
            trace.record({'stage': 'request_started'})
        with patch.object(api, 'DATA_DIR', self.root), TestClient(api.app) as client:
            self.assertEqual(client.get('/api/assistant/requests/' + self.identity + '/trace').status_code, 401)
            with patch.dict(api.app.dependency_overrides, {api.require_token: lambda: None}):
                self.assertEqual(client.get('/api/assistant/requests/' + self.identity + '/trace').status_code, 200)
                with patch.object(control, 'read', return_value={'accounts': [{'telegram_id': 22, 'role': 'user', 'products': ['assistant']}]}):
                    self.assertEqual(client.get('/api/assistant/requests/' + self.identity + '/trace?telegram_id=22').status_code, 404)
