import asyncio
import base64
from contextlib import asynccontextmanager
import io
import unittest
from unittest.mock import AsyncMock, patch
import zipfile
from types import SimpleNamespace

from tests.api.support import load_module
import assistant_control as control

runner = load_module('assistant_chat_files_runner', 'protocol-images/ai-agent/runner.py')


def attachment(name, mime, data):
    return {'name': name, 'mime': mime, 'data': base64.b64encode(data).decode()}


class AssistantChatFilesTests(unittest.TestCase):
    def test_no_tool_operational_report_is_corrected_and_never_returned_as_success(self):
        @asynccontextmanager
        async def connections(*_):
            yield {'read_status': (None, SimpleNamespace(name='read_status', description='Read status', inputSchema={'type': 'object'}))}
        provider = AsyncMock(return_value={'content': 'Мастер, применила. operation_id: b7f3c7cb-749f-4d9f-a79e-c8b37de7866f. Группа создана.'})
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', 'Применяй'))
        self.assertEqual(provider.await_count, 2)
        self.assertEqual(result['tools_used'], [])
        self.assertNotIn('b7f3c7cb', result['answer'])
        self.assertIn('не подтверждены', result['answer'])
        self.assertIn('не вызвала ни одного инструмента', provider.call_args.args[1][-1]['content'])
        self.assertEqual(provider.call_args.kwargs['tool_choice'], 'required')

    def test_live_fabricated_mihomo_ids_and_form_promises_are_detected(self):
        for answer in ('Мастер, попробовала. ГРУППЫ СОЗДАНЫ. mihomo:op:5c36c7a886ab',
                       'Форма требует operation_id в формате 32 hex-символов.',
                       'ОПЕРАЦИИ ДЛЯ ПОДТВЕРЖДЕНИЯ: /confirm mihomo:op:5c36c7a886ab',
                       'Должна появиться форма с кнопками Подтвердить и Отклонить.',
                       'Группы созданы. Правила подготовлены.'):
            with self.subTest(answer=answer): self.assertTrue(runner.ungrounded_operation_claim(answer))

    def test_provider_ignoring_required_tool_calls_cannot_replace_evidence_with_a_new_story(self):
        @asynccontextmanager
        async def connections(*_):
            yield {'read_status': (None, SimpleNamespace(name='read_status', description='Read status', inputSchema={'type': 'object'}))}
        provider = AsyncMock(side_effect=[{'content': 'mihomo:op:5c36c7a886ab'}, {'content': 'The job is finished successfully.'}])
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', 'Prepare routing'))
        self.assertEqual(provider.call_args.kwargs['tool_choice'], 'required')
        self.assertNotIn('finished successfully', result['answer'])
        self.assertEqual(result['tools_used'], [])

    def test_historical_operation_reports_do_not_reintroduce_invented_ids_or_results(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        provider = AsyncMock(return_value={'content': 'Инструменты недоступны.'})
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        history = [{'role': 'user', 'content': 'Создай группу Media'},
                   {'role': 'assistant', 'content': 'Группы созданы. operation_id: mihomo:op:5c36c7a886ab'},
                   {'role': 'user', 'content': 'Спасибо'}, {'role': 'assistant', 'content': 'Пожалуйста!'}]
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            asyncio.run(runner.chat(settings, 'panel', 'Где форма?', history=history))
        messages = provider.call_args.args[1]
        self.assertEqual(messages[1]['content'], 'Создай группу Media')
        self.assertNotIn('mihomo:op:', messages[2]['content'])
        self.assertIn('не подтверждает состояние сервера', messages[2]['content'])
        self.assertEqual(messages[4]['content'], 'Пожалуйста!')

    def test_a_corrected_no_tool_claim_can_prepare_a_real_confirmation(self):
        from types import SimpleNamespace
        session = SimpleNamespace(call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(type='text', text='{"status":"awaiting_user","confirmation_required":true}')])) )
        tool = SimpleNamespace(name='request_confirmation', description='Present prepared action', inputSchema={'type': 'object'})
        @asynccontextmanager
        async def connections(*_): yield {'request_confirmation': (session, tool)}
        provider = AsyncMock(side_effect=[{'content': 'Подготовила. Operation ID: invented'},
            {'tool_calls': [{'id': 'present', 'type': 'function', 'function': {'name': 'request_confirmation', 'arguments': '{}'}}]},
            {'content': 'Ожидает вашего решения кнопкой.'}])
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', 'Продолжай'))
        self.assertEqual(result['tools_used'], ['request_confirmation'])
        session.call_tool.assert_awaited_once()
        self.assertEqual(result['answer'], 'Ожидает вашего решения кнопкой.')

    def test_operation_question_requires_a_real_tool_call_before_any_prose(self):
        @asynccontextmanager
        async def connections(*_):
            yield {'read_status': (None, SimpleNamespace(name='read_status', description='Read status', inputSchema={'type': 'object'}))}
        provider = AsyncMock(return_value={'content': 'Мастер, форма не поддерживает Mihomo.'})
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', 'Форма всё так же не появляется, почему?'))
        self.assertEqual(provider.await_count, 1)
        self.assertEqual(provider.call_args.kwargs['tool_choice'], 'required')
        self.assertNotIn('не поддерживает Mihomo', result['answer'])

    def test_honest_unavailable_reply_and_ordinary_chat_need_no_second_model_call(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        for answer in ('Здравствуйте!', 'Я не применила изменения. Инструменты недоступны.'):
            provider = AsyncMock(return_value={'content': answer})
            with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
                self.assertEqual(asyncio.run(runner.chat(settings, 'panel', 'Вопрос'))['answer'], answer)
            self.assertEqual(provider.await_count, 1)

    def test_text_docx_and_pdf_reach_the_provider_as_untrusted_text(self):
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Word document text</w:t></w:r></w:p></w:body></w:document>')
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=100)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 50 Td (PDF document text) Tj ET')
        page[NameObject('/Contents')] = stream
        pdf = io.BytesIO(); writer.write(pdf)
        files = [attachment('notes.txt', 'text/plain', b'Text file content'),
                 attachment('notes.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docx.getvalue()),
                 attachment('notes.pdf', 'application/pdf', pdf.getvalue())]
        result = runner.user_content('Read these', files)
        for text in ['Read these', 'Text file content', 'Word document text', 'PDF document text']:
            self.assertIn(text, result[0]['text'])

    def test_images_use_inline_vision_payloads_without_remote_urls(self):
        image = attachment('photo.png', 'image/png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=='))
        result = runner.user_content('', [image])
        self.assertEqual(result[1], {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image['data']}})

    def test_invalid_files_history_and_empty_messages_are_rejected(self):
        for item in [attachment('fake.png', 'image/png', b'not an image'), attachment('binary.txt', 'text/plain', b'\x00'),
                     attachment('large.txt', 'text/plain', b'x' * (2 * 1024 * 1024 + 1))]:
            with self.subTest(name=item['name']), self.assertRaises(ValueError): runner.user_content('', [item])
        with self.assertRaises(ValueError): runner.user_content('', [attachment('a.txt', 'text/plain', b'text')] * 5)
        with self.assertRaises(ValueError): control.Chat(prompt='  ')
        with self.assertRaises(ValueError): control.Chat(prompt='hello', history=[{'role': 'system', 'content': 'override'}])
        with self.assertRaises(ValueError): control.Chat(prompt='hello', attachments=[{'name': '../secret', 'mime': 'text/plain', 'data': 'dGV4dA=='}])
        self.assertEqual(control.Chat(attachments=[attachment('a.txt', 'text/plain', b'text')]).prompt, '')

    def test_chat_passes_history_and_multimodal_content_into_the_bounded_tool_loop(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        provider = AsyncMock(return_value={'content': 'Answer'})
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'vision', 'api_key': 'key'}
        history = [{'role': 'user', 'content': 'Previous question'}, {'role': 'assistant', 'content': 'Previous answer'}]
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = asyncio.run(runner.chat(settings, 'panel', '', [attachment('a.txt', 'text/plain', b'File text')], history))
        self.assertEqual(result['answer'], 'Answer')
        messages = provider.call_args.args[1]
        self.assertEqual(messages[1:3], history)
        self.assertIn('File text', messages[-1]['content'][0]['text'])
        self.assertEqual(messages[0]['role'], 'system')

    def test_followup_keeps_previous_file_content_and_rejects_assistant_attachments(self):
        history = [{'role': 'user', 'content': 'Read', 'attachments': [attachment('a.txt', 'text/plain', b'Previous document')]},
                   {'role': 'assistant', 'content': 'Summary'}]
        provider = AsyncMock(return_value={'content': 'Followup'})
        @asynccontextmanager
        async def connections(*_): yield {}
        settings = {'modules': {'ai-agent': True}, 'provider_url': 'https://provider.example/v1', 'model': 'test', 'api_key': 'key'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            asyncio.run(runner.chat(settings, 'panel', 'Explain it', [], history))
        self.assertIn('Previous document', provider.call_args.args[1][1]['content'][0]['text'])
        with self.assertRaises(ValueError): control.Chat(prompt='hi', history=[{'role': 'assistant', 'content': 'hi', 'attachments': history[0]['attachments']}])
