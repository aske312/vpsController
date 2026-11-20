from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.api.support import api
import assistant_trace as trace
import assistant_usage as usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)

    def capture(self, identity, actor='11', channel='bot', retry=False, fail=False):
        with trace.capture(self.env, identity, actor, channel):
            trace.record({'stage': 'request_started', 'prompt': 'PRIVATE PROMPT'})
            trace.record({'stage': 'runtime_started', 'model_id': 'model', 'provider_id': 'c' * 32})
            trace.record({'stage': 'model_start', 'round': 1})
            if retry: trace.record({'stage': 'provider_retry', 'attempt': 2})
            trace.record({'stage': 'model_result', 'round': 1, 'prompt_tokens': 100, 'completion_tokens': 20})
            if fail:
                trace.record({'stage': 'model_start', 'round': 2})
                trace.record({'stage': 'model_error', 'error_code': 'provider_read_timeout'})
            trace.record({'stage': 'request_finished', 'status': 'incomplete' if fail else 'success'})

    def test_one_question_counts_model_rounds_and_partial_tokens_without_double_counting(self):
        self.capture('a' * 32, retry=True, fail=True)
        result = usage.report(self.env)['rows'][0]
        self.assertEqual(result['requests'], 1)
        self.assertEqual(result['model_calls'], 3)
        self.assertEqual(result['retries'], 1)
        self.assertEqual(result['incomplete'], 1)
        self.assertEqual(result['prompt_tokens'], 100)
        self.assertEqual(result['completion_tokens'], 20)
        self.assertEqual(result['unmeasured_requests'], 1)
        usage.store_trace(self.env, trace.directory(self.env) / ('a' * 32 + '.jsonl'))
        self.assertEqual(usage.report(self.env)['rows'][0], result)
        self.assertNotIn(b'PRIVATE PROMPT', usage.path(self.env).read_bytes())

    def test_users_panel_and_models_are_separate_and_stats_survive_trace_cleanup(self):
        self.capture('a' * 32)
        self.capture('b' * 32, actor='22')
        self.capture('d' * 32, actor='panel', channel='panel')
        before = usage.report(self.env)
        self.assertEqual({r['actor'] for r in before['rows']}, {'11', '22', 'panel'})
        for file in trace.directory(self.env).glob('*.jsonl'): file.unlink()
        self.assertEqual(usage.report(self.env)['rows'], before['rows'])
        self.assertFalse(before['cost_available'])

    def test_active_usage_is_checkpointed_before_capture_finishes(self):
        with trace.capture(self.env, 'a' * 32, '11', 'bot'):
            trace.record({'stage': 'model_start', 'round': 1})
            trace.record({'stage': 'model_result', 'round': 1, 'prompt_tokens': 50, 'completion_tokens': 0})
            row = usage.report(self.env)['rows'][0]
            self.assertEqual(row['prompt_tokens'], 50)
            self.assertEqual(row['unknown'], 1)

    def test_accounting_continues_when_diagnostic_trace_is_full(self):
        with patch.object(trace, 'MAX_BYTES', 256), trace.capture(self.env, 'a' * 32, '11', 'bot'):
            for index in range(3):
                trace.record({'stage': 'model_start', 'round': index + 1})
                trace.record({'stage': 'model_result', 'round': index + 1, 'prompt_tokens': 100, 'completion_tokens': 20})
            trace.record({'stage': 'request_finished', 'status': 'success'})
        row = usage.report(self.env)['rows'][0]
        self.assertEqual(row['model_calls'], 3)
        self.assertEqual(row['prompt_tokens'], 300)
        self.assertEqual(row['unmeasured_requests'], 0)

    def test_counters_survive_a_diagnostic_write_failure_and_include_final_summary(self):
        with patch.object(trace.os, 'open', side_effect=PermissionError('diagnostics only')):
            with trace.capture(self.env, 'a' * 32, '11', 'bot'):
                trace.record({'stage': 'model_start', 'round': 1})
                trace.record({'stage': 'model_result', 'round': 1, 'prompt_tokens': 50, 'completion_tokens': 10})
                trace.record({'stage': 'summary_start'})
                trace.record({'stage': 'summary_result', 'prompt_tokens': 100, 'completion_tokens': 20})
                trace.record({'stage': 'request_finished', 'status': 'incomplete'})
        row = usage.report(self.env)['rows'][0]
        self.assertEqual(row['requests'], 1)
        self.assertEqual(row['model_calls'], 2)
        self.assertEqual(row['prompt_tokens'], 150)
        self.assertEqual(row['completion_tokens'], 30)
        self.assertEqual(row['unmeasured_requests'], 0)

    def test_backfill_cannot_replace_a_newer_checkpoint_and_periods_are_applied(self):
        self.capture('a' * 32)
        file = trace.directory(self.env) / ('a' * 32 + '.jsonl')
        earlier = file.read_text(encoding='utf-8').splitlines()[:3]
        old = self.root / 'old.jsonl'
        old.write_text('\n'.join(earlier), encoding='utf-8')
        usage.store_trace(self.env, old)
        self.assertEqual(usage.report(self.env)['rows'][0]['completed'], 1)
        with patch.object(usage.time, 'time', return_value=time.time() + 2 * 86400):
            self.assertEqual(usage.report(self.env, 1)['rows'], [])
            self.assertEqual(len(usage.report(self.env, 7)['rows']), 1)

    def test_usage_endpoint_requires_owner_authentication_and_validates_period(self):
        with (patch.object(api, 'DATA_DIR', self.root), patch.object(api, 'ADMIN_USER', 'admin'),
              patch.object(api, 'ADMIN_PASSWORD', 'fixture'), TestClient(api.app) as client):
            self.assertEqual(client.get('/api/assistant/usage').status_code, 401)
            self.assertEqual(client.get('/api/assistant/usage?days=91', auth=('admin', 'fixture')).status_code, 422)
            self.assertEqual(client.get('/api/assistant/usage', auth=('admin', 'fixture')).status_code, 200)
