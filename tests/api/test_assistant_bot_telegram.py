import asyncio
import json
import unittest
import sys
import types
from unittest.mock import AsyncMock, Mock, patch

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
        actual=httpx.AsyncClient; sent=[]; deleted=[]; requests=[]; secret='1'+'b'*110
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
                if method=='deleteMessage': deleted.append(payload['message_id'])
                return True
            def transport(request):
                response=self.client.post(request.url.path,headers=dict(request.headers),json=json.loads(request.content))
                if request.url.path.endswith('/bot-telegram'):
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
            runtime.assert_called_once()
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0],requests[1])
        self.assertTrue({1,2,101} <= set(deleted),deleted)
        self.assertNotIn(secret,json.dumps(sent))
        self.assertIn('подключён',sent[-1]['text'])
