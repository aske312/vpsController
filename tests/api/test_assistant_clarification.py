import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.api.support import api, load_module
import assistant_control as control
import assistant_jobs as jobs
import application_operation as operations

runner = load_module('clarification_runner', 'protocol-images/ai-agent/runner.py')
SETTINGS = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1',
            'model': 'test', 'api_key': 'key', 'accounts': [], 'web_enabled': False}


def call(name, args, identity):
    return {'id': identity, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


class ClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_input_can_satisfy_required_tool_choice_without_any_change(self):
        change = SimpleNamespace(call_tool=AsyncMock())
        @asynccontextmanager
        async def connections(*_):
            yield {**runner.native_connections(SETTINGS, 'panel'), 'mihomo_profile_create':
                   (change, SimpleNamespace(name='mihomo_profile_create', description='Create', inputSchema={'type': 'object'}))}
        provider = AsyncMock(return_value={'tool_calls': [
            call('mihomo_profile_create', {}, 'write-first'),
            call('ask_user', {'question': 'Для какого пользователя создать подписку?', 'options': ['Для меня', 'Для другого пользователя']}, 'ask')]})
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = await runner.chat(SETTINGS, 'panel', 'Создай подписку')
        self.assertEqual(provider.call_args.kwargs['tool_choice'], 'required')
        provider.assert_awaited_once()
        change.call_tool.assert_not_awaited()
        self.assertEqual(result['tools_used'], ['ask_user'])
        self.assertTrue(result['clarification']['awaiting_input'])
        self.assertIn('1. Для меня', result['answer'])
        self.assertNotIn('status', result)
        self.assertNotIn('ИИ не вызвал', result['answer'])

    async def test_clarification_cannot_hide_an_unverified_execution_claim(self):
        session = runner.NativeTools(SETTINGS, 'panel')
        for question in ('Я создала профиль. Какой срок указать?', 'Операция выполнена. Что дальше?'):
            with self.subTest(question=question), self.assertRaises(runner.ToolValidationError):
                await session.call_tool('ask_user', {'question': question})
        with self.assertRaises(runner.ToolValidationError):
            await session.call_tool('ask_user', {'question': 'Какой профиль использовать?', 'options': ['Первый', 'первый']})


class ClarificationContinuationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(api, 'DATA_DIR', root))
        self.enterContext(patch.object(jobs, 'authorize', return_value={'revision': 'r', 'model': 'm'}))
        self.enterContext(patch.object(jobs.subprocess, 'run', return_value=SimpleNamespace(returncode=0)))
        self.enterContext(patch.object(api, 'observe_service', return_value={'active': True, 'state': 'active', 'unit_present': True}))

    def submit(self, identity, actor='11', prompt='Создай подписку'):
        payload = control.Chat(prompt=prompt, request_id=identity, model='m')
        return jobs.submit(api, payload, actor=actor, channel='bot')

    def test_bot_reply_gets_original_goal_and_question_without_saved_chat_memory(self):
        first = self.submit('a' * 32)
        jobs.update(api, first['id'], state='done', output={'result': {
            'answer': 'Какой срок подписки?', 'tools_used': ['ask_user'],
            'clarification': {'question': 'Какой срок подписки?', 'awaiting_input': True}}})
        next_job = self.submit('b' * 32, prompt='На месяц')
        other = self.submit('c' * 32, actor='22', prompt='На месяц')
        self.assertNotIn('previous_request_id', other)
        self.assertIsNone(operations.read(jobs.path(api, other['id']))['previous_request_id'])
        seen = []
        def chat(_api, payload, actor, channel):
            seen.extend(row.model_dump() for row in payload.history)
            return {'result': {'answer': 'Продолжаю исходную задачу', 'tools_used': []}}
        with patch.object(control, 'chat_request', side_effect=chat):
            jobs.execute(api, next_job['id'])
        self.assertEqual([(row['role'], row['content']) for row in seen],
                         [('user', 'Создай подписку'), ('assistant', 'Какой срок подписки?')])
        # A later unrelated message must not resurrect the earlier question.
        later = self.submit('d' * 32, prompt='Другой вопрос')
        self.assertIsNone(operations.read(jobs.path(api, later['id']))['previous_request_id'])

    def test_expired_question_is_not_reused_for_a_new_message(self):
        import time
        first = self.submit('a' * 32)
        jobs.update(api, first['id'], state='done', output={'result': {
            'answer': 'Какой профиль использовать?', 'tools_used': ['ask_user'],
            'clarification': {'question': 'Какой профиль использовать?', 'awaiting_input': True}}})
        file = jobs.path(api, first['id'])
        row = operations.read(file)
        row['updated_at'] = time.time() - 3601
        operations.atomic_json(file, row)
        later = self.submit('b' * 32, prompt='Другая задача')
        self.assertIsNone(operations.read(jobs.path(api, later['id']))['previous_request_id'])
