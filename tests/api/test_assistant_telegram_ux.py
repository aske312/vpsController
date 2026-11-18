import asyncio
import json
import httpx
from unittest.mock import AsyncMock, patch
import unittest

from tests.api import test_assistant_commands as fixtures
from tests.api.support import api, load_module
import assistant_control as control
import assistant_bot_views as views
import assistant_mihomo as mihomo
import assistant_contacts as contacts
import assistant_diagnostics as diagnostics
import assistant_commands as commands

bot = load_module('telegram_ux_bot', 'protocol-images/nodera-bot/bot.py')


class TelegramViewsTests(unittest.TestCase):
    def test_screens_share_one_heading_style_without_reformatting_body(self):
        self.assertEqual(bot.telegram_html(bot.bot_message('🔗 **Моя подписка**\nhttps://example.org/s/test')),
                         '<b>🔗 Моя подписка</b>\n\nhttps://example.org/s/test')

    def test_application_links_have_platform_navigation_and_guest_stays_scoped(self):
        detail=bot.access_details.set({'permissions':['subscriptions_read'],'sections':{'subscription':True}})
        view=bot.command_view.set({'buttons':[[{'text':'Nodera','url':'https://github.com/aske312/NoderaClient'}]]})
        try:
            actions={}; markup=bot.inline_keyboard('Windows','/apps windows','subscription',actions)
            self.assertEqual(set(actions.values()),{'/apps','/menu'})
            self.assertEqual(markup['inline_keyboard'][0][0]['url'],'https://github.com/aske312/NoderaClient')
            bot.command_view.set(None)
            bot.access_details.set({'permissions':[],'sections':{}})
            actions={}; bot.inline_keyboard('My ID','/id',False,actions)
            self.assertEqual(set(actions.values()),{'/menu'})
        finally: bot.access_details.reset(detail); bot.command_view.reset(view)

    payload = fixtures.ServerCommandTests.payload
    save = fixtures.ServerCommandTests.save
    enabled_bot = fixtures.ServerCommandTests.enabled_bot
    approve = fixtures.ServerCommandTests.approve
    call = fixtures.ServerCommandTests.call

    def setUp(self):
        fixtures.ServerCommandTests.setUp(self)
        self.serial=100
        self.item={'id':'own','name':'Family','revision':'a'*64,'common_device_id':'common',
            'devices':[{'id':'common','scope':'common'}, {'id':'laptop','name':'Laptop','os':'windows','client_name':'Nodera'}],
            'connections':[], 'limits':{'traffic_gb':100,'speed_mbps':50,'days':30},
            'limits_status':{'state':'active','used_bytes':1000000000}}

    def submit(self, prompt, request_id=None):
        self.serial+=1
        return self.client.post('/api/assistant/bot-command',headers=self.headers,
            json={'command':prompt,'request_id':request_id or f'{self.serial:032x}'})

    def request(self, method, path, body=None, *args, **kwargs):
        if path=='/profiles':
            if method=='GET': return {'items':[self.item,{'id':'foreign','name':'Private foreign'}]}
            return {'id':'d'*32,'state':'queued'}
        if path=='/modules': return {'items':[{'id':'transport-tuic','name':'TUIC','installed':True}]}
        if path=='/profiles/validate': return {'valid':True}
        if path.endswith('/subscription'): return {'url':'https://example.org/s/own-only'}
        if path.endswith('/stats'): return {'usage':{'devices':{'laptop':{'used_bytes':2*1024**3}}}}
        if method in ('PATCH','DELETE'): return {'id':'d'*32,'state':'queued'}
        raise AssertionError((method,path))

    def test_sections_follow_products_permissions_and_a_live_linked_subscription(self):
        settings=control.read(api)
        account=settings['accounts'][0]
        account.update(role='user',products=['mihomo'],mihomo_profile_id='own',permission_overrides={})
        with patch.object(mihomo,'request',side_effect=self.request):
            cap=views.capabilities(api,settings,'11')
            self.assertEqual(cap['sections'],{'subscription':True,'subscription_setup':False,'telegram':False,'ai':False,'server':False,'admin':False})
            self.item['limits_status']['state']='expired'; views._active.clear()
            self.assertFalse(views.capabilities(api,settings,'11')['sections']['subscription'])
            account['products']=['assistant']; views._active.clear()
            self.assertTrue(views.capabilities(api,settings,'11')['sections']['ai'])
            self.assertFalse(views.capabilities(api,settings,'11')['sections']['subscription'])
        account.update(role='moderator',products=['assistant'],permission_overrides={'server_read':False,'network_read':True,'chat':False})
        cap=views.capabilities(api,settings,'11')
        self.assertTrue(cap['sections']['server']); self.assertFalse(cap['sections']['ai'])
        token=bot.access_details.set(cap)
        try:
            actions={}; bot.inline_keyboard('','/menu server',True,actions)
            self.assertIn('/network',actions.values()); self.assertNotIn('/status',actions.values())
            self.assertNotIn('ai',dict(bot.available_commands()))
        finally: bot.access_details.reset(token)

    def test_guest_sees_only_usable_commands_and_one_skip_button(self):
        token=bot.access_details.set({'permissions':[],'products':[],'sections':{}})
        state=bot.profile_state.set('name')
        view=bot.command_view.set({'buttons':[[{'text':'Пропустить','command':'/skip'}]]})
        try:
            published=dict(bot.available_commands())
            self.assertNotIn('ai',published); self.assertNotIn('status',published); self.assertNotIn('menu',published)
            actions={}; markup=bot.inline_keyboard('Как вас зовут?','/start',False,actions)
            self.assertEqual(list(actions.values()),['/skip'])
            self.assertEqual(sum(map(len,markup['inline_keyboard'])),1)
        finally: bot.access_details.reset(token); bot.profile_state.reset(state); bot.command_view.reset(view)

    def test_apps_are_platform_choices_then_named_download_links(self):
        first=self.submit('/apps').json()
        self.assertEqual(len(first['buttons']),5)
        self.assertNotIn('/apps',first['text'])
        second=self.submit('/apps windows').json()
        self.assertTrue(any('NoderaClient' in item.get('url','') for row in second['buttons'] for item in row))
        self.assertTrue(all('url' in item and 'command' not in item for row in second['buttons'] for item in row))
        self.assertNotIn('/subscription',second['text'])

    def test_subscription_is_scoped_compact_and_devices_have_measured_usage(self):
        settings=control.read(api); settings['accounts'][0].update(role='user',products=['mihomo'],mihomo_profile_id='own')
        with patch.object(control,'read',return_value=settings), patch.object(mihomo,'request',side_effect=self.request):
            link=views.view(api,'11','/subscription')['text']
            self.assertIn('https://example.org/s/own-only',link); self.assertIn('1.00 GB',link)
            status=views.view(api,'11','/profile')['text']
            self.assertNotIn('/devices',status); self.assertNotIn('/subscription',status)
            devices=views.view(api,'11','/devices')['text']
            self.assertIn('💻',devices); self.assertIn('2 GiB',devices)
            self.assertNotIn('common',devices); self.assertNotIn('Private foreign',devices)
            with self.assertRaises(mihomo.HTTPException): views.view(api,'11','/subscription foreign')

    def test_summaries_are_short_unknowns_stay_unknown_and_logs_are_files(self):
        value={'findings':[{'id':'ssh','state':'risk','risk':'SSH password enabled'}],
               'server':{'public_ip':'192.0.2.1'},'route':{'label':'Direct'},'resolvers':['1.1.1.1'], 'listeners':[],
               'application_security':{'admin_password_strong':True},'ssh':{},'firewall':{},'updates':{},
               'lines':['key='+control.read(api)['control_token']], 'api_key':'secret-key'}
        with patch.object(diagnostics,'call',return_value=value), patch.object(control,'runtime_request',side_effect=AssertionError('AI called')):
            for command in ('/network','/security','/diagnostics'):
                output=self.submit(command).json()
                self.assertLess(len(output['text']),1000)
                self.assertNotIn('secret-key',json.dumps(output))
            logs=self.submit('/logs').json()
            self.assertTrue(logs['buttons']); self.assertNotIn('document',logs)
            file=self.submit('/logfile application').json()['document']
            self.assertEqual(file['name'],'logs-application.txt')
            self.assertNotIn(control.read(api)['control_token'],file['text'])
        with patch.object(api,'overview',return_value={'server':{'name':'Test','os':'Debian'},'resources':{'cpu_percent':4,'cpu_count':2,'memory_total':4*1024**3,'memory_available':2*1024**3,'network_rx':1024**3,'uptime_s':5000}}):
            text=self.submit('/status').json()['text']
            for field in ('Debian','ОЗУ','CPU','NET','Uptime'): self.assertIn(field,text)

    def test_administration_drafts_are_durable_and_only_confirmation_changes_mihomo(self):
        with patch.object(mihomo,'request',side_effect=self.request) as request:
            self.assertEqual(self.submit('/admin name own').status_code,200)
            identity='e'*32
            first=self.submit('/admin input Renamed',identity)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(self.submit('/admin input Renamed',identity).json(),first.json())
            self.assertFalse(any(call.args[0]=='PATCH' for call in request.call_args_list))
            pending=commands.pending_actions(api,'11')
            self.assertEqual(len(pending),1)
            self.assertEqual(pending[0]['changes']['name'],'Renamed')
            self.assertEqual(self.submit('/confirm '+pending[0]['code']).status_code,200)
            patches=[call for call in request.call_args_list if call.args[0]=='PATCH']
            self.assertEqual(len(patches),1)
            self.assertEqual(patches[0].args[2]['expected_revision'],'a'*64)

    def test_admin_creation_asks_name_then_channel_and_validates_before_confirmation(self):
        with patch.object(mihomo,'request',side_effect=self.request) as request:
            self.assertEqual(self.submit('/admin create').status_code,200)
            response=self.submit('/admin input New subscription')
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('/admin channel transport-tuic',str(response.json()['buttons']))
            response=self.submit('/admin channel transport-tuic')
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(any(call.args[:2]==('POST','/profiles/validate') for call in request.call_args_list))
            self.assertFalse(any(call.args[:2]==('POST','/profiles') for call in request.call_args_list))
            pending=commands.pending_actions(api,'11')
            self.assertEqual(pending[0]['changes']['channels'],['transport-tuic'])
            self.assertEqual(self.call('/admin',actor=22).status_code,403)

    def test_admin_does_not_overwrite_a_changed_profile_or_accept_fractional_limits(self):
        with patch.object(mihomo,'request',side_effect=self.request):
            self.submit('/admin days own')
            self.assertEqual(self.submit('/admin input 1.5').status_code,422)
            self.assertEqual(self.submit('/admin input 36501').status_code,422)
            self.item['revision']='b'*64
            self.assertEqual(self.submit('/admin input 30').status_code,409)
            self.assertEqual(commands.pending_actions(api,'11'),[])

    def test_onboarding_sends_one_message_per_step_and_deletes_previous_prompts(self):
        self.approve(False)
        actual=httpx.AsyncClient
        sent,deleted,published=[],[],[]
        async def run():
            polls=0
            async def telegram(settings,method,**payload):
                nonlocal polls
                if method=='getWebhookInfo': return {}
                if method=='setMyCommands': published.append(payload['commands']); return True
                if method=='getUpdates':
                    if polls:
                        async with asyncio.timeout(6):
                            while len(sent)<polls: await asyncio.sleep(.01)
                    polls+=1
                    if polls>4: raise asyncio.CancelledError()
                    return [{'update_id':polls,'message':{'message_id':polls,'from':{'id':11},'chat':{'id':11,'type':'private'},'text':('/start','Name','29.02','/myprofile')[polls-1]}}]
                if method=='sendMessage':
                    sent.append(payload)
                    return {'message_id':100+len(sent),'chat':{'id':11,'type':'private'}}
                if method=='deleteMessage': deleted.append(payload); return True
                return True
            def transport(request):
                response=self.client.post(request.url.path,headers=dict(request.headers),json=json.loads(request.content))
                return httpx.Response(response.status_code,json=response.json())
            with patch.dict(bot.os.environ,{'STATE_DIRECTORY':str(self.root/'onboarding')}), \
                 patch.object(bot,'bot_api',side_effect=telegram),patch.object(bot,'observed_profile',new=AsyncMock(return_value={})), \
                 patch.object(bot,'remember_incoming',new=AsyncMock()), \
                 patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)), \
                 patch.object(bot,'agent_request',new=AsyncMock(side_effect=AssertionError('Profile called AI'))):
                with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(bot.bot(control.read(api)),15)
        asyncio.run(run())
        self.assertEqual(len(sent),4)
        self.assertIn('Ваш помощник в Telegram',sent[0]['text'])
        self.assertTrue({1,2,3,4} <= {row['message_id'] for row in deleted})
        for message in sent[:2]:
            buttons=message['reply_markup']['inline_keyboard']
            self.assertEqual([item['text'] for row in buttons for item in row],['Пропустить'])
            self.assertNotIn('/skipall',message['text'])
        self.assertNotIn('/menu',sent[2]['text'])
        self.assertNotIn('Выберите действие',''.join(row['text'] for row in sent))
        self.assertTrue({101,102} <= {row['message_id'] for row in deleted})
        self.assertTrue(all(row['chat_id']==11 for row in deleted))
        self.assertTrue(all(item['command'] not in ('models','ai','subscription','status') for row in published for item in row))

    def test_log_export_uses_telegram_multipart_with_bounded_utf8_bytes(self):
        actual=httpx.AsyncClient; requests=[]
        def transport(request):
            requests.append(request)
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1,'chat':{'id':11},'caption':'Logs'}})
        async def run():
            with patch.object(bot.httpx,'AsyncClient',side_effect=lambda **kw:actual(transport=httpx.MockTransport(transport),**kw)), \
                 patch.object(bot,'remember_message',new=AsyncMock()):
                await bot.bot_api({'bot_token':'fixture'},'sendDocument',chat_id=11,document={'name':'logs-system.txt','text':'Тест журнала'},caption='Logs')
        asyncio.run(run())
        self.assertIn('multipart/form-data',requests[0].headers['content-type'])
        self.assertIn(b'logs-system.txt',requests[0].content)
        self.assertIn('Тест журнала'.encode(),requests[0].content)
