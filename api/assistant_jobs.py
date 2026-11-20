"""Private durable chat requests; delivery disconnects never replay generation."""
import asyncio
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from fastapi import HTTPException
import application_operation as operations

current_job = ContextVar('assistant_job', default=None)
TERMINAL = {'done', 'failed', 'unknown'}
RETENTION = 86400


def root(api):
    return api.DATA_DIR / 'assistant' / 'requests'


def path(api, identity):
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
        raise HTTPException(422, 'Некорректный ID запроса')
    return root(api) / (identity + '.json')


def input_path(api, identity):
    return root(api) / 'inputs' / path(api, identity).name


def authorize(api, actor):
    import assistant_control as control
    settings = control.read(api)
    if actor != 'panel': control.roles.require(settings, actor, 'chat')
    return settings


def public(record):
    return {key: value for key, value in record.items() if key not in ('payload', 'fingerprint', 'revision', 'actor', 'worker_checked_at', 'provider', 'launch_requested', 'previous_request_id', 'channel', 'queue_order')}


def order(row):
    return (row.get('queue_order', int(row['created_at'] * 10**9)), row['id'])


def records(api):
    values = [operations.read(file) for file in root(api).glob('*.json')]
    return sorted((row for row in values if row.get('id') and row.get('created_at')), key=order)


def dispatch(api):
    """Reserve FIFO work durably before launching; queued requests need no process."""
    ready = []
    with operations.locked(api.DATA_DIR):
        rows = records(api)
        active = [row for row in rows if row.get('state') not in TERMINAL]
        occupied = [row for row in active if row.get('state') == 'running' or row.get('launch_requested')]
        for row in active:
            if row.get('state') != 'queued' or row.get('launch_requested') or row.get('not_before', 0) > time.time(): continue
            earlier = active[:active.index(row)]
            if any(old['actor'] == row['actor'] or old.get('provider') == row.get('provider') for old in earlier): continue
            if len(occupied) >= 2: break
            if any(old['actor'] == row['actor'] or old.get('provider') == row.get('provider') for old in occupied): continue
            row.update(launch_requested=time.time(), updated_at=time.time(), seq=row['seq'] + 1)
            operations.atomic_json(path(api, row['id']), row)
            occupied.append(row); ready.append(row)
    for row in ready:
        try:
            result = subprocess.run(['systemd-run', '--quiet', '--collect', '--property=Type=exec',
                '--unit=' + row['unit'], '--property=RuntimeMaxSec=35min', '--property=UMask=0077',
                '--property=EnvironmentFile=' + str(api.ENV_FILE), '--setenv=DATA_DIR=' + str(api.DATA_DIR),
                sys.executable, str(Path(__file__).resolve()), row['id']], capture_output=True, timeout=12, check=False)
            if result.returncode: update(api, row['id'], stage='launch_unconfirmed')
        except (OSError, subprocess.TimeoutExpired):
            pass  # Same fixed unit and durable queued state are reconciled before relaunch.


async def monitor(api):
    while True:
        try:
            await asyncio.to_thread(recover, api)
        except (OSError, ValueError, operations.OperationConflict): pass
        await asyncio.sleep(3)


def recover(api):
    with operations.locked(api.DATA_DIR):
        for row in records(api):
            if row.get('state') not in TERMINAL: reconcile(api, row)
    dispatch(api)


def reconcile(api, record):
    """Called under the storage lock; missing workers are never rescheduled."""
    if record.get('state') in TERMINAL or time.time() - record['created_at'] <= 30: return record
    if time.time() - record.get('worker_checked_at', record['created_at']) < 10: return record
    if record['state'] == 'queued' and not record.get('launch_requested'): return record
    observed = api.observe_service(record['unit'])
    record['worker_checked_at'] = time.time()
    if observed.get('state') in ('inactive', 'failed') or observed.get('unit_present') is False:
        if record['state'] == 'queued':
            record.pop('launch_requested', None)
            record.update(stage='queued', updated_at=time.time(), seq=record['seq'] + 1)
            operations.atomic_json(path(api, record['id']), record)
            return record  # Generation has not started; safe to dispatch the same queued identity.
        record.update(state='unknown', stage='interrupted', partial='', updated_at=time.time(), seq=record['seq'] + 1,
                      duration_ms=round((time.time() - record['created_at']) * 1000),
                      error={'code': 'worker_interrupted', 'message': 'Рабочий процесс прерван; результат не подтверждён. Автоматического повтора не было.'})
        record.pop('payload', None)
        input_path(api, record['id']).unlink(missing_ok=True)
    operations.atomic_json(path(api, record['id']), record)
    return record


