"""Authenticated, cancellable SSE journal with a bounded number of processes."""
import asyncio
import json
import re
import threading
import time
import uuid

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

import observability_control

_lock = threading.Lock()
_users = {}
_leases = {}


def reserve(user):
    with _lock:
        if sum(_users.values()) >= 4 or _users.get(user, 0) >= 2:
            raise HTTPException(429, 'Лимит потоков журнала: два на пользователя, четыре всего')
        _users[user] = _users.get(user, 0) + 1
        lease = uuid.uuid4().hex
        _leases[lease] = user
        return lease


def release(lease):
    with _lock:
        user = _leases.pop(lease, None)
        if user is None: return
        _users[user] = max(0, _users.get(user, 0) - 1)
        if not _users[user]: _users.pop(user, None)


class Response(StreamingResponse):
    def __init__(self, content, lease):
        self.lease = lease
        super().__init__(content, media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    async def __call__(self, scope, receive, send):
        try: await super().__call__(scope, receive, send)
        finally: release(self.lease)


def encode(value): return 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'


async def events(api, request, user, query):
    process = None
    try:
        args, _ = observability_control.journal_arguments(api, **query.model_dump(), follow=True)
        if query.cursor:
            # sd-journal may seek to a nearby entry after vacuum/rotation.
            # Verify the exact cursor before following, rather than silently skipping.
            process = await asyncio.create_subprocess_exec('journalctl', '--cursor=' + query.cursor, '-n', '1', '--no-pager', '-o', 'json',
                '--output-fields=__CURSOR', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=65536)
            try:
                line = await asyncio.wait_for(process.stdout.readline(), 6)
                valid = json.loads(line).get('__CURSOR') == query.cursor
            except (ValueError, asyncio.TimeoutError): valid = False
            if process.returncode is None:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
            process = None
            if not valid:
                yield encode({'type': 'gap', 'message': 'Курсор недоступен после ротации или очистки. Начните новый просмотр.'})
                return
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=65536)
        deadline = time.monotonic() + 1800
        import assistant_diagnostics
        while time.monotonic() < deadline:
            if await request.is_disconnected(): break
            try: line = await asyncio.wait_for(process.stdout.readline(), 10)
            except asyncio.TimeoutError:
                yield encode({'type': 'heartbeat'})
                continue
            if not line:
                yield encode({'type': 'gap', 'message': 'Поток завершён. Курсор мог устареть; начните новый просмотр.'})
                break
            try:
                row = json.loads(line)
                cursor = row.get('__CURSOR')
                if not isinstance(cursor, str) or len(cursor) > 512: continue
                message = assistant_diagnostics.sanitize(api, str(row.get('MESSAGE', '')), max_chars=4000, max_string=4000)
                unit = str(row.get('_SYSTEMD_UNIT', ''))
                yield encode({'type': 'record', 'cursor': cursor, 'at': int(row['__REALTIME_TIMESTAMP']) / 1000000,
                    'message': message, 'unit': unit if re.fullmatch(r'[a-zA-Z0-9@_.-]{1,160}', unit) else '', 'priority': str(row.get('PRIORITY', ''))[:1]})
            except (ValueError, TypeError, KeyError): continue
        else: yield encode({'type': 'paused', 'message': 'Сеанс завершён через 30 минут. Продолжите с текущего курсора.'})
    except (OSError, ValueError):
        yield encode({'type': 'gap', 'message': 'Журнал недоступен или запись превышает лимит. Начните новый просмотр.'})
    finally:
        try:
            if process is not None and process.returncode is None:
                try: process.kill()
                except ProcessLookupError: pass
                await process.wait()
        finally: release(user)
