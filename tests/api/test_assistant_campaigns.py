import copy
import json
import tempfile
import threading
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.api.support import api, load_module
import assistant_campaigns as campaigns
import assistant_campaign_delivery as delivery
import assistant_contacts as contacts
import assistant_control as control

bot=load_module('campaign_test_bot','protocol-images/nodera-bot/bot.py')


def question(kind='choice'):
    return {'kind':kind,'text':'Как работает подключение?','options':['Хорошо','Плохо'] if kind=='choice' else [],'multiple':False}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env=types.SimpleNamespace(DATA_DIR=self.root)
        self.settings={'bot_token':'123456:synthetic-secret','modules':{'nodera-bot':True},'accounts':[
            {'telegram_id':11,'role':'owner','name':'Владелец'}, {'telegram_id':22,'role':'user','name':'Пользователь','mihomo_profile_id':'profile'},
            {'telegram_id':33,'role':'user'}, {'telegram_id':44,'role':'user'}, {'telegram_id':55,'role':'user'}]}
        self.profiles={str(i):{'snapshot':{'telegram_id':i,'seen_at':1,'name':str(i)}} for i in [11,22,33,55,66]}
        self.profiles['33']['blocked']=True;self.profiles['55']['notices_disabled']=True
        self.now=1800000000.;self.calls=[];self.sequence=100;self.responses=[]
        self.enterContext(patch.object(control,'read',side_effect=lambda _:self.settings))
        self.enterContext(patch.object(campaigns,'ready',return_value=True))
        self.enterContext(patch.object(contacts,'profiles',side_effect=lambda *_:copy.deepcopy(self.profiles)))
        self.enterContext(patch.object(contacts,'observed',return_value=[]))
        self.enterContext(patch.object(contacts,'profile',side_effect=lambda _,settings,actor:copy.deepcopy(self.profiles.get(str(actor),{}))))
        self.enterContext(patch.object(contacts,'save_profile',side_effect=lambda _,settings,actor,value:self.profiles.__setitem__(actor,copy.deepcopy(value))))
        self.enterContext(patch.object(campaigns.time,'time',side_effect=lambda:self.now))
        self.sender=self.enterContext(patch.object(delivery,'telegram',side_effect=self.send))

    def send(self, token, method, payload):
        self.calls.append((token,method,payload));self.sequence+=1
        if self.responses:return self.responses.pop(0)
        return {'state':'sent','message_id':self.sequence,'poll_id':str(self.sequence) if method=='sendPoll' else None}

    def draft(self, kind='message', audience=None, questions=None):
        return campaigns.Draft(id=uuid.uuid4().hex,kind=kind,title='Важное сообщение',text='Секретный текст объявления',
            audience=audience or {'mode':'selected','ids':[22]},questions=questions if questions is not None else [] if kind=='message' else [question()])

    def launch(self, draft=None):
        saved=campaigns.save(self.env,draft or self.draft())
        preview=campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=saved['revision']))
        result=campaigns.launch(self.env,saved['id'],campaigns.Launch(revision=saved['revision'],confirmation=preview['confirmation']))
        return result,preview

    def drain(self, count=20):
        for _ in range(count):
            self.now+=2;delivery.deliver(self.env)

    def rows(self, identity):
        with campaigns.database(self.env) as db:return [dict(row) for row in db.execute('SELECT * FROM deliveries WHERE campaign=? ORDER BY step',(identity,))]

    def event(self, **values):
        return campaigns.receive(self.env,self.settings,'22',campaigns.Event(**values))

    def test_validates_poll_survey_lengths_and_explicit_selection(self):
        for change in [{'text':' '},{'kind':'poll','questions':[question('text')]},{'kind':'survey','questions':[]},
                       {'audience':{'mode':'selected','ids':[]}},{'questions':[question()]},
                       {'title':'😀'*120,'text':'😀'*1800}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):campaigns.Draft.model_validate({**self.draft().model_dump(),**change})
        for change in [{'options':['Да','Да']},{'options':['a']},{'options':['😀'*51,'Нет']},{'text':'😀'*151}]:
            with self.assertRaises(ValidationError):campaigns.Question.model_validate({**question(),**change})
        with self.assertRaises(ValidationError):campaigns.Audience(mode='selected',ids=[True])

    def test_preview_filters_contact_proof_blocks_optouts_and_subscription_members(self):
        saved=campaigns.save(self.env,self.draft(audience={'mode':'all'}))
        preview=campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=1))
        self.assertEqual({r['telegram_id'] for r in preview['recipients']},{11,22,66})
        self.assertEqual({r['excluded_reason'] for r in preview['excluded']},{'blocked','muted','not_started'})
        saved=campaigns.save(self.env,self.draft(audience={'mode':'subscribers'}))
        self.assertEqual([r['telegram_id'] for r in campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=1))['recipients']],[22])
        self.sender.assert_not_called()

    def test_save_launch_and_repeated_launch_are_idempotent_and_content_is_encrypted(self):
        draft=self.draft();saved=campaigns.save(self.env,draft)
        self.assertEqual(campaigns.save(self.env,draft)['revision'],saved['revision'])
        preview=campaigns.preview(self.env,draft.id,campaigns.Revision(revision=1))
        payload=campaigns.Launch(revision=1,confirmation=preview['confirmation'])
        campaigns.launch(self.env,draft.id,payload);campaigns.launch(self.env,draft.id,payload)
        self.assertEqual(len(self.rows(draft.id)),1)
        self.drain();self.assertEqual(len(self.calls),1)
        self.assertEqual(campaigns.detail(self.env,draft.id)['state'],'completed')
        raw=(campaigns.folder(self.env)/'campaigns.sqlite3').read_bytes()
        self.assertNotIn(draft.text.encode(),raw);self.assertNotIn(draft.title.encode(),raw)

    def test_preview_revision_audience_and_expiry_are_checked_again_at_send(self):
        saved=campaigns.save(self.env,self.draft());p=campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=1))
        self.profiles['22']['notices_disabled']=True
        with self.assertRaises(HTTPException):campaigns.launch(self.env,saved['id'],campaigns.Launch(revision=1,confirmation=p['confirmation']))
        self.profiles['22']['notices_disabled']=False;self.now+=601
        with self.assertRaises(HTTPException):campaigns.launch(self.env,saved['id'],campaigns.Launch(revision=1,confirmation=p['confirmation']))
        p=campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=1))
        edited=campaigns.Draft.model_validate({**{k:saved[k] for k in campaigns.Draft.model_fields},'text':'Изменённый текст'})
        campaigns.save(self.env,edited)
        with self.assertRaises(HTTPException):campaigns.launch(self.env,saved['id'],campaigns.Launch(revision=1,confirmation=p['confirmation']))
        self.sender.assert_not_called()

    def test_unknown_and_abandoned_inflight_are_not_retried_but_429_is(self):
        row,_=self.launch();self.responses=[{'state':'unknown'}];self.drain(3)
        self.assertEqual(self.rows(row['id'])[0]['state'],'unknown');self.assertEqual(len(self.calls),1)
        row,_=self.launch()
        with campaigns.database(self.env) as db:db.execute("UPDATE deliveries SET state='sending' WHERE campaign=?",(row['id'],))
        self.drain(2);self.assertEqual(self.rows(row['id'])[0]['state'],'unknown');self.assertEqual(len(self.calls),1)
        row,_=self.launch();self.responses=[{'state':'pending','retry_after':30}]
        self.now+=2;delivery.deliver(self.env);count=len(self.calls)
        self.now+=10;delivery.deliver(self.env);self.assertEqual(len(self.calls),count)
        self.now+=30;delivery.deliver(self.env);self.assertEqual(self.rows(row['id'])[0]['state'],'sent')

    def test_frozen_audience_live_block_and_bot_identity_changes(self):
        row,_=self.launch();self.profiles['22']['blocked']=True;self.drain()
        self.assertEqual(self.rows(row['id'])[0]['state'],'skipped');self.sender.assert_not_called()
        self.profiles['22']['blocked']=False;row,_=self.launch()
        self.settings['bot_token']='999999:another-bot';self.drain()
        self.assertEqual(self.rows(row['id'])[0]['state'],'cancelled');self.sender.assert_not_called()

    def test_same_bot_token_rotation_keeps_the_campaign_and_receivers(self):
        row,_=self.launch();self.settings['bot_token']='123456:rotated-secret';self.drain()
        self.assertEqual(self.calls[0][0],self.settings['bot_token'])
        self.assertEqual(campaigns.detail(self.env,row['id'])['delivery_counts']['sent'],1)

    def test_poll_answer_is_bound_to_recipient_revoting_and_retraction_replace_counts(self):
        row,_=self.launch(self.draft('poll'));self.drain()
        sent=next(x for x in self.rows(row['id']) if x['step']==0)
        event=campaigns.Event(kind='poll',poll_id=sent['poll_id'],option_ids=[0],update_id=100)
        campaigns.receive(self.env,self.settings,'11',event)
        self.assertEqual(campaigns.detail(self.env,row['id'])['respondents'],0)
        self.event(**event.model_dump());self.event(**event.model_dump())
        self.assertEqual(campaigns.detail(self.env,row['id'])['results'][0]['counts'],[1,0])
        self.event(kind='poll',poll_id=sent['poll_id'],option_ids=[1],update_id=101)
        self.assertEqual(campaigns.detail(self.env,row['id'])['results'][0]['counts'],[0,1])
        self.event(kind='poll',poll_id=sent['poll_id'],option_ids=[],update_id=102)
        self.assertEqual(campaigns.detail(self.env,row['id'])['respondents'],0)
        with self.assertRaises(HTTPException):self.event(kind='poll',poll_id=sent['poll_id'],option_ids=[8],update_id=103)

    def test_survey_advances_once_and_text_edits_are_private_and_bound_to_own_answer(self):
        row,_=self.launch(self.draft('survey',questions=[question(),question('text')]))
        self.drain();self.assertEqual(len(self.rows(row['id'])),2)
        poll=next(x for x in self.rows(row['id']) if x['step']==0)
        payload={'kind':'poll','poll_id':poll['poll_id'],'option_ids':[0],'update_id':300}
        self.event(**payload);self.event(**payload);self.drain()
        self.assertEqual(len(self.rows(row['id'])),3)
        text=next(x for x in self.rows(row['id']) if x['step']==1)
        self.assertFalse(self.event(kind='reply',reply_to=text['message_id']+1,text='Не этот вопрос',update_id=301)['handled'])
        self.event(kind='reply',reply_to=text['message_id'],message_id=777,text='Личный ответ анкеты',update_id=302)
        self.event(kind='reply',reply_to=text['message_id'],message_id=778,text='Второе сообщение',update_id=303)
        self.event(kind='reply',reply_to=text['message_id'],message_id=777,text='Исправленный личный ответ',update_id=304,edited=True)
        result=campaigns.detail(self.env,row['id'])
        self.assertEqual(result['completed_responses'],1)
        self.assertEqual(result['answers'][1]['answer'],'Исправленный личный ответ')
        self.assertNotIn('Личный ответ'.encode(),(campaigns.folder(self.env)/'campaigns.sqlite3').read_bytes())

    def test_answer_racing_send_ack_is_applied_after_poll_identity_is_known(self):
        row,_=self.launch(self.draft('survey',questions=[question(),question('text')]))
        def race(token,method,payload):
            if method=='sendPoll':
                self.event(kind='poll',poll_id='race-poll',option_ids=[1],update_id=400)
                return {'state':'sent','message_id':900,'poll_id':'race-poll'}
            return self.send(token,method,payload)
        self.sender.side_effect=race;self.drain()
        result=campaigns.detail(self.env,row['id'])
        self.assertEqual(result['results'][0]['counts'],[0,1]);self.assertEqual(len(self.rows(row['id'])),3)

    def test_close_cancels_unsent_stops_native_polls_and_rejects_late_votes(self):
        row,_=self.launch(self.draft('poll'));self.drain()
        poll=next(x for x in self.rows(row['id']) if x['step']==0)
        campaigns.close(self.env,row['id']);self.drain()
        self.assertTrue(any(call[1]=='stopPoll' for call in self.calls))
        self.event(kind='poll',poll_id=poll['poll_id'],option_ids=[0],update_id=500)
        self.assertEqual(campaigns.detail(self.env,row['id'])['respondents'],0)
        campaigns.remove(self.env,row['id'])
        with self.assertRaises(HTTPException):campaigns.detail(self.env,row['id'])

    def test_unsubscribe_is_ordered_and_prevents_future_steps(self):
        row,_=self.launch()
        self.event(kind='preference',enabled=False,update_id=600)
        self.event(kind='preference',enabled=True,update_id=599)
        self.assertTrue(self.profiles['22']['notices_disabled']);self.drain();self.sender.assert_not_called()
        self.assertEqual(self.rows(row['id'])[0]['state'],'cancelled')

    def test_lost_or_tampered_key_never_creates_a_replacement_or_sends(self):
        row,_=self.launch();key=control.directory(self.env)/'.privacy-key';original=key.read_bytes();key.unlink()
        with self.assertRaises(HTTPException):campaigns.detail(self.env,row['id'])
        with self.assertRaises(HTTPException):campaigns.save(self.env,self.draft())
        self.assertFalse(key.exists());key.write_bytes(b'x'*32)
        with self.assertRaises(ValueError):campaigns.detail(self.env,row['id'])
        key.write_bytes(original);self.assertEqual(campaigns.detail(self.env,row['id'])['title'],row['title'])

    def test_parallel_workers_cannot_claim_the_same_delivery(self):
        row,_=self.launch();threads=[threading.Thread(target=delivery.deliver,args=(self.env,)) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(len(self.calls),1);self.assertEqual(self.rows(row['id'])[0]['state'],'sent')

    def test_opt_out_during_poll_send_closes_the_in_flight_poll(self):
        row,_=self.launch(self.draft('poll'));self.drain(1)
        original=self.send
        def send_and_opt_out(token,method,payload):
            result=original(token,method,payload)
            if method=='sendPoll':self.event(kind='preference',update_id=999,enabled=False)
            return result
        self.sender.side_effect=send_and_opt_out
        self.drain(1)
        self.assertEqual(self.rows(row['id'])[1]['close_state'],'pending')
        self.drain(1)
        self.assertEqual(self.calls[-1][1],'stopPoll')

    def test_draft_without_configured_bot_is_adopted_on_configuration(self):
        self.settings['bot_token']=''
        saved=campaigns.save(self.env,self.draft())
        self.settings['bot_token']='123456:new-secret'
        self.assertEqual(campaigns.overview(self.env)['items'][0]['id'],saved['id'])
        preview=campaigns.preview(self.env,saved['id'],campaigns.Revision(revision=saved['revision']))
        self.assertEqual([item['telegram_id'] for item in preview['recipients']],[22])

    def test_owner_endpoints_reject_bot_bearer_and_anonymous_access(self):
        self.enterContext(patch.object(api,'DATA_DIR',self.root))
        client=TestClient(api.app)
        for headers in [{},{'Authorization':'Bearer synthetic-owner-token','X-MCP-Actor':'11'}]:
            self.assertEqual(client.get('/api/assistant/campaigns',headers=headers).status_code,401)
            self.assertEqual(client.post('/api/assistant/campaigns',json=self.draft().model_dump(),headers=headers).status_code,401)
        api.app.dependency_overrides[api.require_token]=lambda:None
        self.addCleanup(api.app.dependency_overrides.clear)
        self.assertEqual(client.get('/api/assistant/campaigns').status_code,200)


class BotCampaignTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_updates_and_text_answers_are_intercepted_before_ai(self):
        settings={'bot_token':'123456:synthetic','control_token':'gateway'}
        response=types.SimpleNamespace(status_code=200,json=lambda:{'handled':True,'text':''})
        client=AsyncMock();client.post.return_value=response
        with patch.object(bot,'identify',AsyncMock(return_value=False)),patch.object(bot.httpx,'AsyncClient') as factory,patch.object(bot,'agent_request',AsyncMock()) as ai:
            factory.return_value.__aenter__.return_value=client
            self.assertTrue(await bot.campaign_update(settings,{'update_id':1,'poll_answer':{'user':{'id':22},'poll_id':'poll','option_ids':[0]}}))
            message={'chat':{'id':22,'type':'private'},'from':{'id':22},'text':'Ответ анкеты','message_id':10,
                     'reply_to_message':{'message_id':8,'from':{'id':123456}}}
            self.assertTrue(await bot.campaign_update(settings,{'update_id':2,'message':message}))
            self.assertTrue(await bot.campaign_update(settings,{'update_id':3,'edited_message':message}))
            self.assertTrue(client.post.call_args.kwargs['json']['edited']);ai.assert_not_awaited()
            self.assertEqual(client.post.call_args.kwargs['headers']['X-MCP-Actor'],'22')

    async def test_normal_messages_and_other_bots_replies_keep_the_normal_path(self):
        settings={'bot_token':'123456:synthetic','control_token':'gateway'}
        with patch.object(bot,'identify',AsyncMock()) as identify:
            self.assertFalse(await bot.campaign_update(settings,{'update_id':1,'message':{'chat':{'id':22,'type':'private'},'from':{'id':22},'text':'Обычный вопрос'}}))
            identify.assert_not_awaited()


if __name__=='__main__':unittest.main()