def submit(api, payload, *, actor=None, channel='panel'):
    actor = actor or (str(payload.telegram_id) if payload.telegram_id else 'panel')
    settings = authorize(api, actor)
    identity = payload.request_id
    file = path(api, identity)
    body = payload.model_dump()
    fingerprint = hashlib.sha256(json.dumps([actor, body], sort_keys=True).encode()).hexdigest()
    with operations.locked(api.DATA_DIR):
        record = operations.read(file)
        if record:
            if record['actor'] != actor or record.get('channel', 'panel') != channel: raise HTTPException(404, 'Запрос не найден')
            if record['fingerprint'] != fingerprint: raise HTTPException(409, 'ID уже занят другим сообщением')
            return public(record)  # Never submit the same worker twice, even after a lost launch reply.
        files = list(root(api).glob('*.json'))
        active, own, size, retained, queue_order = 0, 0, 0, 0, 0
        for entry in files:
            old = reconcile(api, operations.read(entry))
            queue_order = max(queue_order, order(old)[0])
            if old.get('state') in TERMINAL and old.get('updated_at', 0) < time.time() - RETENTION:
                entry.unlink()
                continue
            retained += 1
            size += entry.stat().st_size
            active += old.get('state') not in TERMINAL
            own += old.get('actor') == actor and old.get('state') not in TERMINAL
        for entry in (root(api) / 'inputs').glob('*.json'):
            if entry.stat().st_mtime < time.time() - RETENTION: entry.unlink()
            else: size += entry.stat().st_size
        if payload.previous_request_id:
            previous = operations.read(path(api, payload.previous_request_id))
            if not previous or previous.get('actor') != actor or previous.get('channel', 'panel') != channel:
                raise HTTPException(404, 'Предыдущее сообщение очереди не найдено')
        if active >= 64 or own >= 16 or size + len(json.dumps(body).encode()) > 96 * 1024 * 1024 or retained >= 256:
            raise HTTPException(429, 'Очередь или хранилище AI заполнены; дождитесь завершения запросов')
        record = {'id': identity, 'actor': actor, 'fingerprint': fingerprint, 'revision': settings['revision'],
                  'state': 'queued', 'stage': 'queued', 'created_at': time.time(), 'updated_at': time.time(),
                  'seq': 0, 'model': payload.model or settings.get('model', ''), 'question': payload.prompt,
                  'channel': channel, 'previous_request_id': payload.previous_request_id,
                  'queue_order': max(time.time_ns(), queue_order + 1),
                  'provider': settings.get('provider_url') or 'default',
                  'stream': payload.stream, 'unit': 'vps-control-chat-' + identity + '.service'}
        # Keep large attachments out of status snapshots and incremental progress writes.
        operations.atomic_json(input_path(api, identity), body)
        operations.atomic_json(file, record)
    dispatch(api)
    return get(api, identity, actor)


def update(api, identity, **values):
    with operations.locked(api.DATA_DIR):
        file = path(api, identity)
        record = operations.read(file)
        if not record or record['state'] in TERMINAL: return
        if all(record.get(key) == value for key, value in values.items()): return
        record.update(values, updated_at=time.time(), seq=record['seq'] + 1)
        if record['state'] in TERMINAL:
            record.pop('payload', None)
            record['partial'] = ''
            record['duration_ms'] = round((time.time() - record['created_at']) * 1000)
        operations.atomic_json(file, record)
        if record['state'] in TERMINAL: input_path(api, identity).unlink(missing_ok=True)
    if record['state'] in TERMINAL:
        # Metadata only; prompts, answers, keys and exception text never enter observability.
        try:
            import observability
            incomplete = record['state'] == 'done' and record.get('output', {}).get('result', {}).get('status') in ('incomplete', 'partial')
            observability.Store(api.DATA_DIR).append({'at': time.time(), 'request_id': identity,
                'service': 'panel', 'route': '/api/assistant/requests/{identity}', 'method': 'JOB',
                'status': 502 if incomplete else 200 if record['state'] == 'done' else record.get('error', {}).get('http_status', 502),
                'duration_ms': record['duration_ms'], 'reason': 'completed' if record['state'] == 'done' and not incomplete else 'server_error',
                'kind': 'chat_job', 'error_code': 'chat_incomplete' if incomplete else record.get('error', {}).get('code'), 'stage': record['stage'],
                'component': 'nodera', 'operation_type': 'chat', 'retryable': False})
        except Exception:
            pass  # Observability must never discard the saved result.


def get(api, identity, actor='panel', *, channel=None):
    authorize(api, actor)
    with operations.locked(api.DATA_DIR):
        record = operations.read(path(api, identity))
        if not record or record['actor'] != actor or channel is not None and record.get('channel', 'panel') != channel: raise HTTPException(404, 'Запрос не найден')
        if record['updated_at'] < time.time() - RETENTION: raise HTTPException(410, 'Срок хранения результата истёк')
        before = (record['state'], record.get('launch_requested'))
        record = reconcile(api, record)
        changed = before != (record['state'], record.get('launch_requested'))
    if changed: dispatch(api)
    return public(record)


