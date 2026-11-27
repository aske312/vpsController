"""Leased campaign delivery. Ambiguous sends are never automatically repeated."""
import asyncio
import fcntl
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

import assistant_campaigns as campaigns
import assistant_contacts as contacts
import assistant_control as control

_guard=threading.Lock()


def telegram(token, method, payload):
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
        data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:result=json.loads(response.read(256*1024))
    except urllib.error.HTTPError as error:
        try:result=json.loads(error.read(65536))
        except (ValueError,OSError):return {'state':'unknown'}
    except (OSError,ValueError):return {'state':'unknown'}
    if result.get('ok'):
        message=result.get('result') or {}
        if method=='stopPoll':return {'state':'sent'}
        if type(message.get('message_id'))is not int:return {'state':'unknown'}
        if method=='sendPoll' and not isinstance(message.get('poll',{}).get('id'),str):return {'state':'unknown'}
        return {'state':'sent','message_id':message['message_id'],'poll_id':message.get('poll',{}).get('id')}
    retry=result.get('parameters',{}).get('retry_after')
    if result.get('error_code')==429 and type(retry)is int:return {'state':'pending','retry_after':max(1,min(retry,86400))}
    if type(result.get('error_code'))is int and result['error_code']>=500:return {'state':'unknown'}
    if method=='stopPoll' and result.get('error_code')==400 and 'already been closed' in str(result.get('description','')).lower():return {'state':'sent'}
    return {'state':'failed'}


def render(api, row, delivery):
    definition=campaigns.definition(api,row);step=delivery['step'];chat=delivery['recipient']
    if step==-1:
        text=definition['title']+'\n\n'+definition['text']
        if definition['kind']!='message':text+=f"\n\nВопросов: {len(definition['questions'])}. Ответы видит владелец панели. Вопросы придут по одному; свободный ответ отправляйте через «Ответить» на вопрос."
        return 'sendMessage',{'chat_id':chat,'text':text.strip(),'protect_content':True,
            'reply_markup':{'inline_keyboard':[[{'text':'Не получать рассылки','callback_data':'notices:off'}]]}}
    question=definition['questions'][step]
    if question['kind']=='choice':
        title=f"{step+1}/{len(definition['questions'])}. {question['text']}" if definition['kind']=='survey' else question['text']
        return 'sendPoll',{'chat_id':chat,'question':title,'options':[{'text':option} for option in question['options']],
            'is_anonymous':False,'allows_multiple_answers':question['multiple'],'protect_content':True}
    return 'sendMessage',{'chat_id':chat,'text':f"{definition['title']} · Вопрос {step+1}/{len(definition['questions'])}\n\n{question['text']}\n\nОтветьте на это сообщение текстом.",
        'protect_content':True,'reply_markup':{'force_reply':True,'input_field_placeholder':'Ваш ответ'}}


def recover(db):
    # A cross-process lease proves there is no surviving sender for these claims.
    db.execute("UPDATE deliveries SET state='unknown' WHERE state='sending'")
    db.execute("UPDATE deliveries SET close_state='unknown' WHERE close_state='sending'")
    db.execute("UPDATE campaigns SET state='completed' WHERE state='active' AND kind='message' AND NOT EXISTS (SELECT 1 FROM deliveries WHERE campaign=campaigns.id AND state IN ('pending','sending'))")


