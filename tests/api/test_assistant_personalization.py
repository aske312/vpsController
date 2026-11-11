import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.api.support import api
import assistant_personalization as context
import assistant_memory as memory


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.api = SimpleNamespace(DATA_DIR=self.root)
        self.settings = {'revision': 'a' * 32, 'control_token': 'test-token', 'memory_days': 30,
                         'accounts': [{'telegram_id': 11, 'bot_chat_access': True}, {'telegram_id': 22, 'bot_chat_access': True}]}

    def test_preferences_and_cross_channel_topics_are_scoped_to_actor_and_reset(self):
        context.update(self.api, self.settings, '11', context.MemoryAction(operation='remember', text='Concise answers'))
        identity = uuid.uuid4().hex
        memory.append(self.api, identity, '11', 'bot', 'My router', [], {'answer': 'Help', 'tools_used': []}, 'model', self.settings)
        snapshot = context.snapshot(self.api, self.settings, '11')
        self.assertEqual(snapshot['facts'][0]['text'], 'Concise answers')
        self.assertEqual(snapshot['recent_topics'], ['My router'])
        self.assertEqual(context.snapshot(self.api, self.settings, '11', identity)['recent_topics'], [])
        other = context.snapshot(self.api, self.settings, '22')
        self.assertEqual(other['facts'], [])
        self.assertEqual(other['recent_topics'], [])
        cleared = context.update(self.api, self.settings, '11', context.MemoryAction(operation='forget'))
        self.assertEqual(cleared['facts'], [])
        self.assertEqual(cleared['recent_topics'], [])
        context.update(self.api, self.settings, '11', context.MemoryAction(operation='remember', text='One'))
        with patch.object(context.time, 'time', return_value=time.time() + 31 * 86400):
            self.assertEqual(context.snapshot(self.api, self.settings, '11')['facts'], [])

    def test_write_permit_is_bound_to_actor_operation_revision_and_expiry(self):
        remember = context.MemoryAction(operation='remember', text='Short answers')
        proof = context.permit(self.settings, '11', 'remember')
        with patch.object(context.control, 'read', return_value=self.settings), patch.object(context.control, 'require_installed'):
            self.assertEqual(context.authorize(self.api, 'Bearer test-token', '11', remember, proof), self.settings)
            for actor, action, token in [('22', remember, proof), ('11', context.MemoryAction(operation='forget'), proof), ('11', remember, ''), ('33', remember, proof)]:
                with self.assertRaises(api.HTTPException): context.authorize(self.api, 'Bearer test-token', actor, action, token)
            with patch.object(context.time, 'time', return_value=time.time() + 181):
                with self.assertRaises(api.HTTPException): context.authorize(self.api, 'Bearer test-token', '11', remember, proof)
            self.settings['revision'] = 'b' * 32
            with self.assertRaises(api.HTTPException): context.authorize(self.api, 'Bearer test-token', '11', remember, proof)
            self.settings['personalization_enabled'] = False
            with self.assertRaises(api.HTTPException): context.authorize(self.api, 'Bearer test-token', '11', context.MemoryAction(operation='read'), '')

    def test_secrets_and_invalid_actor_paths_are_rejected(self):
        with self.assertRaises(api.HTTPException): context.update(self.api, self.settings, '11', context.MemoryAction(operation='remember', text='password: secret'))
        with self.assertRaises(api.HTTPException): context.snapshot(self.api, self.settings, '../22')
