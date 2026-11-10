import asyncio
import base64
from contextlib import asynccontextmanager
import io
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from tests.api.support import load_module
import assistant_control as control

runner = load_module('assistant_chat_files_runner', 'protocol-images/ai-agent/runner.py')


def attachment(name, mime, data):
    return {'name': name, 'mime': mime, 'data': base64.b64encode(data).decode()}


class AssistantChatFilesTests(unittest.TestCase):
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