def cancel(api, identity, actor='panel'):
    authorize(api, actor)
    with operations.locked(api.DATA_DIR):
        row = operations.read(path(api, identity))
        if not row or row['actor'] != actor: raise HTTPException(404, 'Запрос не найден')
        if row['state'] != 'queued': raise HTTPException(409, 'Сообщение уже обрабатывается; отмена очереди недоступна')
        row.update(state='failed', stage='cancelled', updated_at=time.time(), seq=row['seq'] + 1,
                   error={'code': 'cancelled', 'message': 'Сообщение убрано из очереди'})
        operations.atomic_json(path(api, identity), row)
        input_path(api, identity).unlink(missing_ok=True)
    dispatch(api)
    return public(row)


async def events(api, identity, actor, request):
    last, heartbeat, started = -1, 0, time.monotonic()
    while time.monotonic() - started < 2400:
        if await request.is_disconnected(): return
        record = await asyncio.to_thread(get, api, identity, actor)
        if record['seq'] != last:
            yield 'data: ' + json.dumps(record, ensure_ascii=False) + '\n\n'
            last = record['seq']
        if record['state'] in TERMINAL: return
        if time.monotonic() - heartbeat >= 10:
            yield ': heartbeat\n\n'
            heartbeat = time.monotonic()
        await asyncio.sleep(.5)


def emit(event):
    current = current_job.get()
    if not current: return
    api, identity = current
    values = {'stage': event.get('stage', 'provider')}
    if isinstance(event.get('text'), str): values['partial'] = event['text'][:20000]
    update(api, identity, **values)


def execute(api, identity):
    import assistant_control as control
    with operations.locked(api.DATA_DIR):
        record = operations.read(path(api, identity))
        if not record or record['state'] != 'queued': return
        earlier = [row for row in records(api) if order(row) < order(record) and row.get('state') not in TERMINAL]
        if any(row['actor'] == record['actor'] or row.get('provider') == record.get('provider') for row in earlier):
            record.pop('launch_requested', None)
            operations.atomic_json(path(api, identity), record)
            return
        record.update(state='running', stage='starting', seq=record['seq'] + 1, updated_at=time.time())
        operations.atomic_json(path(api, identity), record)
    token = current_job.set((api, identity))
    try:
        authorize(api, record['actor'])
        body = operations.read(input_path(api, identity))
        fingerprint = hashlib.sha256(json.dumps([record['actor'], body], sort_keys=True).encode()).hexdigest()
        if fingerprint != record['fingerprint']: raise HTTPException(409, 'Входные данные запроса AI не подтверждены; генерация не запускалась')
        previous_id = record.get('previous_request_id')
        visited = set()
        while previous_id and previous_id not in visited and len(visited) < 64:
            visited.add(previous_id)
            previous = operations.read(path(api, previous_id))
            if previous.get('actor') == record['actor'] and previous.get('channel', 'panel') == record.get('channel', 'panel'):
                output = previous.get('output', {})
                if not body.get('conversation_id') and output.get('conversation_id'): body['conversation_id'] = output['conversation_id']
                answer = output.get('result', {}).get('answer')
                if answer and not body.get('conversation_id'):
                    verified = not output.get('result', {}).get('status')
                    body['history'] = (body.get('history', []) + [
                        {'role': 'user', 'content': (previous.get('question') or 'Файлы')[:8000]},
                        {'role': 'assistant', 'content': answer[:8000] if verified else 'Предыдущий ответ не завершён; состояние действий нужно проверить.'}])[-40:]
                    while sum(len(row['content']) for row in body['history']) > 48000:
                        body['history'] = body['history'][2:]
                if output: break
                previous_id = previous.get('previous_request_id')
            else: break
        result = control.chat_request(api, control.Chat.model_validate(body), record['actor'], record.get('channel', 'panel'))
        update(api, identity, state='done', stage='complete', output=result, partial='')
    except HTTPException as exc:
        if getattr(exc, 'error_code', '') == 'provider_queue_wait':
            update(api, identity, state='queued', stage='waiting_provider', launch_requested=None, not_before=time.time() + 5)
            return  # Explicit evidence that no generation started; keep accepted work queued.
        detail = exc.detail if isinstance(exc.detail, str) else 'Запрос AI не выполнен'
        update(api, identity, state='failed', stage='error', error={'code': getattr(exc, 'error_code', 'chat_error'),
               'http_status': exc.status_code, 'message': detail[:500]})
    except Exception:
        update(api, identity, state='failed', stage='error',
               error={'code': 'internal_error', 'message': 'Внутренняя ошибка выполнения AI; запрос повторно не отправлялся'})
    finally:
        current_job.reset(token)
        dispatch(api)


if __name__ == '__main__':
    import main as api
    execute(api, sys.argv[1])
