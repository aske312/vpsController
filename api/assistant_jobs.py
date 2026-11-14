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
    return {key: value for key, value in record.items() if key not in ('payload', 'fingerprint', 'revision', 'actor', 'worker_checked_at')}


def reconcile(api, record):
    """Called under the storage lock; missing workers are never rescheduled."""
    if record.get('state') in TERMINAL or time.time() - record['created_at'] <= 30: return record
    if time.time() - record.get('worker_checked_at', record['created_at']) < 10: return record
    observed = api.observe_service(record['unit'])
    record['worker_checked_at'] = time.time()
    if observed.get('state') in ('inactive', 'failed') or observed.get('unit_present') is False:
        record.update(state='unknown', stage='interrupted', updated_at=time.time(), seq=record['seq'] + 1,
                      duration_ms=round((time.time() - record['created_at']) * 1000),
                      error={'code': 'worker_interrupted', 'message': 'Рабочий процесс прерван; результат не подтверждён. Автоматического повтора не было.'})
        record.pop('payload', None)
        input_path(api, record['id']).unlink(missing_ok=True)
    operations.atomic_json(path(api, record['id']), record)
    return record


def submit(api, payload):
    actor = str(payload.telegram_id) if payload.telegram_id else 'panel'
    settings = authorize(api, actor)
    identity = payload.request_id
    file = path(api, identity)
    body = payload.model_dump()
    fingerprint = hashlib.sha256(json.dumps([actor, body], sort_keys=True).encode()).hexdigest()
    with operations.locked(api.DATA_DIR):
        record = operations.read(file)
        if record:
            if record['actor'] != actor: raise HTTPException(404, 'Запрос не найден')
            if record['fingerprint'] != fingerprint: raise HTTPException(409, 'ID уже занят другим сообщением')
            return public(record)  # Never submit the same worker twice, even after a lost launch reply.
        files = list(root(api).glob('*.json'))
        active, own, size, retained = 0, 0, 0, 0
        for entry in files:
            old = reconcile(api, operations.read(entry))
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
        if active >= 16 or own >= 3 or size + len(json.dumps(body).encode()) > 96 * 1024 * 1024 or retained >= 256:
            raise HTTPException(429, 'Очередь или хранилище AI заполнены; дождитесь завершения запросов')
        record = {'id': identity, 'actor': actor, 'fingerprint': fingerprint, 'revision': settings['revision'],
                  'state': 'queued', 'stage': 'queued', 'created_at': time.time(), 'updated_at': time.time(),
                  'seq': 0, 'model': payload.model or settings.get('model', ''), 'question': payload.prompt,
                  'stream': payload.stream, 'unit': 'vps-control-chat-' + identity + '.service'}
        # Keep large attachments out of status snapshots and incremental progress writes.
        operations.atomic_json(input_path(api, identity), body)
        operations.atomic_json(file, record)
    try:
        # An independent unit survives API restarts. Credentials only come from the existing environment file.
        result = subprocess.run(['systemd-run', '--quiet', '--collect', '--property=Type=exec',
            '--unit=' + record['unit'], '--property=RuntimeMaxSec=6min', '--property=UMask=0077',
            '--property=EnvironmentFile=' + str(api.ENV_FILE),
            '--setenv=DATA_DIR=' + str(api.DATA_DIR), sys.executable, str(Path(__file__).resolve()), identity],
            capture_output=True, timeout=12, check=False)
        if result.returncode:
            update(api, identity, stage='launch_unconfirmed')
    except (OSError, subprocess.TimeoutExpired):
        pass  # Launch may have succeeded; inspect the same ID, never retry the mutation.
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
            record['duration_ms'] = round((time.time() - record['created_at']) * 1000)
        operations.atomic_json(file, record)
        if record['state'] in TERMINAL: input_path(api, identity).unlink(missing_ok=True)
    if record['state'] in TERMINAL:
        # Metadata only; prompts, answers, keys and exception text never enter observability.
        try:
            import observability
            observability.Store(api.DATA_DIR).append({'at': time.time(), 'request_id': identity,
                'service': 'panel', 'route': '/api/assistant/requests/{identity}', 'method': 'JOB',
                'status': 200 if record['state'] == 'done' else record.get('error', {}).get('http_status', 502),
                'duration_ms': record['duration_ms'], 'reason': 'completed' if record['state'] == 'done' else 'server_error',
                'kind': 'chat_job', 'error_code': record.get('error', {}).get('code'), 'stage': record['stage'],
                'component': 'nodera', 'operation_type': 'chat', 'retryable': False})
        except Exception:
            pass  # Observability must never discard the saved result.


def get(api, identity, actor='panel'):
    authorize(api, actor)
    with operations.locked(api.DATA_DIR):
        record = operations.read(path(api, identity))
        if not record or record['actor'] != actor: raise HTTPException(404, 'Запрос не найден')
        if record['updated_at'] < time.time() - RETENTION: raise HTTPException(410, 'Срок хранения результата истёк')
        record = reconcile(api, record)
    return public(record)


async def events(api, identity, actor, request):
    last, heartbeat, started = -1, 0, time.monotonic()
    while time.monotonic() - started < 300:
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
        record.update(state='running', stage='starting', seq=record['seq'] + 1, updated_at=time.time())
        operations.atomic_json(path(api, identity), record)
    token = current_job.set((api, identity))
    try:
        settings = authorize(api, record['actor'])
        if settings['revision'] != record['revision']:
            raise HTTPException(409, 'Настройки AI изменились после приёма запроса; генерация не запускалась')
        body = operations.read(input_path(api, identity))
        fingerprint = hashlib.sha256(json.dumps([record['actor'], body], sort_keys=True).encode()).hexdigest()
        if fingerprint != record['fingerprint']: raise HTTPException(409, 'Входные данные запроса AI не подтверждены; генерация не запускалась')
        result = control.chat_request(api, control.Chat.model_validate(body), record['actor'])
        update(api, identity, state='done', stage='complete', output=result, partial='')
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else 'Запрос AI не выполнен'
        update(api, identity, state='failed', stage='error', error={'code': getattr(exc, 'error_code', 'chat_error'),
               'http_status': exc.status_code, 'message': detail[:500]})
    except Exception:
        update(api, identity, state='failed', stage='error',
               error={'code': 'internal_error', 'message': 'Внутренняя ошибка выполнения AI; запрос повторно не отправлялся'})
    finally:
        current_job.reset(token)


if __name__ == '__main__':
    import main as api
    execute(api, sys.argv[1])
