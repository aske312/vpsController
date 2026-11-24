import json
from copy import deepcopy
import asyncio
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading
from unittest.mock import AsyncMock
import yaml
from unittest.mock import patch
import unittest

from tests.api import test_assistant_telegram_ux as fixtures
from tests.api.support import api, manager, ROOT
from tests.api.test_mihomo_rules_runtime import profile as export_profile
import assistant_control as control
import assistant_mihomo as mihomo
import assistant_commands as commands


class SubscriptionManagementTests(unittest.TestCase):
    submit = fixtures.TelegramViewsTests.submit
    payload = fixtures.TelegramViewsTests.payload
    save = fixtures.TelegramViewsTests.save
    enabled_bot = fixtures.TelegramViewsTests.enabled_bot
    approve = fixtures.TelegramViewsTests.approve

    def setUp(self):
        fixtures.TelegramViewsTests.setUp(self)
        self.item['devices'][0].update(name='Common', routing={'routing_scope':'selected','vpn_services':'youtube'})
        self.item['devices'][1].update(scope='hwid', hwid_hash='b'*64, routing={}, personal_rule_ids=['existing'])
        settings = control.read(api)
        settings['accounts'][0].update(role='user', products=['mihomo'], mihomo_profile_id='own', permission_overrides={})
        self.enterContext(patch.object(control, "read", return_value=settings))
        self.enterContext(patch.object(api, 'get_application_operation', return_value={'state':'queued'}))

    def request(self, method, path, body=None, *args, **kwargs):
        if path == '/routing/schema':
            return {'schema':[{'key':'direct_local_network','type':'boolean'}],
                    'values':{'connection_mode':'proxy','direct_local_network':True,'structured_policy':'{"groups":[]}','private_domains':'secret.example'}}
        if path.endswith('/subscription/rotate'): return {'id':'d'*32,'state':'queued'}
        return fixtures.TelegramViewsTests.request(self, method, path, body, *args, **kwargs)

    def choose(self, response, label='Продолжить'):
        self.assertEqual(response.status_code, 200, response.text)
        command = next(button['command'] for row in response.json()['buttons'] for button in row if button['text'] == label)
        return self.submit(command)

    def test_buttons_use_shared_catalog_and_platform_rules_without_channel_or_limit_controls(self):
        with patch.object(mihomo, 'request', side_effect=self.request):
            screen = self.submit('/manage').json()
            self.assertIn('/manage targets',str(screen['buttons']))
            self.assertNotIn('channel',str(screen)); self.assertNotIn('limits',str(screen))
            self.assertEqual(self.submit('/manage device laptop').status_code,200)
            services = self.submit('/manage services laptop').json()
            self.assertIn('YouTube',str(services)); self.assertIn('✓ YouTube',str(services))
            rules = self.submit('/manage rules laptop').json()
            self.assertIn('Локальная сеть',str(rules)); self.assertNotIn('Геолокация Windows',str(rules))
            safe = mihomo.call(api,'11','GET','/routing/schema',{})
            self.assertTrue(safe['profile_defaults']['direct_local_network'])
            self.assertNotIn('secret.example',json.dumps(safe))
            self.assertNotIn('structured_policy',safe['profile_defaults'])

    def test_edit_requires_confirmation_and_preserves_identity_without_duplicate_execution(self):
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            command = '/manage set laptop connection_mode tun ' + self.item['revision']
            first = self.submit(command,'e'*32)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(self.submit(command,'e'*32).json(), first.json())
            self.assertEqual(len(commands.pending_actions(api,'11')), 1)
            self.assertFalse(any(call.args[0] == 'PATCH' for call in transport.call_args_list))
            self.assertIn('Proxy → TUN', first.json()['text'])
            self.assertIn('Настройки ещё не изменены', first.json()['text'])
            self.assertIn('применятся позже', self.choose(first).json()['text'])
            self.item['revision']='c'*64
            self.assertEqual(self.choose(first).status_code, 200)
            writes = [call for call in transport.call_args_list if call.args[0] == 'PATCH']
            self.assertEqual(len(writes),1)
            body = writes[0].args[2]
            device = body['devices'][1]
            self.assertEqual(device['routing'],{'connection_mode':'tun','tun_enabled':True,'tun_force':False})
            self.assertEqual(device['hwid_hash'],'b'*64)
            self.assertEqual(device['personal_rule_ids'],['existing'])
            self.assertEqual(body['devices'][0]['routing'],self.item['devices'][0]['routing'])
            self.assertNotIn('connections',body); self.assertNotIn('limits',body)

    def test_scope_revision_and_live_permissions_are_checked_before_preparation(self):
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            for command, status in [('/manage device foreign',404),
                                    ('/manage set laptop connection_mode tun '+'c'*64,409),
                                    ('/manage set laptop channels transport-tuic '+'a'*64,403),
                                    ('/manage set laptop traffic_gb 0 '+'a'*64,403),
                                    ('/manage set laptop windows_geolocation 1 '+'a'*64,403)]:
                response = self.submit(command)
                self.assertEqual(response.status_code,status,response.text)
            settings=control.read(api)
            settings['accounts'][0]['permission_overrides']={'subscription_routing':False}
            self.assertEqual(self.submit('/manage set laptop connection_mode tun '+'a'*64).status_code,403)
            self.assertEqual(self.submit('/manage targets').status_code,403)
            self.assertEqual(self.submit('/manage').status_code,200)
            self.assertNotIn('/manage targets',str(self.submit('/manage').json()))
            settings=control.read(api)
            settings['accounts'][0]['permission_overrides']={'subscription_manage':False}
            self.assertEqual(self.submit('/manage').status_code,403)
            self.assertNotIn('/manage',str(self.submit('/subscription').json()['buttons']))
            self.assertFalse(any(call.args[0]=='PATCH' for call in transport.call_args_list))

    def test_service_selection_and_mode_match_panel_fields(self):
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            self.choose(self.submit('/manage service laptop telegram 1 '+'a'*64))
            body=next(c.args[2] for c in transport.call_args_list if c.args[0]=='PATCH')
            self.assertEqual(set(body['devices'][1]['routing']['vpn_services'].split(',')),{'youtube','telegram'})
            self.assertEqual(self.choose(self.submit('/manage set common routing_scope all '+'a'*64)).status_code,200)
            bodies=[c.args[2] for c in transport.call_args_list if c.args[0]=='PATCH']
            self.assertTrue(any(b['devices'][0]['routing'].get('mode')=='rule' for b in bodies))
            changed = next(b for b in bodies if b.get('routing'))
            self.assertEqual(changed['routing'],changed['devices'][0]['routing'])

    def test_prepared_common_routing_changes_generated_config_for_inheriting_device(self):
        self.item['routing']={'routing_scope':'selected','vpn_services':'youtube','connection_mode':'proxy'}
        self.item['devices'][0]['routing']=dict(self.item['routing'])
        self.item['devices'][1]['personal_rule_ids']=[]
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            response=self.submit('/manage set common connection_mode tun '+'a'*64)
            self.assertEqual(response.status_code,200,response.text)
            self.choose(response)
            body=next(c.args[2] for c in transport.call_args_list if c.args[0]=='PATCH')
        item=export_profile()
        item.update(id='own',name='Family',devices=deepcopy(body['devices']),routing=body['routing'])
        item['connections'][0]['device_id']='laptop'
        with patch.object(manager,'SUBMODULE_ROOT',ROOT/'protocol-images/mihomo/modules'), \
             patch.object(manager,'public_endpoint',return_value='127.0.0.1'), \
             patch.object(manager,'module_is_installed',return_value=True), \
             patch.object(manager,'routing_settings',return_value={}), \
             patch.object(manager,'personal_rule_lines_for_device',return_value=[]):
            generated=yaml.safe_load(manager.render_profile(item,'laptop'))
        self.assertTrue(generated['tun']['enable'])
        self.assertEqual(generated['rules'][-1],'MATCH,DIRECT')
        self.assertIn('DOMAIN-SUFFIX,youtube.com,'+manager.MIHOMO_PROXY_GROUP,generated['rules'])

    def test_bot_dispatch_and_back_navigation_respect_management_permission(self):
        bot=fixtures.bot
        detail=bot.access_details.set({'permissions':['subscription_read','subscription_manage'],'sections':{'subscription':True}})
        view=bot.command_view.set(None)
        try:
            with patch.object(bot,'server_command',new=AsyncMock(return_value='settings')) as call:
                self.assertEqual(asyncio.run(bot.reply({}, {'id':11}, '/manage','subscription')),'settings')
                self.assertEqual(call.call_args.args[-1],'/manage')
                actions={}; bot.inline_keyboard('settings','/manage','subscription',actions)
                self.assertIn('/subscription',actions.values())
                self.assertNotIn('/menu admin',actions.values())
                bot.access_details.set({'permissions':['subscription_read'],'sections':{'subscription':True}})
                call.reset_mock()
                asyncio.run(bot.reply({}, {'id':11}, '/manage','subscription'))
                call.assert_not_called()
        finally:
            bot.access_details.reset(detail); bot.command_view.reset(view)

    def test_rotation_warns_about_old_links_and_runs_only_after_confirmation(self):
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            response=self.submit('/manage rotate '+'a'*64,'f'*32)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(self.submit('/manage rotate '+'a'*64,'f'*32).json(),response.json())
            self.assertEqual(len(commands.pending_actions(api,'11')), 1)
            self.assertIn('Старая ссылка', response.json()['text'])
            self.assertFalse(any(call.args[0] == 'POST' for call in transport.call_args_list))
            self.choose(response)
            self.choose(response)
            writes=[call for call in transport.call_args_list if call.args[0]=='POST']
            self.assertEqual(len(writes),1)
            self.assertEqual(writes[0].args[1],'/profiles/own/subscription/rotate')

    def test_completed_failed_and_pending_operations_have_plain_status(self):
        for state, text in [('succeeded','Изменения применены'),('failed','Не удалось применить'),('running','применятся позже'),('unknown','результат пока неизвестен')]:
            with self.subTest(state=state), patch.object(mihomo,'request',side_effect=self.request), \
                 patch.object(api,'get_application_operation',return_value={'state':state}):
                response=self.choose(self.submit('/manage set laptop route_dns 1 '+'a'*64))
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn(text,response.json()['text'])
                self.assertNotIn('/operation',response.text)

    def test_lost_mutation_response_is_not_retried_or_reported_as_applied(self):
        def lost(method,path,body=None,*args,**kwargs):
            if method=='PATCH': raise api.HTTPException(503,'PRIVATE DEBUG /operation '+'d'*32)
            return self.request(method,path,body,*args,**kwargs)
        with patch.object(mihomo,'request',side_effect=lost) as transport:
            command='/manage set laptop route_dns 1 '+'a'*64
            first=self.submit(command,'e'*32)
            completed = self.choose(first)
            self.assertIn('применятся позже', completed.json()['text'])
            self.assertNotIn('PRIVATE', completed.text)
            self.choose(first)
            self.assertEqual(sum(c.args[0]=='PATCH' for c in transport.call_args_list),1)
            self.assertEqual(commands.pending_actions(api,'11'),[])

    def test_cancel_closes_only_this_change_and_never_sends_a_mutation(self):
        with patch.object(mihomo, 'request', side_effect=self.request) as transport:
            preview = self.submit('/manage set laptop route_dns 1 ' + 'a'*64)
            cancelled = self.choose(preview, 'Отменить')
            self.assertIn('отменено', cancelled.json()['text'])
            self.assertTrue(cancelled.json()['decision_complete'])
            self.assertIn('/manage device laptop', str(cancelled.json()['buttons']))
            self.assertEqual(commands.pending_actions(api, '11'), [])
            self.assertIn('закрыт', self.choose(preview).json()['text'])
            self.assertFalse(any(call.args[0] == 'PATCH' for call in transport.call_args_list))

    def test_confirmation_rechecks_revision_expiry_and_live_permissions(self):
        for reason in ('revision', 'expiry', 'permission'):
            with self.subTest(reason=reason), patch.object(mihomo, 'request', side_effect=self.request) as transport:
                self.item['revision'] = 'a'*64
                control.read(api)['accounts'][0]['permission_overrides'] = {}
                preview = self.submit('/manage set laptop route_dns 1 ' + 'a'*64)
                record = commands.pending_action(api, '11')
                if reason == 'revision': self.item['revision'] = 'c'*64
                elif reason == 'permission': control.read(api)['accounts'][0]['permission_overrides'] = {'subscription_routing': False}
                else:
                    file = commands.pending_path(api, '11', code=record['code'])
                    value = control.operations.read(file)
                    control.operations.atomic_json(file, {**value, 'expires': 1})
                rejected = self.choose(preview)
                self.assertTrue(rejected.json()['decision_complete'])
                self.assertNotIn('применены', rejected.json()['text'])
                self.assertFalse(any(call.args[0] == 'PATCH' for call in transport.call_args_list))

    def test_status_button_reads_result_without_sending_the_change_again(self):
        with patch.object(mihomo, 'request', side_effect=self.request) as transport:
            preview = self.submit('/manage set laptop route_dns 1 ' + 'a'*64)
            accepted = self.choose(preview)
            self.assertIn('DNS', accepted.json()['text'])
            with patch.object(api, 'get_application_operation', return_value={'state': 'succeeded'}):
                completed = self.choose(accepted, 'Проверить результат')
                self.assertIn('Изменения применены', completed.json()['text'])
                self.assertIn('Обновите подписку', completed.json()['text'])
                self.choose(preview)
            self.assertEqual(sum(call.args[0] == 'PATCH' for call in transport.call_args_list), 1)

    def test_unchanged_choice_does_not_create_an_unnecessary_confirmation(self):
        with patch.object(mihomo, 'request', side_effect=self.request) as transport:
            response = self.submit('/manage set laptop connection_mode proxy ' + 'a'*64)
            self.assertIn('уже выбрана', response.json()['text'])
            self.assertEqual(commands.pending_actions(api, '11'), [])
            self.assertFalse(any(call.args[0] == 'PATCH' for call in transport.call_args_list))

    def test_relinking_account_cannot_confirm_a_change_to_the_previous_profile(self):
        with patch.object(mihomo, 'request', side_effect=self.request) as transport:
            preview = self.submit('/manage set laptop route_dns 1 ' + 'a'*64)
            control.read(api)['accounts'][0]['mihomo_profile_id'] = 'foreign'
            self.assertEqual(self.choose(preview).status_code, 403)
            self.assertFalse(any(call.args[0] == 'PATCH' for call in transport.call_args_list))

    def test_simultaneous_confirmations_have_one_mutation_and_both_show_accepted_status(self):
        gate, release = threading.Barrier(2), threading.Event()
        execute = commands.execute
        def simultaneous(*args, **kwargs):
            gate.wait(5)
            return execute(*args, **kwargs)
        def blocking(method, path, body=None, *args, **kwargs):
            if method == 'PATCH': self.assertTrue(release.wait(5))
            return self.request(method, path, body, *args, **kwargs)
        with patch.object(mihomo, 'request', side_effect=blocking) as transport:
            preview = self.submit('/manage set laptop route_dns 1 ' + 'a'*64)
            with patch.object(commands, 'execute', side_effect=simultaneous), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.choose, preview) for _ in range(2)]
                try:
                    done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                    self.assertTrue(done)
                    first = next(iter(done)).result()
                    self.assertEqual(first.status_code, 200, first.text)
                    self.assertIn('применятся позже', first.json()['text'])
                finally: release.set()
                self.assertTrue(all(future.result(timeout=5).status_code == 200 for future in futures))
            self.assertEqual(sum(call.args[0] == 'PATCH' for call in transport.call_args_list), 1)

    def test_actual_bot_keyboard_has_continue_and_cancel_callbacks_without_technical_text(self):
        bot = fixtures.bot
        with patch.object(mihomo, 'request', side_effect=self.request):
            preview = self.submit('/manage set laptop connection_mode tun ' + 'a'*64).json()
        token = bot.command_view.set(preview)
        try:
            actions = {}
            keyboard = bot.inline_keyboard(preview['text'], '/manage', 'subscription', actions)
            row = keyboard['inline_keyboard'][0]
            self.assertEqual([entry['text'] for entry in row], ['Продолжить', 'Отменить'])
            self.assertTrue(all(len(entry['callback_data'].encode()) <= 64 for entry in row))
            self.assertTrue(any(command.startswith('/manage confirm ') for command in actions.values()))
            self.assertTrue(any(command.startswith('/manage cancel ') for command in actions.values()))
            self.assertNotIn('/confirm', preview['text'])
            self.assertNotIn('/cancel', preview['text'])
        finally: bot.command_view.reset(token)

    def test_request_identity_cannot_be_reused_for_another_change(self):
        with patch.object(mihomo,'request',side_effect=self.request) as transport:
            self.assertEqual(self.submit('/manage set laptop route_dns 1 '+'a'*64,'e'*32).status_code,200)
            self.assertEqual(self.submit('/manage set laptop route_dns 0 '+'a'*64,'e'*32).status_code,409)
            self.assertEqual(sum(c.args[0]=='PATCH' for c in transport.call_args_list),0)

    def test_duplicate_delivery_during_execution_does_not_send_a_second_change(self):
        started, release = threading.Event(), threading.Event()
        def blocking(method,path,body=None,*args,**kwargs):
            if method=='PATCH':
                started.set()
                self.assertTrue(release.wait(5))
            return self.request(method,path,body,*args,**kwargs)
        command='/manage set laptop route_dns 1 '+'a'*64
        with patch.object(mihomo,'request',side_effect=blocking) as transport, ThreadPoolExecutor(max_workers=1) as pool:
            preview = self.submit(command, 'e'*32)
            first=pool.submit(self.choose, preview)
            try:
                self.assertTrue(started.wait(5))
                second=self.choose(preview)
                self.assertEqual(second.status_code,200,second.text)
                self.assertIn('применятся позже',second.json()['text'])
            finally: release.set()
            self.assertEqual(first.result(timeout=5).status_code,200)
            self.assertEqual(sum(c.args[0]=='PATCH' for c in transport.call_args_list),1)
