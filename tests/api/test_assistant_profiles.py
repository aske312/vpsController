import asyncio
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from tests.api import test_assistant_contacts as contact_fixtures
from tests.api.support import api, load_module
import assistant_contacts as contacts
import assistant_control as control

bot = load_module('profile_dialog_bot', 'protocol-images/nodera-bot/bot.py')


class ProfileTests(unittest.TestCase):
    payload = contact_fixtures.BotAccessTests.payload
    save = contact_fixtures.BotAccessTests.save
    enabled_bot = contact_fixtures.BotAccessTests.enabled_bot
    approve = contact_fixtures.BotAccessTests.approve

    def setUp(self):
        contact_fixtures.BotAccessTests.setUp(self)
        self.headers = self.enabled_bot()
        self.client = TestClient(api.app)
        self.serial = 0
        self.contact(name='Telegram name', birthdate={'day':1, 'month':1, 'year':2000})

    def contact(self, **values):
        return self.client.post('/api/assistant/bot-contact', headers=self.headers, json={'telegram_id':11, **values})

    def edit(self, **values):
        with patch.dict(api.app.dependency_overrides, {api.require_token:lambda:None}):
            return self.client.patch('/api/assistant/bot-users/11', json=values)

    def dialog(self, text, identity=None):
        self.serial += 1
        return self.client.post('/api/assistant/bot-profile', headers=self.headers,
                                json={'text':text, 'request_id':identity or f'{self.serial:032x}'})

    def test_guest_block_is_durable_scoped_and_stops_bot_requests(self):
        self.assertEqual(self.edit(blocked=True).status_code, 200)
        settings = control.read(api)
        self.assertTrue(self.contact().json()['blocked'])
        with self.assertRaises(contacts.HTTPException):
            contacts.require_unblocked(api, {**settings, 'bot_token':settings['bot_token'].split(':')[0] + ':rotated-secret'}, '11')
        self.assertEqual(self.dialog('/start').status_code, 403)
        self.approve()  # A later role change must not silently bypass the block.
        self.assertEqual(self.client.post('/api/assistant/bot-chat', headers=self.headers, json={'prompt':'Hello'}).status_code, 403)
        with patch.object(contacts.time, 'time', return_value=time.time() + contacts.RETENTION + 10):
            self.assertTrue(contacts.list_recent(api)[0]['blocked'])
        contacts.require_unblocked(api, {**settings, 'bot_token':'different-bot'}, '11')
        self.assertEqual(self.edit(blocked=False).status_code, 200)
        self.assertNotIn('blocked', self.contact().json())

    def test_non_guests_cannot_be_blocked_or_deleted_by_guest_management(self):
        self.approve()
        self.assertEqual(self.edit(blocked=True).status_code, 409)
        with patch.dict(api.app.dependency_overrides, {api.require_token:lambda:None}):
            self.assertEqual(self.client.delete('/api/assistant/bot-users/11').status_code, 409)
        self.assertFalse(contacts.profile(api, control.read(api), '11').get('blocked'))

    def test_manual_birthday_survives_telegram_refresh_and_can_be_cleared(self):
        self.assertEqual(self.edit(display_name='Саша', birthdate={'day':29,'month':2}).status_code, 200)
        self.contact(name='Updated Telegram', birthdate={'day':3,'month':4})
        value = contacts.interlocutor(api, control.read(api), '11')
        self.assertEqual(value['name'], 'Саша')
        self.assertEqual(value['profile']['birthdate'], {'day':29,'month':2,'year':None})
        self.assertEqual(value['telegram']['birthdate'], {'day':3,'month':4,'year':None})
        self.assertEqual(value['permissions'], [])
        self.assertEqual(self.edit(birthdate=None).status_code, 200)
        self.contact(birthdate={'day':3,'month':4})
        self.assertIsNone(contacts.list_recent(api)[0]['birthdate'])
        for date in ({'day':31,'month':2}, {'day':1,'month':1,'year':9999}):
            self.assertEqual(self.edit(birthdate=date).status_code, 422)
        self.assertEqual(self.client.patch('/api/assistant/bot-users/11', headers=self.headers, json={'blocked':False}).status_code, 401)

    def test_deleting_guest_removes_contact_and_custom_profile_but_allows_a_new_visit(self):
        self.edit(display_name='Private name', blocked=True)
        with patch.dict(api.app.dependency_overrides, {api.require_token:lambda:None}):
            self.assertEqual(self.client.delete('/api/assistant/bot-users/11').status_code, 200)
        self.assertEqual(contacts.list_recent(api), [])
        self.assertEqual(contacts.profile(api, control.read(api), '11'), {})
        self.assertEqual(self.contact(name='New visit').status_code, 200)
        self.assertEqual(contacts.list_recent(api)[0]['profile_name'], '')

    def test_questionnaire_validates_dates_and_receipts_survive_lost_replies(self):
        identity = 'a' * 32
        first = self.dialog('/start', identity).json()
        self.assertIn('Как к вам обращаться', first['text'])
        self.assertEqual(self.dialog('/start', identity).json(), first)
        self.assertEqual(self.dialog('Something else', identity).status_code, 409)
        self.dialog('Александр')
        self.assertIn('существующую дату', self.dialog('31.02.2001').json()['text'])
        identity = 'b' * 32
        final = self.dialog('29.02', identity).json()
        before = deepcopy(contacts.profile(api, control.read(api), '11'))
        self.assertEqual(self.dialog('29.02', identity).json(), final)
        self.assertEqual(contacts.profile(api, control.read(api), '11'), before)
        self.assertEqual(before['onboarding'], 'done')
        self.assertIn('Александр', self.dialog('/myprofile').json()['text'])
        self.assertIn('29.02', self.dialog('/myprofile').json()['text'])
        self.assertNotIn('Как к вам обращаться', self.dialog('/start').json()['text'])
        self.assertEqual(control.read(api)['accounts'], [])

    def test_skip_each_question_and_skip_all_remain_optional_across_restart(self):
        self.dialog('/start'); self.dialog('/skip'); self.dialog('/skip')
        self.assertEqual(contacts.profile(api, control.read(api), '11')['onboarding'], 'done')
        self.dialog('/setup'); self.dialog('/skipall')
        self.assertEqual(contacts.profile(api, control.read(api), '11')['onboarding'], 'done')
        self.assertIsNone(self.dialog('A normal message').json()['text'])
        other = {**self.headers, 'X-MCP-Actor':'22'}
        self.assertEqual(self.client.post('/api/assistant/bot-profile', headers=other, json={'text':'/myprofile','request_id':'c'*32}).status_code, 404)

    def test_real_bot_queue_completes_guest_profile_after_a_lost_final_reply(self):
        actual = httpx.AsyncClient
        lost, requests, sent = False, [], []
        settings = control.read(api)
        async def run():
            delivered = asyncio.Event()
            polls = 0
            async def telegram(_settings, method, **payload):
                nonlocal polls
                if method == 'getWebhookInfo': return {}
                if method == 'getUpdates':
                    polls += 1
                    if polls == 1:
                        return [{'update_id':n,'message':{'from':{'id':11},'chat':{'type':'private'},'text':text}}
                                for n,text in enumerate(('/start','Имя профиля','29.02','/myprofile'),1)]
                    await asyncio.wait_for(delivered.wait(), 12)
                    raise asyncio.CancelledError()
                if method == 'sendMessage':
                    sent.append(payload['text'])
                    if 'Имя профиля' in payload['text'] and '29.02' in payload['text']: delivered.set()
                    return {}
                return True
            def transport(request):
                nonlocal lost
                body = json.loads(request.content)
                requests.append((request.url.path, body))
                result = self.client.post(request.url.path, headers=dict(request.headers), json=body)
                if request.url.path.endswith('/bot-profile') and body['text'] == '29.02' and not lost:
                    lost = True
                    raise httpx.ReadError('lost reply', request=request)
                return httpx.Response(result.status_code, json=result.json())
            with patch.dict(bot.os.environ, {'STATE_DIRECTORY':str(self.root/'inbox')}), \
                 patch.object(bot,'bot_api',side_effect=telegram), patch.object(bot,'command_menu',new=AsyncMock()), \
                 patch.object(bot,'observed_profile',new=AsyncMock(return_value={})), \
                 patch.object(bot,'remember_incoming',new=AsyncMock()), \
                 patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)), \
                 patch.object(bot,'agent_request',new=AsyncMock(side_effect=AssertionError('Profile reached AI'))):
                with self.assertRaises(asyncio.CancelledError): await bot.bot(settings)
        asyncio.run(run())
        final_requests = [body for path,body in requests if path.endswith('/bot-profile') and body['text']=='29.02']
        self.assertEqual(len(final_requests),2)
        self.assertEqual(final_requests[0]['request_id'],final_requests[1]['request_id'])
        self.assertTrue(lost)
        self.assertEqual(contacts.profile(api, settings, '11')['onboarding'],'done')
        self.assertFalse(list((self.root/'inbox/inbox').glob('*.json')))

    def test_blocked_guest_receives_no_id_or_expired_menu_reply(self):
        self.edit(blocked=True)
        actual, sent = httpx.AsyncClient, []
        async def run():
            checked = asyncio.Event()
            reads, polls = 0, 0
            async def telegram(_settings, method, **payload):
                nonlocal polls
                if method == 'getWebhookInfo': return {}
                if method == 'getUpdates':
                    polls += 1
                    if polls == 1:
                        return [{'update_id':1,'callback_query':{'id':'old','from':{'id':11},'data':'nd:expired','message':{'chat':{'type':'private','id':11}}}},
                                {'update_id':2,'message':{'from':{'id':11},'chat':{'type':'private'},'text':'/id'}}]
                    async with asyncio.timeout(5):
                        while list((self.root/'blocked-inbox/inbox').glob('*.json')): await asyncio.sleep(.05)
                    raise asyncio.CancelledError()
                if method == 'sendMessage': sent.append(payload)
                return True
            def transport(request):
                nonlocal reads
                result = self.client.post(request.url.path, headers=dict(request.headers), json=json.loads(request.content))
                reads += 1
                if reads == 2: checked.set()
                return httpx.Response(result.status_code,json=result.json())
            with patch.dict(bot.os.environ, {'STATE_DIRECTORY':str(self.root/'blocked-inbox')}), \
                 patch.object(bot,'bot_api',side_effect=telegram), patch.object(bot,'command_menu',new=AsyncMock()), \
                 patch.object(bot,'observed_profile',new=AsyncMock(return_value={})), \
                 patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)):
                with self.assertRaises(asyncio.CancelledError): await bot.bot(control.read(api))
        asyncio.run(run())
        self.assertEqual(sent, [])
        self.assertFalse(list((self.root/'blocked-inbox/inbox').glob('*.json')))
