"""Exercise upstream read handlers with synthetic Telegram entities, never a live account."""
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('guarded_adapter_tests', ROOT / 'protocol-images/ai-agent/telegram_guarded.py')
guarded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guarded)
HAS_MCP = importlib.util.find_spec('mcp') is not None


class VendorTests(unittest.TestCase):
    def test_pinned_sources_are_complete_and_unchanged(self):
        root = guarded.verify_vendor()
        self.assertEqual(json.loads((root / 'SOURCE.json').read_text())['commit'], guarded.UPSTREAM_COMMIT)
        self.assertTrue((root / 'tganalytics/mcp_server_actions.py').exists())
        self.assertEqual(len(guarded.TOOLS), 54)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'vendor'
            shutil.copytree(root, target)
            (target / 'tganalytics/mcp_server_read.py').write_text('modified')
            with self.assertRaises(RuntimeError): guarded.verify_vendor(target)


@unittest.skipUnless(HAS_MCP, 'Requires the optional AI-agent environment; also run by assistant_mcp_smoke.py')
class GuardedReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from telethon.sessions import StringSession
        from telethon.crypto import AuthKey
        from telethon.tl.types import User, Message, PeerUser, Chat, ChatPhotoEmpty
        from telethon import utils
        cls.directory = tempfile.TemporaryDirectory()
        session = StringSession()
        session.set_dc(2, '149.154.167.51', 443)
        session.auth_key = AuthKey(b'X' * 256)
        cls.settings = {'mcp': {'telegram-mcp': True}, 'telegram_api_id': 123, 'telegram_api_hash': 'a' * 32,
            'telegram_state_dir': cls.directory.name, 'accounts': [{'telegram_id': 11, 'session': session.save(),
                'telegram_access': True, 'effective_permissions': ['telegram_read'], 'excluded_dialogs': [99], 'telegram_action_targets': ['42']}]}
        cls.adapter = guarded.ReadAdapter(cls.settings, '11')
        cls.adapter.load()
        cls.allowed = User(id=42, first_name='Allowed')
        cls.excluded = User(id=99, first_name='Excluded')
        cls.group = Chat(id=77, title='Fixture group', photo=ChatPhotoEmpty(), participants_count=2, date=datetime.now(timezone.utc), version=1)
        cls.messages = [Message(id=1, peer_id=PeerUser(42), from_id=PeerUser(42), date=datetime.now(timezone.utc), message='fixture message')]
        def dialog(entity):
            title = getattr(entity, 'first_name', None) or getattr(entity, 'title', '')
            return types.SimpleNamespace(id=utils.get_peer_id(entity), entity=entity, title=title, name=title,
                                         is_user=isinstance(entity, User), is_group=isinstance(entity, Chat), is_channel=False, unread_count=0)
        class Client:
            owner = 11
            fail = False
            include_group = False
            calls = []
            def __init__(self, *args, **kwargs): pass
            async def connect(self): self.calls.append('connect')
            async def disconnect(self): self.calls.append('disconnect')
            async def get_me(self): return User(id=self.owner)
            async def get_entity(self, target):
                identity = getattr(target, 'user_id', target)
                if str(identity) == '-77': return cls.group
                return cls.excluded if str(identity).lstrip('@') in ('99', 'excluded') else cls.allowed
            async def iter_participants(self, entity, **kwargs):
                for user in (cls.allowed, cls.excluded): yield user
            async def send_message(self, entity, text, **kwargs):
                assert guarded.WRITE_AUTHORIZED.get(), 'Write outside trusted confirmation'
                self.calls.append(('send', entity.id, text))
                return cls.messages[0]
            async def get_messages(self, entity, **kwargs):
                return cls.messages if 'limit' in kwargs else cls.messages[0]
            async def get_dialogs(self, **kwargs):
                self.calls.append('dialogs')
                return [dialog(cls.allowed), dialog(cls.excluded)] + ([dialog(cls.group)] if self.include_group else [])
            async def iter_dialogs(self, **kwargs):
                if self.fail: raise OSError('private-session-secret')
                for item in (cls.allowed, cls.excluded): yield dialog(item)
            async def iter_messages(self, entity, **kwargs):
                self.calls.append(('messages', getattr(entity, 'id', None), kwargs))
                if self.fail: raise OSError('private-session-secret')
                for row in cls.messages:
                    if not kwargs.get('search') or kwargs['search'] in row.message: yield row
        cls.client = Client
        cls.adapter.client_factory = Client

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def setUp(self):
        self.client.owner, self.client.fail, self.client.calls = 11, False, []
        self.client.include_group = False
        self.adapter.client = self.adapter.manager = None
        self.adapter.enabled_tools = set(guarded.READ_TOOLS)
        self.adapter.action_targets = {'42'}
        self.adapter.actions.ALLOWED_TARGETS = {'42'}

    def run_read(self, name, arguments):
        async def run():
            try: return await self.adapter.call(name, arguments)
            finally: await self.adapter.close()
        return asyncio.run(run())

    def test_reads_really_use_upstream_handlers_and_filtered_manager(self):
        with patch.object(self.adapter.backend, 'tg_get_my_dialogs', wraps=self.adapter.backend.tg_get_my_dialogs) as handler:
            result = self.run_read('tg_get_my_dialogs', {'limit': 20})
        handler.assert_awaited_once()
        self.assertEqual([row['id'] for row in result['dialogs']], [42])
        result = self.run_read('tg_get_messages', {'group': '42', 'limit': 20})
        self.assertEqual(result['messages'][0]['text'], 'fixture message')
        result = self.run_read('tg_search_messages', {'group': '42', 'query': 'fixture', 'limit': 20})
        self.assertEqual(result['count'], 1)
        self.assertNotIn('phone', json.dumps(result))

    def test_excluded_unknown_and_username_targets_are_blocked_before_history(self):
        for group in ('99', '@username', '1234'):
            with self.assertRaises(PermissionError): self.run_read('tg_get_messages', {'group': group, 'limit': 20})
        self.assertFalse(any(isinstance(row, tuple) and row[0] == 'messages' for row in self.client.calls))

    def test_wrong_session_owner_cannot_read_dialogs(self):
        self.client.owner = 22
        with self.assertRaises(PermissionError): self.run_read('tg_get_my_dialogs', {'limit': 20})
        self.assertNotIn('dialogs', self.client.calls)
        self.assertIn('disconnect', self.client.calls)

    def test_failed_reads_do_not_become_empty_successes_or_reveal_secrets(self):
        self.client.fail = True
        with self.assertRaises(RuntimeError) as error: self.run_read('tg_get_messages', {'group': '42', 'limit': 20})
        self.assertNotIn('private-session-secret', str(error.exception))
        for path in Path(self.directory.name).rglob('*'):
            if path.is_file(): self.assertNotIn(b'private-session-secret', path.read_bytes())

    def test_large_results_are_bounded_and_explicitly_truncated(self):
        from telethon.tl.types import Message, PeerUser
        message = Message(id=2, peer_id=PeerUser(42), date=datetime.now(timezone.utc), message='X' * 10000)
        with patch.object(type(self), 'messages', [message] * 50):
            result = self.run_read('tg_get_messages', {'group': '42', 'limit': 50})
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 24000)
        self.assertTrue(all(len(row['text']) <= 4000 for row in result['messages']))

    def test_limits_and_actions_are_rejected_without_reading_Telegram(self):
        for name, arguments in [('tg_get_messages', {'group': '42', 'limit': 0}),
                                ('tg_get_messages', {'group': '42', 'limit': 1000}),
                                ('tg_use_session', {'session_name': 'other'}), ('tg_send_message', {'group': '42', 'text': 'no'})]:
            with self.assertRaises((PermissionError, ValueError)): self.run_read(name, arguments)
        self.assertFalse(self.client.calls)

    def test_upstream_RPC_and_method_guards_reject_writes_before_network(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.messages import SendMessageRequest
        from telethon.tl.types import InputPeerSelf
        async def run():
            client = self.adapter.transport.GuardedTelegramClient(StringSession(self.settings['accounts'][0]['session']), 123, 'a' * 32)
            with patch.object(TelegramClient, '__call__', new=AsyncMock()) as wire:
                with self.assertRaises(PermissionError): await client.send_message('me', 'not sent')
                with self.assertRaises(PermissionError): await client(SendMessageRequest(InputPeerSelf(), 'not sent', random_id=1))
                wire.assert_not_awaited()
        asyncio.run(run())

    def test_upstream_limiter_keeps_persistent_counters_and_read_only_usage(self):
        value = self.run_read('tg_get_stats', {})
        self.assertEqual(value['rate_limiter']['global_rps_mode'], 'shared')
        self.assertEqual(value['rate_limiter']['current_rps'], 4)
        self.assertIsNone(value['current_session'])
        self.assertTrue((Path(self.directory.name) / '11/anti_spam/daily_counters.txt').is_file())

    def test_flood_circuit_persists_and_blocks_RPC_until_cooldown(self):
        limiter = self.adapter.limiter.get_rate_limiter()
        async def run():
            try:
                await limiter.trip_circuit_breaker(301)
                # A new limiter sees the same persisted protection.
                fresh = self.adapter.limiter.RateLimiter(rps=4)
                with self.assertRaises(self.adapter.limiter.CircuitBreakerOpenError):
                    await fresh.check_circuit_breaker()
                rpc = AsyncMock()
                with self.assertRaises(self.adapter.limiter.CircuitBreakerOpenError):
                    await self.adapter.limiter.safe_call(rpc, max_retries=0)
                rpc.assert_not_awaited()
            finally:
                limiter.circuit_state_file.unlink(missing_ok=True)
        asyncio.run(run())

    def test_opted_in_reads_participants_global_search_and_own_session(self):
        self.adapter.enabled_tools = set(guarded.TOOLS)
        self.client.include_group = True
        result = self.run_read('tg_get_participants', {'group': '-77'})
        self.assertEqual([row['id'] for row in result['participants']], [42])
        result = self.run_read('tg_search_global_messages', {'query': 'fixture'})
        self.assertEqual(result['messages'][0]['chat_id'], 42)
        self.assertEqual(self.run_read('tg_list_sessions', {})['sessions'][0]['name'], '11')
        with self.assertRaises(PermissionError): self.run_read('tg_use_session', {'session_name': '22'})
        self.assertTrue(self.run_read('tg_use_session', {'session_name': '11'})['authorized'])

    def test_upstream_action_preview_host_confirmation_and_exactly_one_write(self):
        self.adapter.enabled_tools.add('tg_send_message')
        arguments = {'group': '42', 'message_text': 'synthetic approved message'}
        preview = self.run_read('tg_send_message', arguments)
        self.assertTrue(preview['dry_run'])
        self.assertIn('approval_code', preview)
        from importlib import import_module
        sys.path.insert(0, str(ROOT / 'protocol-images/ai-agent'))
        runner = import_module('runner')
        arguments.update(dry_run=False, confirm=True, confirmation_text='confirm telegram action', approval_code=preview['approval_code'])
        with patch.object(runner, 'vps_call', new=AsyncMock(return_value={'confirmation_required': True})) as host:
            result = self.run_read('tg_send_message', arguments)
            self.assertTrue(result['confirmation_required'])
            host.assert_awaited_once()
            self.assertFalse(any(isinstance(row, tuple) and row[0] == 'send' for row in self.client.calls))
        async def execute():
            try:
                with patch.object(self.adapter.actions, 'APPROVAL_MIN_AGE_SEC', 0):
                    result = await self.adapter.call('tg_send_message', arguments, execute=True)
                self.assertTrue(result['success'], result)
                self.assertFalse(guarded.WRITE_AUTHORIZED.get())
                result = await self.adapter.call('tg_send_message', arguments, execute=True)
                self.assertFalse(result['success'])
            finally: await self.adapter.close()
        asyncio.run(execute())
        self.assertEqual(sum(isinstance(row, tuple) and row[0] == 'send' for row in self.client.calls), 1)

    def test_private_file_references_cannot_escape_or_follow_symlinks(self):
        for reference in ('../secret', '/etc/passwd', 'C:\\Windows\\win.ini'):
            with self.assertRaises(PermissionError): self.adapter.file_path(reference)
        path = self.adapter.files / 'test.txt'
        path.write_text('fixture')
        self.assertEqual(Path(self.adapter.file_path('test.txt')), path.resolve())
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / 'private.txt'
            secret.write_text('not exposed')
            link = self.adapter.files / 'link.txt'
            try: link.symlink_to(secret)
            except OSError: return  # Windows may not grant symbolic-link creation.
            try:
                with self.assertRaises(PermissionError): self.adapter.file_path('link.txt')
            finally: link.unlink()

    def test_downloads_use_private_generated_paths_and_enforce_size_before_network(self):
        async def download(message, filename, progress_callback):
            progress_callback(7, message.file.size)
            Path(filename).write_bytes(b'fixture')
            return filename
        raw = types.SimpleNamespace(download_media=AsyncMock(side_effect=download))
        scoped = guarded.ScopedClient(raw, self.adapter)
        async def run():
            path = Path(await scoped.download_media(types.SimpleNamespace(file=types.SimpleNamespace(size=7)), '/etc/ignored'))
            self.assertEqual(path.parent, self.adapter.files)
            self.assertEqual(path.read_bytes(), b'fixture')
            raw.download_media.reset_mock()
            with self.assertRaises(ValueError):
                await scoped.download_media(types.SimpleNamespace(file=types.SimpleNamespace(size=21 * 1024 * 1024)))
            raw.download_media.assert_not_awaited()
        asyncio.run(run())

    def test_raw_actions_cannot_target_excluded_peers(self):
        from telethon.tl.functions.messages import DeleteHistoryRequest
        from telethon.tl.types import InputPeerUser
        raw = AsyncMock()
        async def run():
            with self.assertRaises(PermissionError):
                await guarded.ScopedClient(raw, self.adapter)(DeleteHistoryRequest(InputPeerUser(99, 0), max_id=0))
            raw.assert_not_awaited()
        asyncio.run(run())

    def test_exclusions_block_username_resolution_and_actions_even_when_enabled(self):
        self.adapter.enabled_tools = set(guarded.TOOLS)
        with self.assertRaises(RuntimeError): self.run_read('tg_resolve_username', {'username': 'excluded'})
        self.adapter.action_targets.add('99')
        self.adapter.actions.ALLOWED_TARGETS.add('99')
        with self.assertRaises(PermissionError): self.run_read('tg_send_message', {'group': '99', 'message_text': 'blocked'})
        with patch.object(self.excluded, 'phone', '70000000000'):
            with self.assertRaises(PermissionError):
                self.run_read('tg_delete_contacts_by_phones', {'phones': ['70000000000']})
        self.assertNotIn('private-session-secret', json.dumps(self.adapter.clean_result({'last_error': 'private-session-secret'})))