def _deliver(api):
    settings=control.read(api)
    with campaigns.database(api) as db:
        recover(db)
        now=time.time();bot=campaigns.scope(settings)
        db.execute("UPDATE deliveries SET state='cancelled' WHERE state='pending' AND campaign IN (SELECT id FROM campaigns WHERE bot<>?)",(bot,))
        if not campaigns.ready(settings):return
        clock=db.execute('SELECT due FROM delivery_clock WHERE bot=?',(bot,)).fetchone()
        if clock and clock['due']>now:return
        item=db.execute("""SELECT d.* FROM deliveries d JOIN campaigns c ON c.id=d.campaign
            WHERE c.bot=? AND d.close_state='pending' AND d.due<=? ORDER BY d.due LIMIT 1""",(bot,now)).fetchone()
        closing=item is not None
        if not closing:
            item=db.execute("""SELECT d.* FROM deliveries d JOIN campaigns c ON c.id=d.campaign
                WHERE c.bot=? AND c.state='active' AND d.state='pending' AND d.due<=?
                ORDER BY (d.step>=0) DESC,d.due,c.created,d.recipient LIMIT 1""",(bot,now)).fetchone()
        if item is None:return
        delivery=dict(item);row=campaigns.get_row(db,delivery['campaign'],settings)
        if not closing:
            profile=contacts.profile(api,settings,str(delivery['recipient']))
            contacted=profile.get('snapshot',{}).get('seen_at') or any(item['telegram_id']==delivery['recipient'] for item in contacts.observed(api,settings))
            if not contacted or profile.get('blocked') or profile.get('notices_disabled'):
                db.execute("UPDATE deliveries SET state='skipped' WHERE campaign=? AND recipient=? AND state='pending'",(row['id'],delivery['recipient']))
                return
        column='close_state' if closing else 'state'
        db.execute('UPDATE deliveries SET '+column+"='sending',attempted=? WHERE campaign=? AND recipient=? AND step=?",
                   (now,row['id'],delivery['recipient'],delivery['step']))
        db.execute('INSERT OR REPLACE INTO delivery_clock VALUES(?,?)',(bot,now+.4))
    if closing:
        method,payload='stopPoll',{'chat_id':delivery['recipient'],'message_id':delivery['message_id']}
    else:method,payload=render(api,row,delivery)
    result=telegram(settings['bot_token'],method,payload)
    with campaigns.database(api) as db:
        current=db.execute('SELECT * FROM deliveries WHERE campaign=? AND recipient=? AND step=?',
                           (row['id'],delivery['recipient'],delivery['step'])).fetchone()
        if not current or current[column]!='sending':return
        state=result['state'];due=time.time()+result.get('retry_after',0)
        db.execute('UPDATE deliveries SET '+column+'=?,due=? WHERE campaign=? AND recipient=? AND step=?',
                   (state,due,row['id'],delivery['recipient'],delivery['step']))
        if state=='pending':db.execute('INSERT OR REPLACE INTO delivery_clock VALUES(?,?)',(bot,due))
        if closing:return
        if state=='sent':
            db.execute('UPDATE deliveries SET message_id=?,poll_id=? WHERE campaign=? AND recipient=? AND step=?',
                (result['message_id'],result.get('poll_id'),row['id'],delivery['recipient'],delivery['step']))
            fresh=campaigns.get_row(db,row['id'],settings)
            profile=contacts.profile(api,settings,str(delivery['recipient']))
            opted_out=profile.get('notices_disabled') or profile.get('blocked')
            if (fresh['state']=='closed' or opted_out) and result.get('poll_id'):
                db.execute("UPDATE deliveries SET close_state='pending' WHERE campaign=? AND recipient=? AND step=?",(row['id'],delivery['recipient'],delivery['step']))
            if delivery['step']==-1 and not opted_out:campaigns.enqueue_next(api,db,fresh,delivery['recipient'],0)
            else:
                for early in db.execute('SELECT * FROM early_answers WHERE campaign=? AND recipient=? AND step=? ORDER BY update_id',
                                        (row['id'],delivery['recipient'],delivery['step'])).fetchall():
                    event=campaigns.Event.model_validate(campaigns.unseal(api,row['id'],f"early/{delivery['recipient']}/{early['update_id']}",early['payload']))
                    matches=event.poll_id==result.get('poll_id') if event.kind=='poll' else event.reply_to==result['message_id']
                    if matches and not opted_out:
                        campaigns.record_answer(api,db,fresh,dict(current),event,delivery['recipient'])
                        db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(bot,event.update_id))
                db.execute('DELETE FROM early_answers WHERE campaign=? AND recipient=? AND step=?',(row['id'],delivery['recipient'],delivery['step']))
        definition=campaigns.definition(api,row)
        if definition['kind']=='message' and not db.execute("SELECT 1 FROM deliveries WHERE campaign=? AND state IN ('pending','sending') LIMIT 1",(row['id'],)).fetchone():
            db.execute("UPDATE campaigns SET state='completed',updated=? WHERE id=? AND state='active'",(time.time(),row['id']))


def deliver(api):
    if not (campaigns.folder(api)/'campaigns.sqlite3').exists() or not _guard.acquire(blocking=False):return
    try:
        lease=campaigns.folder(api)/'delivery.lock'
        with lease.open('a+b') as lock:
            os.chmod(lease,0o600)
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            _deliver(api)
    finally:_guard.release()


async def monitor(api):
    while True:
        delay=.4
        try:await asyncio.to_thread(deliver,api)
        except Exception:
            logging.getLogger(__name__).warning('Campaign delivery unavailable')
            delay=10
        await asyncio.sleep(delay)
