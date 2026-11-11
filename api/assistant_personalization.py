"""Private per-interlocutor preferences and bounded context across conversations."""
import hashlib
import hmac
import math
import re
import time
import uuid
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, Field

import assistant_control as control
import assistant_memory as memory
import assistant_roles as roles


class MemoryAction(BaseModel):
    model_config = {'extra': 'forbid'}
    operation: str = Field(pattern=r'^(read|remember|forget)$')
    text: str = Field(default='', max_length=300)
    permanent: bool = Field(default=False, strict=True)
    identity: str = Field(default='all', pattern=r'^(all|[a-f0-9]{16})$')


def path(api, actor):
    memory.folder(api, actor)  # Validate the actor with the same rules as chat history.
    return control.directory(api) / 'personalization' / (str(actor) + '.json')


def read_record(api, actor):
    file = path(api, actor)
    if file.exists() and file.stat().st_size > 262144:
        raise HTTPException(503, 'Персональный контекст превышает лимит')
    record = control.operations.read(file)
    if not isinstance(record.get('facts', []), list) or any(not isinstance(row, dict) or not isinstance(row.get('text'), str) or len(row['text']) > 300 or type(row.get('at')) not in (int, float) or not math.isfinite(row['at']) or not re.fullmatch(r'[a-f0-9]{16}', str(row.get('id', ''))) for row in record.get('facts', [])):
        raise HTTPException(503, 'Персональный контекст повреждён; данные сохранены для восстановления')
    if record.get('reset_at') is not None and (type(record['reset_at']) not in (int, float) or not math.isfinite(record['reset_at'])):
        raise HTTPException(503, 'Персональный контекст повреждён; данные сохранены для восстановления')
    return record


def expire(api, settings):
    cutoff = time.time() - settings.get('memory_days', 30) * 86400
    for file in (control.directory(api) / 'personalization').glob('*.json'):
        try:
            record = read_record(api, file.stem)
            facts = [row for row in record.get('facts', []) if row.get('permanent', False) or row['at'] >= cutoff]
            if facts != record.get('facts', []): control.operations.atomic_json(file, {**record, 'facts': facts})
        except (OSError, HTTPException, control.operations.OperationConflict):
            continue  # Damaged records are preserved, never silently reset.


def usage(api):
    root = control.directory(api)
    files = list((root / 'personalization').glob('*.json')) + list((root / 'conversations').glob('*/*.json'))
    return {'used_bytes': sum(file.stat().st_size for file in files if file.exists()), 'limit_bytes': memory.MAX_BYTES}


def snapshot(api, settings, actor, exclude=None):
    with control.operations.locked(api.DATA_DIR):
        record = read_record(api, actor)
        cutoff = time.time() - settings.get('memory_days', 30) * 86400
        facts = [row for row in record.get('facts', []) if row.get('permanent', False) or row.get('at', 0) >= cutoff]
        if facts != record.get('facts', []):
            control.operations.atomic_json(path(api, actor), {**record, 'facts': facts})
        topics = []
        if settings.get('memory_enabled', True):
            files = [file for channel in ('panel', 'bot') for file in memory.folder(api, actor, channel).glob('*.json')
                     if file.stem != exclude and file.stat().st_mtime >= cutoff]
            for file in sorted(files, key=lambda entry: entry.stat().st_mtime, reverse=True)[:3]:
                for turn in memory._read(file).get('turns', [])[-3:]:
                    if record.get('reset_at'):
                        try: at = datetime.fromisoformat(turn.get('at', '').replace('Z', '+00:00')).timestamp()
                        except ValueError: continue
                        if at < record['reset_at']: continue
                    question = turn['question'].strip()[:500]
                    if question and question not in topics: topics.append(question)
        return {'enabled': settings.get('personalization_enabled', True), 'actor': actor, 'facts': facts[-200:], 'recent_topics': topics[:6], 'cache': usage(api), 'retention_days': settings.get('memory_days', 30)}


def for_model(value, prompt=''):
    words = set(re.findall(r'[\w-]{4,}', prompt.lower()))
    facts = sorted(value.get('facts', []), key=lambda row: (sum(word in row['text'].lower() for word in words), row.get('at', 0)), reverse=True)
    return {**value, 'facts': facts[:20], 'total_facts': len(facts)}


def update(api, settings, actor, action):
    if action.operation == 'read':
        return snapshot(api, settings, actor)
    with control.operations.locked(api.DATA_DIR):
        record = read_record(api, actor)
        facts = record.get('facts', [])
        if action.operation == 'remember':
            text = ' '.join(action.text.split())
            if not text:
                raise HTTPException(422, 'Укажите предпочтение или факт для сохранения')
            if re.search(r'(?i)парол|токен|api[ _-]?key|private[ _-]?key|password|secret', text):
                raise HTTPException(422, 'Пароли и ключи не сохраняются в персональном контексте')
            facts = [{**row, 'at': time.time(), 'permanent': row.get('permanent', False) or action.permanent} if row['text'] == text else row for row in facts]
            if not any(row['text'] == text for row in facts):
                facts = (facts + [{'id': uuid.uuid4().hex[:16], 'text': text, 'at': time.time(), 'permanent': action.permanent}])[-200:]
        elif action.operation == 'forget':
            if action.identity == 'all':
                facts = []
                record['reset_at'] = time.time()
            else:
                facts = [row for row in facts if row['id'] != action.identity]
        before = dict(record)
        control.operations.atomic_json(path(api, actor), {**record, 'facts': facts})
        memory._prune(api, settings)
        if usage(api)['used_bytes'] > memory.MAX_BYTES:
            control.operations.atomic_json(path(api, actor), before)
            raise HTTPException(507, 'Долгосрочная память заполнила лимит 5 ГБ. Удалите ненужные факты.')
    return snapshot(api, settings, actor)


def permit(settings, actor, operation):
    expires = int(time.time()) + 180
    value = f'{actor}:{operation}:{settings["revision"]}:{expires}'
    signature = hmac.new(settings['control_token'].encode(), value.encode(), hashlib.sha256).hexdigest()
    return f'{expires}:{signature}'


def authorize(api, authorization, actor, action, proof):
    settings = control.read(api)
    if not authorization or not hmac.compare_digest(authorization, 'Bearer ' + settings.get('control_token', '')):
        raise HTTPException(401, 'Нужен служебный токен ассистента')
    user = next((row for row in settings['accounts'] if str(row['telegram_id']) == actor), {})
    if not roles.allows(settings, actor, 'memory'):
        raise HTTPException(403, 'Контекст собеседника недоступен')
    if not settings.get('personalization_enabled', True):
        raise HTTPException(403, 'Персонализация выключена')
    control.require_installed(api, 'ai-agent')
    if action.operation != 'read':
        parts = (proof or '').split(':')
        if len(parts) != 2 or not parts[0].isdigit() or not time.time() <= int(parts[0]) <= time.time() + 181:
            raise HTTPException(403, 'Запрос на изменение памяти не подтверждён собеседником')
        value = f'{actor}:{action.operation}:{settings["revision"]}:{parts[0]}'
        signature = hmac.new(settings['control_token'].encode(), value.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, parts[1]):
            raise HTTPException(403, 'Нельзя изменять контекст другого собеседника')
    return settings
