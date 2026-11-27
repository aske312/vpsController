"""Owner-authored Telegram campaigns, frozen audiences and private survey answers."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

import assistant_contacts as contacts
import assistant_control as control
import assistant_roles as roles
import assistant_privacy as privacy

MIN_BOT_VERSION = '1.25.9'
MAX_CAMPAIGNS = 200
MAX_RECIPIENTS = 10000


class Question(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['choice', 'text'] = 'choice'
    text: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(default_factory=list, max_length=10)
    multiple: StrictBool = False

    @model_validator(mode='after')
    def valid(self):
        self.text = self.text.strip()
        self.options = [option.strip() for option in self.options]
        if not self.text: raise ValueError('Напишите вопрос')
        if self.kind == 'choice':
            if not 2 <= len(self.options) <= 10 or any(not option or len(option.encode('utf-16-le'))//2 > 100 for option in self.options):
                raise ValueError('Добавьте от 2 до 10 вариантов, до 100 символов каждый')
            if len(set(self.options)) != len(self.options): raise ValueError('Варианты не должны повторяться')
        elif self.options or self.multiple: raise ValueError('Свободный ответ не принимает варианты')
        if len(self.text.encode('utf-16-le'))//2>300:raise ValueError('Вопрос слишком длинный для Telegram')
        return self


class Audience(BaseModel):
    model_config = {'extra': 'forbid'}
    mode: Literal['all', 'subscribers', 'selected'] = 'all'
    ids: list[StrictInt] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    @model_validator(mode='after')
    def valid(self):
        if any(type(value) is not int or not 0 < value <= 2**52 for value in self.ids): raise ValueError('Неверный Telegram ID')
        self.ids = sorted(set(self.ids))
        if self.mode == 'selected' and not self.ids: raise ValueError('Выберите получателей')
        if self.mode != 'selected' and self.ids: raise ValueError('Список получателей относится к выбранной аудитории')
        return self


class Draft(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(default=0, strict=True, ge=0)
    kind: Literal['message', 'poll', 'survey'] = 'message'
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(default='', max_length=3000)
    audience: Audience = Field(default_factory=Audience)
    questions: list[Question] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def valid(self):
        self.title, self.text = self.title.strip(), self.text.strip()
        if not self.title: raise ValueError('Добавьте название')
        if self.kind == 'message' and (not self.text or self.questions): raise ValueError('Уведомлению нужен текст без вопросов')
        if self.kind == 'poll' and (len(self.questions) != 1 or self.questions[0].kind != 'choice'):
            raise ValueError('Голосование содержит один вопрос с вариантами')
        if self.kind == 'survey' and not self.questions: raise ValueError('Добавьте вопросы анкеты')
        if self.kind=='survey' and any(q.kind=='choice' and len(f'{i+1}/{len(self.questions)}. {q.text}'.encode('utf-16-le'))//2>300 for i,q in enumerate(self.questions)):
            raise ValueError('Вопрос анкеты с номером должен помещаться в 300 символов Telegram')
        if len((self.title+'\n\n'+self.text).encode('utf-16-le'))//2>3600:raise ValueError('Сообщение слишком длинное для Telegram')
        return self


class Revision(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: int = Field(strict=True, ge=1)


class Launch(Revision):
    confirmation: str = Field(pattern=r'^[a-f0-9]{32}$')


class Event(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['poll', 'reply', 'preference']
    update_id: int = Field(strict=True, ge=0)
    poll_id: str = Field(default='', max_length=128)
    option_ids: list[StrictInt] = Field(default_factory=list, max_length=10)
    reply_to: int = Field(default=0, strict=True, ge=0)
    text: str = Field(default='', max_length=4096)
    enabled: StrictBool | None = None
    message_id: int = Field(default=0, strict=True, ge=0)
    edited: StrictBool = False

    @model_validator(mode='after')
    def valid(self):
        if self.kind=='poll' and not self.poll_id:raise ValueError('Нужен ID голосования')
        if self.kind=='reply' and not self.reply_to:raise ValueError('Нужен ID вопроса')
        return self


def folder(api): return control.directory(api) / 'campaigns'


@contextmanager
def database(api):
    root = folder(api)
    if root.is_symlink(): raise HTTPException(503, 'Хранилище рассылок недоступно')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = root / 'campaigns.sqlite3'
    if file.is_symlink(): raise HTTPException(503, 'Хранилище рассылок недоступно')
    db = sqlite3.connect(file, timeout=5, isolation_level=None)
    file.chmod(0o600)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY, bot TEXT NOT NULL, definition TEXT NOT NULL,
            kind TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            confirmation TEXT, preview_digest TEXT, preview_expires REAL, audience TEXT NOT NULL DEFAULT '[]');
          CREATE TABLE IF NOT EXISTS deliveries (
            campaign TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            recipient INTEGER NOT NULL, step INTEGER NOT NULL, state TEXT NOT NULL, due REAL NOT NULL,
            attempted REAL, message_id INTEGER, poll_id TEXT, close_state TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(campaign,recipient,step));
          CREATE UNIQUE INDEX IF NOT EXISTS campaign_poll ON deliveries(poll_id) WHERE poll_id IS NOT NULL;
          CREATE INDEX IF NOT EXISTS campaign_message ON deliveries(recipient,message_id);
          CREATE TABLE IF NOT EXISTS answers (
            campaign TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE, recipient INTEGER NOT NULL,
            step INTEGER NOT NULL, answer TEXT NOT NULL, update_id INTEGER NOT NULL, at REAL NOT NULL, message_id INTEGER NOT NULL DEFAULT 0, answered INTEGER NOT NULL,
            PRIMARY KEY(campaign,recipient,step));
          CREATE TABLE IF NOT EXISTS tallies (
            campaign TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            step INTEGER NOT NULL, option_id INTEGER NOT NULL, n INTEGER NOT NULL,
            PRIMARY KEY(campaign,step,option_id));
          CREATE TABLE IF NOT EXISTS receipts (bot TEXT NOT NULL, update_id INTEGER NOT NULL, PRIMARY KEY(bot,update_id));
          CREATE TABLE IF NOT EXISTS early_answers (
            bot TEXT NOT NULL, update_id INTEGER NOT NULL, recipient INTEGER NOT NULL,
            campaign TEXT NOT NULL, step INTEGER NOT NULL, payload TEXT NOT NULL, at REAL NOT NULL,
            PRIMARY KEY(bot,update_id));
          CREATE TABLE IF NOT EXISTS delivery_clock (bot TEXT PRIMARY KEY, due REAL NOT NULL);
        ''')
        db.execute('PRAGMA max_page_count='+str(256*1024*1024//db.execute('PRAGMA page_size').fetchone()[0]))
        if not (control.directory(api)/'.privacy-key').exists() and db.execute('SELECT 1 FROM campaigns LIMIT 1').fetchone():
            raise HTTPException(503,'Ключ рассылок отсутствует; восстановите защищённую резервную копию')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally: db.close()


def seal(api, identity, section, value):
    return json.dumps(privacy.seal_record(control.directory(api),f'campaigns/{identity}/{section}'.encode(),{'value':value}))


def unseal(api, identity, section, value):
    encoded=json.loads(value)
    if 'private_storage' not in encoded:raise HTTPException(503,'Повреждено защищённое содержимое рассылки')
    return privacy.unseal_record(control.directory(api),f'campaigns/{identity}/{section}'.encode(),encoded)['value']


def definition(api,row): return unseal(api,row['id'],'definition',row['definition'])


def scope(settings):
    identity = str(settings.get('bot_token') or '').split(':',1)[0]
    return hashlib.sha256(identity.encode()).hexdigest()[:24] if identity else ''


def ready(settings):
    return bool(settings.get('bot_token') and settings.get('modules',{}).get('nodera-bot')
                and control.runtime_ready('nodera-bot')
                and control.version_at_least(control.runtime_directory('nodera-bot')/'version',MIN_BOT_VERSION))


def require_ready(settings):
    if not ready(settings): raise HTTPException(409, 'Включите NoderaBot и обновите его до '+MIN_BOT_VERSION+' или новее')


def recipients(api, settings):
    accounts = {str(row['telegram_id']):row for row in settings.get('accounts',[])}
    saved = contacts.profiles(api,settings)
    observed = {str(row['telegram_id']):row for row in contacts.observed(api,settings)}
    result = []
    for actor in sorted(set(accounts)|set(saved)|set(observed)):
        account, profile = accounts.get(actor,{}), saved.get(actor,{})
        snapshot = observed.get(actor) or profile.get('snapshot',{})
        if not re.fullmatch(r'[1-9][0-9]{0,15}',actor): continue
        contacted = isinstance(snapshot.get('seen_at'),(int,float)) and snapshot['seen_at'] > 0
        reason = 'blocked' if profile.get('blocked') else 'muted' if profile.get('notices_disabled') else '' if contacted else 'not_started'
        result.append({'telegram_id':int(actor), 'name':str(profile.get('display_name') or account.get('name') or snapshot.get('name') or actor)[:120],
            'role':roles.role(account) if account else 'guest','subscriber':bool(account.get('mihomo_profile_id')),
            'eligible':not reason,'excluded_reason':reason})
    if len(result)>MAX_RECIPIENTS: raise HTTPException(409,'Слишком большая аудитория рассылки')
    return result


def audience_rows(api, settings, definition):
    audience = definition['audience']; rows = recipients(api,settings)
    selected_ids=set(audience['ids'])
    selected = [row for row in rows if audience['mode']=='all' or audience['mode']=='subscribers' and row['subscriber']
                or audience['mode']=='selected' and row['telegram_id'] in selected_ids]
    return [row for row in selected if row['eligible']], [row for row in selected if not row['eligible']]


def digest(rows): return hashlib.sha256(json.dumps(sorted(row['telegram_id'] for row in rows)).encode()).hexdigest()


def get_row(db, identity, settings):
    if scope(settings):db.execute("UPDATE campaigns SET bot=? WHERE bot='' AND state='draft'",(scope(settings),))
    row=db.execute('SELECT * FROM campaigns WHERE id=? AND bot=?',(identity,scope(settings))).fetchone()
    if row is None: raise HTTPException(404,'Рассылка не найдена')
    return dict(row)


def public(api, db, row, *, details=False, offset=0):
    value={**definition(api,row), 'revision':row['revision'], 'state':row['state'],
           'created_at':row['created'],'updated_at':row['updated'],'recipient_count':len(json.loads(row['audience']))}
    counts=db.execute('SELECT state,COUNT(*) n FROM deliveries WHERE campaign=? AND step=-1 GROUP BY state',(row['id'],)).fetchall()
    value['delivery_counts']={item['state']:item['n'] for item in counts}
    value['pending_steps']=db.execute("SELECT COUNT(*) FROM deliveries WHERE campaign=? AND state IN ('pending','sending')",(row['id'],)).fetchone()[0]
    value['unconfirmed_closes']=db.execute("SELECT COUNT(*) FROM deliveries WHERE campaign=? AND close_state IN ('pending','sending','failed','unknown')",(row['id'],)).fetchone()[0]
    valid='campaign=? AND answered=1'
    value['respondents']=db.execute('SELECT COUNT(DISTINCT recipient) FROM answers WHERE '+valid,(row['id'],)).fetchone()[0]
    value['completed_responses']=db.execute('SELECT COUNT(*) FROM (SELECT recipient FROM answers WHERE '+valid+' GROUP BY recipient HAVING COUNT(*)=?)',
        (row['id'],len(value['questions']))).fetchone()[0] if value['questions'] else 0
    value['answer_total']=db.execute('SELECT COUNT(*) FROM answers WHERE '+valid,(row['id'],)).fetchone()[0]
    value['step_counts']={item['state']:item['n'] for item in db.execute('SELECT state,COUNT(*) n FROM deliveries WHERE campaign=? AND step>=0 GROUP BY state',(row['id'],))}
    value['results']=[]
    if details:
        answered={item['step']:item['n'] for item in db.execute('SELECT step,COUNT(*) n FROM answers WHERE '+valid+' GROUP BY step',(row['id'],))}
        choices={(item['step'],item['option_id']):item['n'] for item in db.execute('SELECT * FROM tallies WHERE campaign=?',(row['id'],))}
        value['results']=[{'answered':answered.get(index,0),'counts':[choices.get((index,option),0) for option in range(len(question['options']))]} for index,question in enumerate(value['questions'])]
        value['answers']=[{'telegram_id':item['recipient'],'step':item['step'],'answer':unseal(api,row['id'],f"answer/{item['recipient']}/{item['step']}",item['answer']),'at':item['at']}
            for item in db.execute('SELECT * FROM answers WHERE '+valid+' ORDER BY recipient,step LIMIT 50 OFFSET ?',(row['id'],offset))]
        value['answers_offset']=offset
        value['deliveries']=[dict(item) for item in db.execute("SELECT recipient,step,state,close_state FROM deliveries WHERE campaign=? AND (state IN ('failed','unknown','skipped') OR close_state IN ('failed','unknown')) ORDER BY recipient,step LIMIT 100",(row['id'],))]
    return value


def overview(api):
    settings=control.read(api)
    with database(api) as db:
        if scope(settings):db.execute("UPDATE campaigns SET bot=? WHERE bot='' AND state='draft'",(scope(settings),))
        rows=db.execute('SELECT * FROM campaigns WHERE bot=? ORDER BY created DESC LIMIT 200',(scope(settings),)).fetchall()
        return {'ready':ready(settings),'minimum_bot_version':MIN_BOT_VERSION,'recipients':recipients(api,settings),
                'items':[public(api,db,dict(row)) for row in rows]}


def detail(api, identity, offset=0):
    settings=control.read(api)
    with database(api) as db:return public(api,db,get_row(db,identity,settings),details=True,offset=offset)


def save(api, draft):
    settings=control.read(api); config=draft.model_dump(exclude={'revision'})
    with database(api) as db:
        existing=db.execute('SELECT * FROM campaigns WHERE id=?',(draft.id,)).fetchone()
        if existing:
            row=get_row(db,draft.id,settings)
            if row['state']!='draft': raise HTTPException(409,'Запущенную рассылку нельзя редактировать')
            if row['revision']!=draft.revision:
                if definition(api,row)==config:return public(api,db,row,details=True)
                raise HTTPException(409,'Черновик изменился; обновите страницу')
            db.execute('UPDATE campaigns SET definition=?,revision=revision+1,updated=?,confirmation=NULL WHERE id=?',
                       (seal(api,draft.id,'definition',config),time.time(),draft.id))
        else:
            if draft.revision:raise HTTPException(409,'Черновик больше не существует')
            if db.execute('SELECT COUNT(*) FROM campaigns WHERE bot=?',(scope(settings),)).fetchone()[0]>=MAX_CAMPAIGNS:raise HTTPException(409,'Удалите старые черновики или завершённые рассылки')
            db.execute('INSERT INTO campaigns(id,bot,definition,kind,state,revision,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                (draft.id,scope(settings),seal(api,draft.id,'definition',config),draft.kind,'draft',1,time.time(),time.time()))
        db.execute('UPDATE campaigns SET kind=? WHERE id=?',(draft.kind,draft.id))
        return public(api,db,get_row(db,draft.id,settings),details=True)


def preview(api, identity, payload):
    settings=control.read(api);require_ready(settings)
    with database(api) as db:
        row=get_row(db,identity,settings)
        if row['state']!='draft' or row['revision']!=payload.revision:raise HTTPException(409,'Обновите черновик перед просмотром')
        selected,excluded=audience_rows(api,settings,definition(api,row))
        if not selected:raise HTTPException(409,'Нет доступных получателей: пользователь должен запустить бота и разрешить рассылки')
        token=uuid.uuid4().hex; expires=time.time()+600
        db.execute('UPDATE campaigns SET confirmation=?,preview_digest=?,preview_expires=? WHERE id=?',
                   (token,digest(selected),expires,identity))
        return {'campaign':public(api,db,row),'confirmation':token,'expires_at':expires,'recipients':selected,'excluded':excluded}


def launch(api, identity, payload):
    settings=control.read(api);require_ready(settings)
    with database(api) as db:
        row=get_row(db,identity,settings)
        if row['state']!='draft':
            if row['confirmation']==payload.confirmation:return public(api,db,row,details=True)
            raise HTTPException(409,'Рассылка уже запущена или завершена')
        selected,_=audience_rows(api,settings,definition(api,row))
        if (row['revision']!=payload.revision or row['confirmation']!=payload.confirmation
                or not row['preview_expires'] or row['preview_expires']<time.time() or digest(selected)!=row['preview_digest']):
            raise HTTPException(409,'Черновик или аудитория изменились; откройте предварительный просмотр снова')
        now=time.time()
        db.execute("UPDATE campaigns SET state='active',audience=?,updated=? WHERE id=?",(json.dumps([item['telegram_id'] for item in selected]),now,identity))
        db.executemany('INSERT INTO deliveries(campaign,recipient,step,state,due) VALUES(?,?,?,\'pending\',?)',
            [(identity,item['telegram_id'],-1,now) for item in selected])
        return public(api,db,get_row(db,identity,settings),details=True)


def close(api, identity):
    settings=control.read(api)
    with database(api) as db:
        row=get_row(db,identity,settings)
        if row['state']=='draft':
            db.execute('DELETE FROM campaigns WHERE id=?',(identity,));return {'deleted':True}
        db.execute("UPDATE campaigns SET state='closed',updated=? WHERE id=?",(time.time(),identity))
        db.execute("UPDATE deliveries SET state='cancelled' WHERE campaign=? AND state='pending'",(identity,))
        db.execute("UPDATE deliveries SET close_state='pending' WHERE campaign=? AND poll_id IS NOT NULL AND close_state=''",(identity,))
        return public(api,db,get_row(db,identity,settings),details=True)


def remove(api, identity):
    settings=control.read(api)
    with database(api) as db:
        row=get_row(db,identity,settings)
        if row['state']=='active':raise HTTPException(409,'Сначала завершите рассылку')
        if db.execute("SELECT 1 FROM deliveries WHERE campaign=? AND (state='sending' OR close_state IN ('pending','sending'))",(identity,)).fetchone():
            raise HTTPException(409,'Дождитесь завершения отправки и закрытия голосований')
        db.execute('DELETE FROM early_answers WHERE campaign=?',(identity,))
        db.execute('DELETE FROM campaigns WHERE id=?',(identity,))
        return {'deleted':True}


def enqueue_next(api, db, row, recipient, step):
    config=definition(api,row)
    if row['state']!='active' or step>=len(config['questions']):return
    last=db.execute('SELECT MAX(attempted) FROM deliveries WHERE recipient=?',(recipient,)).fetchone()[0] or 0
    db.execute("INSERT OR IGNORE INTO deliveries(campaign,recipient,step,state,due) VALUES(?,?,?,'pending',?)",
               (row['id'],recipient,step,max(time.time(),last+1.1)))


def record_answer(api, db, row, delivery, event, actor):
    config=definition(api,row);step=delivery['step']
    if row['state']!='active':return {'handled':True,'text':'Этот опрос уже завершён.'}
    question=config['questions'][step]
    if event.kind=='poll':
        answer=sorted(set(event.option_ids))
        if any(type(option)is not int or not 0<=option<len(question['options']) for option in answer) or not question['multiple'] and len(answer)>1:
            raise HTTPException(422,'Неверный вариант ответа')
    else:
        answer=event.text.strip()
        if not answer or len(answer.encode('utf-16-le'))//2>4000:return {'handled':True,'text':'Напишите ответ текстом, до 4000 символов.'}
    previous=db.execute('SELECT * FROM answers WHERE campaign=? AND recipient=? AND step=?',(row['id'],actor,step)).fetchone()
    if previous and (previous['update_id']>=event.update_id or event.kind=='reply' and not (event.edited and event.message_id and previous['message_id']==event.message_id)):
        return {'handled':True,'text':''}
    if event.edited and not previous:return {'handled':True,'text':''}
    section=f'answer/{actor}/{step}'
    if event.kind=='poll':
        old=unseal(api,row['id'],section,previous['answer']) if previous else []
        for option in set(old)|set(answer):
            change=int(option in answer)-int(option in old)
            db.execute('INSERT INTO tallies VALUES(?,?,?,?) ON CONFLICT(campaign,step,option_id) DO UPDATE SET n=MAX(0,n+excluded.n)',(row['id'],step,option,change))
    db.execute('INSERT OR REPLACE INTO answers VALUES(?,?,?,?,?,?,?,?)',(row['id'],actor,step,seal(api,row['id'],section,answer),event.update_id,time.time(),event.message_id,int(bool(answer))))
    if answer:enqueue_next(api,db,row,actor,step+1)
    acknowledgement=event.kind=='reply' or config['kind']=='survey' and step+1==len(config['questions']) and not previous and bool(answer)
    return {'handled':True,'text':('Спасибо! Ответ сохранён.' if step+1==len(config['questions']) else 'Ответ сохранён. Следующий вопрос придёт отдельным сообщением.') if acknowledgement else ''}


def receive(api, settings, actor, event):
    if not re.fullmatch(r'[1-9][0-9]{0,15}',str(actor)):raise HTTPException(403,'Неверный участник')
    actor=int(actor)
    if event.kind=='preference':
        with control.operations.locked(api.DATA_DIR):
            profile=contacts.profile(api,settings,str(actor))
            if not profile.get('snapshot',{}).get('seen_at'):raise HTTPException(409,'Сначала откройте /start')
            if event.enabled is None:
                return {'handled':True,'text':('Рассылки отключены.' if profile.get('notices_disabled') else 'Рассылки включены.')+'\n/notifications on — включить\n/notifications off — отключить'}
            if event.update_id>profile.get('notices_update_id',-1):
                profile['notices_disabled']=not event.enabled
                profile['notices_update_id']=event.update_id
                contacts.save_profile(api,settings,str(actor),profile)
        enabled=not profile.get('notices_disabled')
        if not enabled:
            with database(api) as db:
                db.execute("UPDATE deliveries SET state='cancelled' WHERE recipient=? AND state='pending' AND campaign IN (SELECT id FROM campaigns WHERE bot=?)",(actor,scope(settings)))
                db.execute("UPDATE deliveries SET close_state='pending' WHERE recipient=? AND poll_id IS NOT NULL AND close_state='' AND campaign IN (SELECT id FROM campaigns WHERE bot=?)",(actor,scope(settings)))
        return {'handled':True,'text':'Рассылки включены.' if enabled else 'Рассылки отключены. Включить снова: /notifications on.'}
    with database(api) as db:
        if db.execute('SELECT 1 FROM receipts WHERE bot=? AND update_id=?',(scope(settings),event.update_id)).fetchone():return {'handled':True,'text':''}
        if event.kind=='poll':
            item=db.execute('SELECT d.* FROM deliveries d JOIN campaigns c ON c.id=d.campaign WHERE c.bot=? AND d.recipient=? AND d.poll_id=? AND d.step>=0',
                            (scope(settings),actor,event.poll_id)).fetchone()
        else:
            item=db.execute('SELECT d.* FROM deliveries d JOIN campaigns c ON c.id=d.campaign WHERE c.bot=? AND d.recipient=? AND d.message_id=? AND d.step>=0',
                            (scope(settings),actor,event.reply_to)).fetchone()
        if item is None:
            # Handle a vote/reply that races the send acknowledgement. The
            # returned Telegram poll/message ID must match before it is applied.
            pending=db.execute("SELECT d.*,c.definition FROM deliveries d JOIN campaigns c ON c.id=d.campaign WHERE c.bot=? AND d.recipient=? AND d.state='sending' AND d.step>=0",(scope(settings),actor)).fetchone()
            if pending:
                question=unseal(api,pending['campaign'],'definition',pending['definition'])['questions'][pending['step']]
                if (event.kind=='poll')==(question['kind']=='choice'):
                    db.execute('DELETE FROM early_answers WHERE at<?',(time.time()-86400,))
                    if db.execute('SELECT COUNT(*) FROM early_answers').fetchone()[0]<1000:
                        db.execute('INSERT OR IGNORE INTO early_answers VALUES(?,?,?,?,?,?,?)',
                            (scope(settings),event.update_id,actor,pending['campaign'],pending['step'],seal(api,pending['campaign'],f'early/{actor}/{event.update_id}',event.model_dump()),time.time()))
                        return {'handled':True,'text':''}
            return {'handled':event.kind=='poll','text':''}
        delivery=dict(item);row=get_row(db,delivery['campaign'],settings)
        question=definition(api,row)['questions'][delivery['step']]
        if (event.kind=='poll')!=(question['kind']=='choice'):return {'handled':True,'text':'Выберите вариант в голосовании над этим сообщением.'}
        if contacts.profile(api,settings,str(actor)).get('notices_disabled'):return {'handled':True,'text':'Рассылки отключены. Включить снова: /notifications on.'}
        result=record_answer(api,db,row,delivery,event,actor)
        db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(scope(settings),event.update_id))
        if event.update_id%100==0:
            db.execute('DELETE FROM receipts WHERE rowid NOT IN (SELECT rowid FROM receipts ORDER BY rowid DESC LIMIT 100000)')
        return result
