import asyncio
import hashlib
import hmac
import json
import time
import unittest
import sys
import types
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import HTTPException
from tests.api.support import api
from tests.api import test_assistant_telegram_ux as fixtures
import assistant_bot_views as views
import assistant_contacts as contacts
import assistant_control as control
import assistant_telegram as telegram

bot = fixtures.bot


class BotTelegramSettingsTests(unittest.TestCase):
    payload = fixtures.TelegramViewsTests.payload
    save = fixtures.TelegramViewsTests.save
    enabled_bot = fixtures.TelegramViewsTests.enabled_bot
    approve = fixtures.TelegramViewsTests.approve
    submit = fixtures.TelegramViewsTests.submit

    def setUp(self):
        fixtures.TelegramViewsTests.setUp(self)
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['assistant','mihomo'], mihomo_profile_id=None,
            permission_overrides={'chat':False}, telegram_tools=[*telegram.DEFAULT_TOOLS,'tg_send_message'], session='1'+'a'*110)
        settings.update(telegram_api_id=1234, telegram_api_hash='a'*32)
        settings['mcp']['telegram-mcp'] = True
        control.store(api, settings)

    def input(self, text, request_id='e'*32):
        return self.client.post('/api/assistant/bot-telegram', headers=self.headers, json={'text':text,'request_id':request_id})

    def test_own_subscription_offer_and_telegram_menu_do_not_require_chat_or_a_link(self):
        with patch('assistant_mihomo.request', side_effect=AssertionError('No profile lookup needed')):
            cap=views.capabilities(api,control.read(api),'11')
            self.assertTrue(cap['sections']['telegram']); self.assertTrue(cap['sections']['subscription_setup'])
            self.assertFalse(cap['sections']['subscription']); self.assertFalse(cap['sections']['ai'])
            response=self.submit('/subscription')
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('`11`',response.json()['text']); self.assertIn('Императора',response.json()['text'])
        token=bot.access_details.set(cap)
        try:
            actions={}; bot.inline_keyboard('Menu','/menu','subscription',actions)
            self.assertIn('/telegram',actions.values()); self.assertIn('/subscription',actions.values())
            self.assertNotIn('/menu ai',actions.values())
            self.assertIn('telegram',dict(bot.available_commands()))
        finally: bot.access_details.reset(token)

    def test_tool_preferences_are_narrower_than_admin_policy_and_replay_once(self):
        menu=self.submit('/telegram tools').json()
        command=next(item['command'] for row in menu['buttons'] for item in row if 'tg_send_message' in item['command'])
        response=self.submit(command,'d'*32)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.submit(command,'d'*32).json(),response.json())
        selected=control.read(api)['accounts'][0]
        self.assertNotIn('tg_send_message',telegram.enabled(selected))
        self.assertIn('tg_send_message',selected['telegram_tools'])
        self.assertIn('tg_send_message',selected['telegram_disabled_tools'])
        with self.assertRaises(HTTPException): telegram.require(control.read(api),'11','tg_send_message')
        revision=control.read(api)['revision']
        self.assertEqual(self.submit('/telegram tool tg_delete_messages on '+revision+' 1').status_code,403)
        self.assertEqual(self.submit(command).status_code,409)
        self.assertEqual(self.submit('/telegram','d'*32).status_code,409)

    def test_missing_api_credentials_open_setup_guide_and_can_be_rechecked(self):
        settings = control.read(api)
        settings['accounts'][0].update(role='owner', permission_overrides={})
        settings['telegram_api_id'] = None
        settings.pop('telegram_api_hash', None)
        control.store(api, settings)

        self.assertEqual(bot.direct_prompt('Помоги настроить Telegram API'), '/telegram configure')
        command = '/telegram session'
        guide = self.submit(command).json()
        self.assertIn('API ID, API hash', guide['text'])
        self.assertEqual(guide['buttons'][0][0]['url'], 'https://my.telegram.org/apps')
        self.assertFalse(contacts.profile(api, control.read(api), '11').get('telegram_dialog'))

        settings = control.read(api)
        settings.update(telegram_api_id=1234, telegram_api_hash='a'*32)
        control.store(api, settings)
        retry = next(item for row in guide['buttons'] for item in row if item['text'] == 'Проверить настройки')
        ready = self.submit(retry['command']).json()
        self.assertIn('StringSession', ready['text'])
        self.assertTrue(contacts.profile(api, control.read(api), '11').get('telegram_dialog'))

    def test_only_owner_can_save_mcp_credentials_through_the_bot_form(self):
        self.assertEqual(self.submit('/telegram configure').status_code, 403)
        settings = control.read(api)
        settings['accounts'][0].update(role='owner', permission_overrides={})
        control.store(api, settings)
        with patch.object(api, 'PUBLIC_DOMAIN', 'panel.example.com'):
            command = self.submit('/telegram configure', 'a'*32)
            self.assertEqual(command.status_code, 200, command.text)
            self.assertEqual(self.submit('/telegram configure', 'a'*32).json(), command.json())
        url = urlsplit(command.json()['buttons'][0][0]['web_app'])
        self.assertEqual(url.hostname, 'panel.example.com')
        page = self.client.get(url.path + '?' + url.query)
        self.assertIn('Сохранить параметры', page.text)
        self.assertEqual(page.headers['cache-control'], 'no-store')

        def launch_data(actor, age=0):
            fields = {'auth_date': str(int(time.time()) - age), 'user': json.dumps({'id': actor})}
            key = hmac.digest(b'WebAppData', settings['bot_token'].encode(), 'sha256')
            message = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
            fields['hash'] = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
            return urlencode(fields)

        payload = {'setup': parse_qs(url.query)['setup'][0], 'api_id': 4321, 'api_hash': 'c'*32}
        valid = launch_data(11)
        for launch, status in ((valid + '&user=invalid', 401), (launch_data(11, 700), 401), (launch_data(22), 403)):
            self.assertEqual(self.client.post(url.path, json=payload, headers={'X-Telegram-Init-Data': launch}).status_code, status)
        header = {'X-Telegram-Init-Data': valid}
        self.assertEqual(self.client.post(url.path, json={**payload, 'setup': 'b'*32}, headers=header).status_code, 409)
        first = self.client.post(url.path, json=payload, headers=header)
        self.assertEqual(first.json(), {'saved': True}, first.text)
        self.assertEqual(self.client.post(url.path, json=payload, headers=header).status_code, 409)
        updated = control.read(api)
        self.assertEqual((updated['telegram_api_id'], updated['telegram_api_hash']), (4321, 'c'*32))
        self.assertEqual(updated['accounts'][0]['session'], settings['accounts'][0]['session'])

    def test_agent_setup_tool_offers_form_without_saving(self):
        from tests.api.test_assistant_control import runner
        settings = {'accounts': [], 'personalization_enabled': False}
        self.assertNotIn('offer_telegram_setup', runner.native_connections(settings, '11'))
        session, tool = runner.native_connections({**settings, 'offer_telegram_setup': True}, '11')['offer_telegram_setup']
        self.assertEqual(tool.inputSchema['properties'], {})
        output = asyncio.run(session.call_tool(tool.name, {}))
        self.assertEqual(json.loads(output.content[0].text)['saved'], False)

    def test_verified_session_is_private_actor_scoped_and_lost_reply_reconciles(self):
        self.assertEqual(self.submit('/telegram session').status_code,200)
        secret='1'+'b'*110
        with patch.object(control,'runtime_request',return_value={'result':{'authorized':True,'telegram_id':11}}) as runtime:
            first=self.input(secret)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(self.input(secret).json(),first.json())
            runtime.assert_called_once_with(api,'--telegram-session-check',{'actor':'11','session':secret})
        self.assertEqual(control.read(api)['accounts'][0]['session'],secret)
        self.assertNotIn(secret,first.text)
        self.assertNotIn(secret,json.dumps(contacts.profiles(api,control.read(api))))
        self.submit('/telegram session')
        with patch.object(control,'runtime_request',return_value={'result':{'authorized':True,'telegram_id':22}}):
            self.assertEqual(self.input('1'+'c'*110,'f'*32).status_code,422)
        self.assertEqual(control.read(api)['accounts'][0]['session'],secret)
        self.assertEqual(self.client.post('/api/assistant/bot-command',headers={**self.headers,'X-MCP-Actor':'22'},json={'command':'/telegram'}).status_code,403)

    def test_revocation_blocks_receipt_replay_and_session_input(self):
        self.submit('/telegram session')
        settings=control.read(api); settings['accounts'][0]['permission_overrides']['telegram_read']=False
        control.store(api,settings)
        self.assertEqual(self.submit('/telegram').status_code,403)
        self.assertEqual(self.input('1'+'b'*110).status_code,403)

    def test_runtime_rejection_is_a_durable_terminal_answer_without_changing_session(self):
        self.submit('/telegram session')
        prior=control.read(api)['accounts'][0]['session']
        with patch.object(control,'runtime_request',side_effect=HTTPException(502,'private-runtime-error')) as runtime:
            first=self.input('1'+'b'*110)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(self.input('1'+'b'*110).json(),first.json())
            runtime.assert_called_once()
        self.assertEqual(control.read(api)['accounts'][0]['session'],prior)
        self.assertNotIn('private-runtime-error',first.text)
        self.assertIn('Не удалось подтвердить',first.json()['text'])

    def test_runtime_checks_candidate_session_without_a_model_and_keeps_tool_preferences(self):
        from tests.api.test_assistant_control import runner
        settings=control.read(api)
        settings['accounts'][0].update(effective_permissions=['telegram_read'],telegram_access=True,telegram_disabled_tools=['tg_send_message'])
        scoped=runner.scoped_mcp_settings(settings,'11','telegram-mcp')
        self.assertEqual(scoped['accounts'][0]['telegram_disabled_tools'],['tg_send_message'])
        adapter=Mock(); adapter.auth_status=AsyncMock(return_value={'authorized':True,'telegram_id':11}); adapter.close=AsyncMock()
        factory=Mock(return_value=adapter)
        with patch.dict(sys.modules,{'telegram_guarded':types.SimpleNamespace(ReadAdapter=factory)}), \
             patch.object(runner,'chat',new=AsyncMock(side_effect=AssertionError('Session passed to model'))):
            output=asyncio.run(runner.request_main({'actor':'11','settings':settings,'session':'candidate'},'--telegram-session-check'))
            self.assertEqual(output['telegram_id'],11)
            self.assertEqual(factory.call_args.args[0]['accounts'][0]['session'],'candidate')
            self.assertNotEqual(settings['accounts'][0]['session'],'candidate')
            adapter.load.assert_called_once(); adapter.close.assert_awaited_once()
            with self.assertRaises(PermissionError):
                asyncio.run(runner.request_main({'actor':'22','settings':settings,'session':'candidate'},'--telegram-session-check'))
            factory.assert_called_once()

    def test_sessions_never_reach_history_or_free_form_ai(self):
        actual=httpx.AsyncClient; sent=[]; secret='1'+'z'*110
        def transport(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={})
        async def run():
            with patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)):
                await bot.remember_incoming({'control_token':'fixture','bot_token':'123:fixture'},
                    {'message_id':1,'chat':{'id':11},'from':{'id':11},'text':secret})
            token=bot.access_details.set(views.capabilities(api,control.read(api),'11'))
            try:
                with patch.object(bot,'agent_request',new=AsyncMock(side_effect=AssertionError('Secret sent to AI'))):
                    self.assertIn('Telegram API',await bot.scoped_reply({}, {'id':11},secret,'subscription'))
            finally: bot.access_details.reset(token)
        asyncio.run(run())
        self.assertNotIn(secret,json.dumps(sent))
        self.assertTrue(sent)

    def test_lost_session_reply_is_reconciled_after_dialog_has_finished(self):
        self.check_session_delivery(True)

    def test_session_is_not_validated_when_telegram_refuses_deletion(self):
        self.check_session_delivery(False)

    def check_session_delivery(self, allow_delete):
        actual=httpx.AsyncClient; sent=[]; deleted=[]; requests=[]; secret='1'+'b'*110
        settings = control.read(api)
        profile = contacts.profile(api, settings, '11')
        contacts.save_profile(api, settings, '11', {**profile, 'onboarding': 'name'})
        async def run():
            polls=0
            async def telegram_api(settings,method,**payload):
                nonlocal polls
                if method=='getWebhookInfo': return {}
                if method=='getUpdates':
                    if polls:
                        async with asyncio.timeout(8):
                            while len(sent)<polls: await asyncio.sleep(.01)
                    polls+=1
                    if polls>2: raise asyncio.CancelledError()
                    return [{'update_id':polls,'message':{'message_id':polls,'from':{'id':11},'chat':{'id':11,'type':'private'},'text':('/telegram session',secret)[polls-1]}}]
                if method=='sendMessage': sent.append(payload); return {'message_id':100+len(sent)}
                if method=='deleteMessage':
                    if payload['message_id'] == 2 and not allow_delete:
                        raise RuntimeError('Telegram refused to delete the message')
                    deleted.append(payload['message_id'])
                return True
            def transport(request):
                response=self.client.post(request.url.path,headers=dict(request.headers),json=json.loads(request.content))
                if request.url.path.endswith('/bot-telegram'):
                    self.assertIn(2, deleted)
                    requests.append(json.loads(request.content)['request_id'])
                    if len(requests)==1: raise httpx.ReadTimeout('lost reply',request=request)
                return httpx.Response(response.status_code,json=response.json())
            with patch.dict(bot.os.environ,{'STATE_DIRECTORY':str(self.root/'telegram-replay')}), \
                 patch.object(bot,'bot_api',side_effect=telegram_api),patch.object(bot,'observed_profile',new=AsyncMock(return_value={})), \
                 patch.object(bot,'remember_incoming',new=AsyncMock()), \
                 patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)), \
                 patch.object(bot,'agent_request',new=AsyncMock(side_effect=AssertionError('Session sent to AI'))):
                with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(bot.bot(control.read(api)),20)
        with patch.object(control,'runtime_request',return_value={'result':{'authorized':True,'telegram_id':11}}) as runtime:
            asyncio.run(run())
            if not allow_delete:
                runtime.assert_not_called()
                self.assertFalse(requests)
                self.assertIn('Не удалось удалить сообщение', sent[-1]['text'])
                return
            runtime.assert_called_once()
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0],requests[1])
        self.assertTrue({1,2,101} <= set(deleted),deleted)
        self.assertNotIn(secret,json.dumps(sent))
        self.assertIn('подключён',sent[-1]['text'])
