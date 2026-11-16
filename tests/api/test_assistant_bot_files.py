"""Telegram attachments use the existing chat contract, never command routing."""
import asyncio
import base64
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from tests.api.support import load_module

bot = load_module('assistant_files_bot', 'protocol-images/nodera-bot/bot.py')


def document(name='notes.txt', size=None):
    return {'document': {'file_id': 'fixture', 'file_name': name, 'file_size': size}}


class BotFileTests(unittest.IsolatedAsyncioTestCase):
    def client_factory(self, handler):
        client = httpx.AsyncClient
        return lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)

    async def test_document_download_is_bounded_and_uses_only_fixed_telegram_origin(self):
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, content='Текст файла'.encode())
        with patch.object(bot, 'bot_api', new=AsyncMock(return_value={'file_path': 'documents/file_1.txt', 'file_size': 24})) as api, \
                patch.object(bot.httpx, 'AsyncClient', self.client_factory(transport)):
            result = await bot.download_attachment({'bot_token': 'fixture-token'}, document())
        self.assertEqual(result['name'], 'notes.txt')
        self.assertEqual(result['mime'], 'text/plain')
        self.assertEqual(base64.b64decode(result['data']).decode(), 'Текст файла')
        self.assertEqual(str(requests[0].url), 'https://api.telegram.org/file/botfixture-token/documents/file_1.txt')
        api.assert_awaited_once_with({'bot_token': 'fixture-token'}, 'getFile', file_id='fixture')

    async def test_unsafe_paths_and_declared_oversize_never_start_download(self):
        with patch.object(bot.httpx, 'AsyncClient') as client:
            for path in ('https://foreign.example/file', '../file', 'documents/../file', 'documents/%2e%2e/file', 'file?token=secret', '//foreign/file'):
                with patch.object(bot, 'bot_api', new=AsyncMock(return_value={'file_path': path})):
                    with self.assertRaises(bot.AttachmentError): await bot.download_attachment({}, document())
            with patch.object(bot, 'bot_api', new=AsyncMock()) as metadata:
                with self.assertRaises(bot.AttachmentError): await bot.download_attachment({}, document(size=bot.MAX_FILE_BYTES + 1))
                metadata.assert_not_awaited()
            with patch.object(bot, 'bot_api', new=AsyncMock(return_value={'file_size': bot.MAX_FILE_BYTES + 1})):
                with self.assertRaises(bot.AttachmentError): await bot.download_attachment({}, document())
            client.assert_not_called()

    async def test_stream_size_redirects_and_file_content_are_checked(self):
        for status, content, name in [(200, b'x' * (bot.MAX_FILE_BYTES + 1), 'large.txt'),
                                      (302, b'', 'notes.txt'), (200, b'', 'notes.txt'),
                                      (200, b'not a png', 'image.png'), (200, b'\x00binary', 'notes.txt'),
                                      (200, b'not a pdf', 'doc.pdf')]:
            with self.subTest(status=status, name=name), \
                    patch.object(bot, 'bot_api', new=AsyncMock(return_value={'file_path': 'documents/file'})), \
                    patch.object(bot.httpx, 'AsyncClient', self.client_factory(lambda request: httpx.Response(status, content=content, headers={'location': 'https://foreign.example'}))):
                with self.assertRaises(bot.AttachmentError): await bot.download_attachment({'bot_token': 'fixture'}, document(name))

    async def test_photo_selects_largest_variant_within_limit(self):
        message = {'photo': [{'file_id': 'small', 'width': 10, 'height': 10, 'file_size': 100},
                             {'file_id': 'best', 'width': 100, 'height': 100, 'file_size': 1000},
                             {'file_id': 'large', 'width': 1000, 'height': 1000, 'file_size': bot.MAX_FILE_BYTES + 1}]}
        self.assertEqual(bot.message_file(message)['file_id'], 'best')
        with patch.object(bot, 'bot_api', new=AsyncMock(return_value={'file_path': 'photos/file.jpg'})), \
                patch.object(bot.httpx, 'AsyncClient', self.client_factory(lambda request: httpx.Response(200, content=b'\xff\xd8\xffjpeg'))):
            result = await bot.download_attachment({'bot_token': 'fixture'}, message)
        self.assertEqual(result['mime'], 'image/jpeg')

    async def test_guest_subscription_and_oversize_album_never_download_or_call_ai(self):
        with patch.object(bot, 'download_attachment', new=AsyncMock()) as download, patch.object(bot, 'agent_request', new=AsyncMock()) as agent:
            self.assertEqual(await bot.attachment_reply({}, {'id': 11}, '', [document()], False), '')
            self.assertIn('AI', await bot.attachment_reply({}, {'id': 11}, '', [document()], 'subscription'))
            self.assertIn('4', await bot.attachment_reply({}, {'id': 11}, '', [document()] * 5, True))
            download.assert_not_awaited()
            agent.assert_not_awaited()

    async def test_total_size_and_unsupported_formats_never_call_ai(self):
        large = {'name': 'a.txt', 'mime': 'text/plain', 'data': base64.b64encode(b'x' * bot.MAX_FILE_BYTES).decode()}
        with patch.object(bot, 'download_attachment', new=AsyncMock(return_value=large)), patch.object(bot, 'agent_request', new=AsyncMock()) as agent, patch.object(bot, 'bot_api', new=AsyncMock()):
            self.assertIn('6 МБ', await bot.attachment_reply({}, {'id': 11}, '', [document()] * 4, True))
            agent.assert_not_awaited()
        with self.assertRaises(bot.AttachmentError): bot.message_file(document('archive.exe'))

    async def test_file_payload_and_caption_reach_chat_api_without_model_or_command_interpretation(self):
        calls = []
        files = [{'name': 'notes.txt', 'mime': 'text/plain', 'data': 'dGV4dA=='}]
        def transport(request):
            calls.append(request)
            return httpx.Response(200, json={'result': {'answer': 'Прочитано'}})
        with patch.object(bot.httpx, 'AsyncClient', self.client_factory(transport)):
            result = await bot.agent_request({'control_token': 'fixture'}, 11, '/status', attachments=files)
        self.assertEqual(result, 'Прочитано')
        import json
        body = json.loads(calls[0].content)
        self.assertEqual(body['attachments'], files)
        self.assertEqual(body['prompt'], '/status')
        self.assertEqual(calls[0].headers['X-MCP-Actor'], '11')
        self.assertEqual(calls[0].url.path, '/api/assistant/bot-chat')

    async def test_captionless_file_and_explicit_ai_caption_use_the_attachment_handler(self):
        file = {'name': 'notes.txt', 'mime': 'text/plain', 'data': 'dGV4dA=='}
        with patch.object(bot, 'download_attachment', new=AsyncMock(return_value=file)), \
                patch.object(bot, 'bot_api', new=AsyncMock()), \
                patch.object(bot, 'agent_request', new=AsyncMock(return_value='OK')) as agent:
            for caption, prompt in [('', 'Проанализируй приложенные файлы.'), ('/ai Что на фото?', 'Что на фото?')]:
                self.assertEqual(await bot.attachment_reply({}, {'id': 11}, caption, [document()], 'chat'), 'OK')
                agent.assert_awaited_with({}, 11, prompt, attachments=[file])

    async def test_polling_album_is_one_ai_request_and_caption_is_not_a_server_command(self):
        delivered = asyncio.Event()
        polls = 0
        async def transport(settings, method, **payload):
            nonlocal polls
            if method == 'getWebhookInfo': return {}
            if method == 'getUpdates':
                polls += 1
                if polls == 1:
                    return [{'update_id': index, 'message': {**document(), 'chat': {'type': 'private'}, 'from': {'id': 11},
                             'media_group_id': 'album', 'caption': '/status' if index == 1 else ''}} for index in (1, 2)]
                await asyncio.wait_for(delivered.wait(), 3)
                raise asyncio.CancelledError()
            if method == 'sendMessage' and 'Прочитано' in payload['text']: delivered.set()
            return True
        file = {'name': 'notes.txt', 'mime': 'text/plain', 'data': 'dGV4dA=='}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_DIRECTORY': folder}), \
                patch.object(bot, 'bot_api', side_effect=transport), patch.object(bot, 'command_menu', new=AsyncMock()), \
                patch.object(bot, 'identify', new=AsyncMock(return_value='chat')), \
                patch.object(bot, 'download_attachment', new=AsyncMock(return_value=file)) as download, \
                patch.object(bot, 'agent_request', new=AsyncMock(return_value='Прочитано')) as agent, \
                patch.object(bot, 'server_command', new=AsyncMock()) as server:
            settings = {'modules': {'nodera-bot': True}, 'bot_token': 'fixture'}
            with self.assertRaises(asyncio.CancelledError): await bot.bot(settings)
            agent.assert_awaited_once_with(settings, 11, '/status', attachments=[file, file])
            self.assertEqual(download.await_count, 2)
            server.assert_not_awaited()


if __name__ == '__main__': unittest.main()
