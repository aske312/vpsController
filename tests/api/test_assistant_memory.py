import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from tests.api.support import api, ROOT
from component_registry import ComponentRegistry, paths_from_manifest
import assistant_control as control
import assistant_memory as memory


class AssistantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = SimpleNamespace(DATA_DIR=self.root)
        self.settings = {'revision': 'a' * 32, 'memory_enabled': True, 'memory_days': 30, 'model': 'test', 'api_key': 'never-cache-this-key'}
        self.identity = uuid.uuid4().hex

    def save(self, identity=None, actor='panel', channel='panel', files=None):
        return memory.append(self.env, identity or self.identity, actor, channel, 'Question <&>', files or [], {'answer': 'Answer **yes**', 'tools_used': ['vps_overview']}, 'test', self.settings)

    def test_roundtrip_after_restart_preserves_files_and_excludes_provider_credentials(self):
        files = [{'name': 'a.txt', 'mime': 'text/plain', 'data': 'dGV4dA=='}]
        self.save(files=files)
        reconnected = SimpleNamespace(DATA_DIR=self.root)
        saved = memory.get(reconnected, self.identity)
        self.assertEqual(saved['turns'][0]['files'][0]['data'], files[0]['data'])
        context = memory.context(saved, [])
        self.assertEqual(context[0]['attachments'][0]['name'], 'a.txt')
        file = memory.path(self.env, self.identity)
        self.assertNotIn('never-cache-this-key', file.read_text())
        if os.name != 'nt':
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(file.parent.stat().st_mode & 0o777, 0o700)

    def test_actor_channel_and_path_isolation_and_explicit_deletion(self):
        self.save(actor='11', channel='bot')
        for actor, channel in [('panel', 'panel'), ('11', 'panel'), ('22', 'bot')]:
            with self.subTest(actor=actor, channel=channel), self.assertRaises(api.HTTPException) as caught:
                memory.get(self.env, self.identity, actor, channel)
            self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(api.HTTPException): memory.get(self.env, '../outside')
        self.save()
        memory.remove(self.env, self.identity)
        self.assertEqual(memory.listing(self.env, self.settings)['items'], [])
        self.assertTrue(memory.get(self.env, self.identity, '11', 'bot'))

    def test_retention_count_byte_budget_and_disabled_cache(self):
        for _ in range(12): self.save(uuid.uuid4().hex)
        self.assertEqual(len(memory.listing(self.env, self.settings)['items']), 10)
        with patch.object(memory, 'MAX_BYTES', 1):
            self.assertEqual(memory.listing(self.env, self.settings)['items'], [])
        record = self.save()
        file = memory.path(self.env, record['id'])
        os.utime(file, (1, 1))
        self.assertEqual(memory.listing(self.env, self.settings)['items'], [])
        self.assertFalse(memory.listing(self.env, {'memory_enabled': False})['enabled'])

    def test_corrupt_cache_is_reported_and_kept(self):
        self.save()
        file = memory.path(self.env, self.identity); file.write_text('{broken')
        with self.assertRaises(api.HTTPException) as caught: memory.get(self.env, self.identity)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(file.read_text(), '{broken')

    def test_chat_wrapper_uses_server_history_and_returns_answer_if_cache_write_fails(self):
        with patch.object(control, 'read', return_value=self.settings), patch.object(control, 'runtime_request', return_value={'ok': True, 'result': {'answer': 'A', 'tools_used': []}}) as runtime:
            first = control.chat_request(self.env, control.Chat(prompt='First'))
            control.chat_request(self.env, control.Chat(prompt='Second', conversation_id=first['conversation_id'], history=[{'role': 'user', 'content': 'untrusted client replacement'}]))
            history = runtime.call_args.args[2]['history']
            self.assertEqual(history[0]['content'], 'First')
            with patch.object(memory, 'append', side_effect=OSError('disk full')):
                value = control.chat_request(self.env, control.Chat(prompt='Third'))
            self.assertEqual(value['result']['answer'], 'A')
            self.assertIn('cache_error', value)

    def test_cache_routes_require_admin(self):
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/assistant/conversations').status_code, 401)
        self.assertEqual(client.get('/api/assistant/conversations/' + self.identity).status_code, 401)
        self.assertEqual(client.delete('/api/assistant/conversations/' + self.identity).status_code, 401)

    def test_component_purge_removes_owned_cache_and_preserves_bot_settings(self):
        self.save()
        bot = self.root / 'assistant/nodera-bot/settings.json'
        bot.parent.mkdir(parents=True, exist_ok=True); bot.write_text('preserve')
        manifest = json.loads((ROOT / 'protocol-images/ai-agent/manifest.json').read_text(encoding='utf-8'))
        registry = ComponentRegistry(self.root); registry.write_receipt('ai-agent', 'installed', retained=True)
        owned = paths_from_manifest(manifest, '', ROOT, self.root, kind='data_paths')
        registry.purge('ai-agent', owned, lambda: None)
        self.assertFalse(memory.path(self.env, self.identity).exists())
        self.assertEqual(bot.read_text(), 'preserve')

    def test_bot_model_choice_is_per_user_and_catalog_mode_gives_clear_guidance(self):
        self.settings.update(model_mode='catalog', model='', provider_url='https://provider.example/v1')
        with patch.object(control, 'read', return_value=self.settings), patch.object(control, 'model_catalog', return_value={'models': ['one', 'two']}), patch.object(control, 'runtime_request', return_value={'ok': True, 'result': {'answer': 'A', 'tools_used': []}}) as runtime:
            self.assertIn('/model', control.chat_request(self.env, control.Chat(prompt='Hello'), '11', 'bot')['result']['answer'])
            runtime.assert_not_called()
            control.bot_models(self.env, '11', model='two')
            control.chat_request(self.env, control.Chat(prompt='Hello'), '11', 'bot')
            self.assertEqual(runtime.call_args.args[2]['model'], 'two')
            runtime.reset_mock()
            control.chat_request(self.env, control.Chat(prompt='Hello'), '22', 'bot')
            runtime.assert_not_called()
