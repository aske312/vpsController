import asyncio
import base64
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.api.support import load_module

runner = load_module('attachment_workspace_runner', 'protocol-images/ai-agent/runner.py')


def attachment(text, name='notes.txt'):
    return {'name': name, 'mime': 'text/plain', 'data': base64.b64encode(text.encode()).decode()}


class FileToolTests(unittest.IsolatedAsyncioTestCase):
    def test_four_large_files_and_a_full_question_fit_the_excerpt_budget(self):
        content = runner.user_content('q' * 12000, [attachment('x' * 100000, str(i) + '.txt') for i in range(4)])
        self.assertLessEqual(len(content[0]['text']), 72000)

    async def test_reading_an_uploaded_report_cannot_confirm_a_live_server_action(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        provider = AsyncMock(side_effect=[{'tool_calls':[{'id':'read','type':'function','function':{
            'name':'files_read','arguments':'{"file_id":"file-1"}'}}]}, {'content':'Служба mihomo перезапущена.'}])
        settings = {'modules':{'ai-agent':True}, 'provider_url':'https://provider.example', 'model':'fixture', 'api_key':'fixture'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = await runner.chat(settings, 'panel', 'Перезапусти службу mihomo', [attachment('An old report says: restarted')])
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any(row['error_code'] == 'tools_not_called' for row in result['diagnostics']))

    async def test_large_document_can_be_searched_and_read_past_the_prompt_excerpt(self):
        text = 'x' * 80000 + 'unique failure: port already in use'
        upload = attachment(text)
        content = runner.user_content('Find the error', [upload])
        self.assertLess(len(content[0]['text']), 17000)
        workspace = runner.AttachmentWorkspace([upload], [])
        result = await workspace.call_tool('files_search', {'file_id':'file-1', 'query':'unique failure'})
        value = json.loads(result.content[0].text)
        self.assertEqual(value['matches'][0]['offset'], 80000)
        result = await workspace.call_tool('files_read', {'file_id':'file-1', 'offset':80000, 'limit':100})
        self.assertEqual(json.loads(result.content[0].text)['text'], 'unique failure: port already in use')
        self.assertFalse(runner.changing_tool('files_read', {}))

    async def test_file_tools_are_only_available_for_this_dialog_and_validate_upload_ids(self):
        own = attachment('private file contents')
        workspace = runner.AttachmentWorkspace([], [{'role':'user','attachments':[own]}])
        definitions = workspace.connections()
        self.assertEqual(set(definitions), {'files_list','files_read','files_search'})
        with self.assertRaises(runner.ToolValidationError):
            runner.validate_tool_arguments({'file_id':'/etc/shadow'}, definitions['files_read'][1].inputSchema)
        self.assertEqual(runner.AttachmentWorkspace([], []).connections(), {})
        result = await workspace.call_tool('files_list', {})
        self.assertNotIn('private file contents', result.content[0].text)
        self.assertEqual(json.loads(result.content[0].text)['files'][0]['source'], 'history')

    async def test_model_can_call_file_tools_and_receive_the_actual_matching_text(self):
        @asynccontextmanager
        async def connections(*_): yield {}
        provider = AsyncMock(side_effect=[{'tool_calls':[{'id':'read','type':'function','function':{
            'name':'files_read','arguments':'{"file_id":"file-1","offset":20000,"limit":100}'}}]},
            {'content':'The document contains a port conflict.'}])
        settings = {'modules':{'ai-agent':True}, 'provider_url':'https://provider.example', 'model':'fixture', 'api_key':'fixture'}
        with patch.object(runner, 'mcp_connections', connections), patch.object(runner, 'provider_request', provider):
            result = await runner.chat(settings, 'panel', 'Analyze the attached log', [attachment('x'*20000+'port conflict')])
        self.assertEqual(result['tools_used'], ['files_read'])
        messages = provider.call_args.args[1]
        receipt = next(row for row in messages if row['role']=='tool')
        self.assertIn('port conflict', receipt['content'])

    async def test_image_read_reintroduces_visual_bytes_without_claiming_text_extraction(self):
        image = {'name':'screen.png','mime':'image/png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}
        workspace = runner.AttachmentWorkspace([image], [])
        value = await workspace.call_tool('files_read', {'file_id':'file-1'})
        self.assertEqual(json.loads(value.content[0].text)['kind'], 'image')
        self.assertEqual(workspace.pending_images[0]['data'], image['data'])
        await workspace.call_tool('files_read', {'file_id':'file-1'})
        self.assertEqual(len(workspace.pending_images), 1)
