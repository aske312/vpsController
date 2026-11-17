"""Conversation and tool-loop regressions found during the Nodera flow audit."""
from contextlib import asynccontextmanager
import asyncio
import base64
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from tests.api.support import api, load_module
import assistant_control as control

runner = load_module('assistant_flow_audit_runner', 'protocol-images/ai-agent/runner.py')
SETTINGS = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'fixture', 'api_key': 'secret'}
OPERATION = 'a' * 32


def call(name, arguments=None, identity='call'):
    return {'id': identity, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {})}}


def receipt(value):
    return SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(value))])


class FlowAuditTests(unittest.IsolatedAsyncioTestCase):
    async def run_chat(self, replies, tool_results, prompt='Проверь Mihomo', names=('mihomo_settings',), rounds=12):
        session = SimpleNamespace(call_tool=AsyncMock(side_effect=tool_results))
        @asynccontextmanager
        async def connections(*_):
            yield {name: (session, SimpleNamespace(name=name, description=name, inputSchema={'type': 'object'})) for name in names}
        provider = AsyncMock(side_effect=replies)
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider), patch.object(runner, 'MAX_ROUNDS', rounds):
            result = await runner.chat(SETTINGS, 'panel', prompt)
        return result, session.call_tool, provider

    async def test_identical_preparation_is_not_executed_twice(self):
        pending = receipt({'confirmation_required': True, 'operation_id': OPERATION})
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]},
             {'tool_calls': [call('mihomo_settings', {'method': 'PATCH'}, 'second')]}, {'content': 'Ожидает решения.'}],
            [pending, pending])
        self.assertEqual(tool.await_count, 1)
        self.assertEqual(result['status'], 'awaiting_confirmation')

    async def test_creative_reply_is_not_treated_as_a_server_mutation(self):
        answer = 'Я создала короткую сказку: однажды маленький робот нашёл звезду.'
        result, tool, _ = await self.run_chat([{'content': answer}], [], prompt='Придумай короткую сказку про робота')
        self.assertEqual(result['answer'], answer)
        self.assertNotIn('status', result)
        tool.assert_not_awaited()

    async def test_component_clarification_keeps_the_models_direct_answer(self):
        answer = 'Да, я могу управлять Mihomo доступными инструментами. Изменения требуют вашего подтверждения.'
        result, tool, provider = await self.run_chat([{'content': answer}], [],
            prompt='я про контроль этим компонентом mihomo')
        self.assertEqual(result['answer'], answer)
        self.assertNotIn('status', result)
        self.assertNotIn('tool_choice', provider.call_args.kwargs)
        tool.assert_not_awaited()

    def test_component_topics_are_distinct_from_requests_for_live_evidence(self):
        for prompt in ('я про контроль этим компонентом mihomo', 'Mihomo',
                'Какие твои возможности по управлению профилями?', 'Что ты умеешь с Mihomo?',
                'Объясни правила маршрутизации', 'по твоему функционалу, новый протокол в твоей власти?'):
            with self.subTest(prompt=prompt): self.assertFalse(runner.operation_request(prompt))
        for prompt in ('Проверь Mihomo', 'Какие профили у нас сейчас?', 'Форма всё так же не появляется, почему?',
                'Создай группу', 'Prepare routing', 'Покажи текущие правила', 'Подтверди операцию'):
            with self.subTest(prompt=prompt): self.assertTrue(runner.operation_request(prompt))

    async def test_one_completed_operation_does_not_confirm_an_entire_plan(self):
        result, _, _ = await self.run_chat([
            {'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]},
            {'tool_calls': [call('mihomo_operation', {'id': OPERATION}, 'poll')]},
            {'content': 'Все изменения применены.'}], [
                receipt({'confirmation_required': True, 'operation_ids': [OPERATION, 'b'*32]}),
                receipt({'id': OPERATION, 'state': 'succeeded', 'result': 'passed'})],
            names=('mihomo_settings', 'mihomo_operation'))
        self.assertNotIn('Все изменения применены', result['answer'])
        self.assertEqual(result['prepared_operation_ids'], [OPERATION, 'b'*32])

    async def test_read_polling_can_observe_a_changed_operation(self):
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_operation')]}, {'tool_calls': [call('mihomo_operation', identity='second')]}, {'content': 'Операция завершилась.'}],
            [receipt({'id': OPERATION, 'state': 'running'}), receipt({'id': OPERATION, 'state': 'succeeded'})], names=('mihomo_operation',))
        self.assertEqual(tool.await_count, 2)
        self.assertIn('завершилась', result['answer'])

    async def test_repeat_failure_stops_later_actions_in_the_same_batch(self):
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]},
             {'tool_calls': [call('mihomo_settings', {'method': 'PATCH'}, 'repeat'), call('mihomo_profile_create', identity='unexpected')]},
             {'content': 'Изменение отклонено, следующие действия не запускались.'}],
            [receipt({'error': 'revision_conflict'}), receipt({'confirmation_required': True, 'operation_id': OPERATION})],
            names=('mihomo_settings', 'mihomo_profile_create'))
        self.assertEqual(tool.await_count, 1)
        self.assertEqual(result['status'], 'incomplete')

    async def test_provider_failure_preserves_a_prepared_action_without_retry(self):
        result, tool, provider = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]}, httpx.ReadTimeout('PRIVATE PROVIDER VALUE')],
            [receipt({'confirmation_required': True, 'operation_id': OPERATION})])
        self.assertEqual(tool.await_count, 1)
        self.assertEqual(provider.await_count, 2)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('подготовлен', result['answer'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    async def test_unknown_write_result_blocks_a_different_write_until_user_review(self):
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH', 'values': {'name': 'first'}})]},
             {'tool_calls': [call('mihomo_settings', {'method': 'PATCH', 'values': {'name': 'second'}}, 'second')]},
             {'content': 'Итог первого запроса неизвестен.'}], [TimeoutError('PRIVATE')])
        self.assertEqual(tool.await_count, 1)
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any(row['error_code'] == 'mutation_unconfirmed' for row in result['diagnostics']))

    async def test_transient_read_failure_can_be_rechecked_once(self):
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_read')]}, {'tool_calls': [call('mihomo_read', identity='second')]},
             {'content': 'Mihomo ответил на повторное чтение.'}],
            [TimeoutError('PRIVATE'), receipt({'available': True})], names=('mihomo_read',))
        self.assertEqual(tool.await_count, 2)
        self.assertIn('ответил', result['answer'])

    async def test_successful_read_clears_the_previous_failure_budget(self):
        result, tool, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_read', identity=str(index))]} for index in range(3)] + [{'content': 'Последнее состояние прочитано.'}],
            [TimeoutError('PRIVATE'), receipt({'state': 'running'}), receipt({'state': 'stopped'})], names=('mihomo_read',))
        self.assertEqual(tool.await_count, 3)
        self.assertIn('Последнее состояние', result['answer'])

    async def test_failed_tool_is_not_evidence_of_a_successful_change(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]}, {'content': 'Применила изменения. Группы созданы.'}],
            [receipt({'error': 'revision_conflict', 'http_status': 409})])
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('Группы созданы', result['answer'])
        self.assertNotIn('Применила', result['answer'])

    async def test_pending_receipt_survives_large_tool_details(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]}],
            [receipt({'confirmation_required': True, 'operation_id': OPERATION, 'details': 'x' * 100000})], rounds=1)
        self.assertEqual(result['status'], 'awaiting_confirmation')

    async def test_pending_action_cannot_be_reported_as_applied(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]}, {'content': 'Изменения применены. Группы созданы.'}],
            [receipt({'confirmation_required': True, 'operation_id': OPERATION})])
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.assertNotIn('применены', result['answer'])
        self.assertIn('решения', result['answer'])

    async def test_unknown_final_outcome_is_not_proof_of_application(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_operation')]}, {'content': 'Изменения применены.'}],
            [receipt({'id': OPERATION, 'state': 'succeeded', 'result': 'unknown'})], names=('mihomo_operation',))
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('применены', result['answer'])

    async def test_confirmed_personal_memory_write_can_be_acknowledged(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('context_remember', {'text': 'concise answers'})]}, {'content': 'Сохранила предпочтение.'}],
            [receipt({'facts': [{'text': 'concise answers'}]})], prompt='Запомни предпочтение', names=('context_remember',))
        self.assertEqual(result['answer'], 'Сохранила предпочтение.')

    async def test_malformed_batch_cannot_execute_a_valid_first_action(self):
        with self.assertRaises(runner.ProviderFailure):
            await self.run_chat([{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'}), {'id': 'broken'}]}],
                                [receipt({'confirmation_required': True, 'operation_id': OPERATION})])

    async def test_conceptual_question_does_not_force_a_server_action(self):
        result, tool, provider = await self.run_chat([{'content': 'Группа url-test выбирает доступный узел с меньшей задержкой.'}], [],
                                                   prompt='Что такое группа url-test в Mihomo?')
        self.assertNotIn('tool_choice', provider.call_args.kwargs)
        tool.assert_not_awaited()
        self.assertIn('меньшей задержкой', result['answer'])

    async def test_summary_cannot_invent_success_after_a_tool_failure(self):
        result, _, _ = await self.run_chat(
            [{'tool_calls': [call('mihomo_settings', {'method': 'PATCH'})]}, {'content': 'Всё применила, профиль создан.'}],
            [receipt({'error': 'tool_timeout'})], rounds=1)
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('профиль создан', result['answer'])


class ProviderEnvelopeTests(unittest.TestCase):
    def test_implicit_bot_context_cannot_reintroduce_an_old_operation(self):
        settings = {**SETTINGS, 'bot_dialog_context': {'untrusted_source': True, 'messages': [
            {'role': 'assistant', 'text': 'Перепроверю операцию ' + OPERATION + '.'},
            {'role': 'user', 'text': 'Почему так?'}]}}
        persona = runner.persona(settings)
        self.assertNotIn(OPERATION, persona)
        self.assertIn('Почему так?', persona)
        self.assertIn(OPERATION, settings['bot_dialog_context']['messages'][0]['text'])

    def test_mihomo_operation_retains_the_actual_outcome(self):
        with patch.object(control, 'read', return_value={'accounts': []}), \
             patch.object(control.operations, 'read', return_value={}), \
             patch.object(api, 'get_application_operation', return_value={'id': OPERATION, 'action': 'mihomo-profile-create', 'state': 'succeeded', 'result': 'unknown'}):
            result = control.control_tool(api, 'mihomo_operation', {'operation_id': OPERATION})
        self.assertEqual(result['result'], 'unknown')

    def test_api_checks_runtime_result_before_it_reaches_cache_and_ui(self):
        for result in (None, {'answer': 42}, {'answer': 'text', 'tools_used': 'bad'}, {'answer': 'text', 'status': 'invented'}):
            with self.subTest(result=result), self.assertRaises(control.HTTPException):
                control.checked_chat_result({'ok': True, 'result': result})
        value = control.checked_chat_result({'ok': True, 'result': {'answer': 'Legacy answer'}})
        self.assertEqual(value['result']['tools_used'], [])

    def test_incomplete_and_malformed_provider_envelopes_are_rejected(self):
        for value in ({'content': 'partial', 'tool_calls': [call('read'), call('read')]},
                      {'content': {'unexpected': 'object'}}, {'tool_calls': [{'id': 'call'}]}):
            with self.subTest(value=value), self.assertRaises(runner.ProviderFailure):
                runner.provider_message(value)

    def test_nonstream_truncation_is_rejected_by_the_real_provider_adapter(self):
        client = httpx.AsyncClient
        def handler(_request):
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]})
        async def check():
            with patch.object(runner.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)), \
                 patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('8.8.8.8', 443))])):
                with self.assertRaises(runner.ProviderFailure):
                    await runner.provider_request(SETTINGS, [], [])
        asyncio.run(check())

    def test_large_previous_documents_do_not_block_the_new_question(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        provider = AsyncMock(return_value={'content': 'Продолжаем разбор.'})
        history = []
        for index, character in enumerate(('x', 'z')):
            history.extend([{'role': 'user', 'content': 'Document ' + str(index), 'attachments': [
                {'name': 'notes.txt', 'mime': 'text/plain', 'data': base64.b64encode((character * 60000).encode()).decode()}]},
                {'role': 'assistant', 'content': 'Read.'}])
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(SETTINGS, 'panel', 'Продолжаем', history=history))
        self.assertEqual(result['answer'], 'Продолжаем разбор.')
        messages = provider.call_args.args[1]
        self.assertIn('Document 0', messages[1]['content'])
        self.assertNotIn('x' * 100, json.dumps(messages))
        self.assertIn('z' * 100, json.dumps(messages))

