"""Private, bounded HTTP evidence shared by the API and Mihomo manager.

Only route templates and controlled reason codes are stored. Never ingest URLs,
headers, bodies, exception messages, IPs, subscription tokens or client traffic.
"""
import asyncio
from contextvars import ContextVar
from contextlib import contextmanager, closing
import logging
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool
import diagnostic_context as tracing

request_id = ContextVar('observability_request_id', default='')
MAX_EVENTS = 100_000
DEFAULTS = {'enabled': True, 'retention_days': 14, 'max_events': 20_000, 'errors_only': False, 'revision': 0}
SUBSCRIPTION_REASONS = {'subscription_expired', 'subscription_exhausted', 'subscription_signer_unavailable',
                        'subscription_invalid_nonce', 'subscription_unsupported_format', 'subscription_client_mismatch',
                        'subscription_invalid_device', 'subscription_render_failed'}


class Settings(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool
    retention_days: int = Field(strict=True, ge=1, le=90)
    max_events: int = Field(strict=True, ge=100, le=MAX_EVENTS)
    errors_only: StrictBool
    revision: int = Field(strict=True, ge=0)


class Store:
    def __init__(self, root):
        self.directory = Path(root) / 'observability'
        self.path = self.directory / 'events.sqlite3'

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Limit database size as well as row count. Auto-vacuum reclaims purged pages.
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            self.path.chmod(0o600)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA auto_vacuum=FULL')
            db.execute('PRAGMA max_page_count=32768')
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER, retention_days INTEGER, max_events INTEGER, errors_only INTEGER, revision INTEGER)')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1,1,14,20000,0,0)')
            db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL, request_id TEXT, service TEXT, route TEXT, method TEXT, status INTEGER, duration_ms REAL, reason TEXT, profile_id TEXT, kind TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS events_at ON events(at)')
            db.execute('CREATE INDEX IF NOT EXISTS events_request ON events(request_id)')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(events)')}
            for name, kind in [('trace_id', 'TEXT'), ('parent_request_id', 'TEXT'), ('operation_id', 'TEXT'),
                               ('error_code', 'TEXT'), ('stage', 'TEXT'), ('retryable', 'INTEGER'), ('changed_fields', 'TEXT'), ('target_profile_id', 'TEXT'),
                               ('component', 'TEXT'), ('operation_type', 'TEXT')]:
                if name not in columns:
                    try: db.execute(f'ALTER TABLE events ADD COLUMN {name} {kind}')
                    except sqlite3.OperationalError:
                        if name not in {row['name'] for row in db.execute('PRAGMA table_info(events)')}: raise
            db.execute('CREATE INDEX IF NOT EXISTS events_trace ON events(trace_id)')
            db.execute('CREATE INDEX IF NOT EXISTS events_operation ON events(operation_id)')
            db.execute('CREATE INDEX IF NOT EXISTS events_component ON events(component)')
            db.execute('CREATE INDEX IF NOT EXISTS events_error_code ON events(error_code)')
            db.execute('CREATE TABLE IF NOT EXISTS probes (id TEXT PRIMARY KEY, at REAL, trace_id TEXT, profile_id TEXT, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS retention (id INTEGER PRIMARY KEY CHECK(id=1), tracked_since REAL, expired INTEGER, capacity INTEGER, coverage_since REAL)')
            if 'coverage_since' not in {row['name'] for row in db.execute('PRAGMA table_info(retention)')}:
                db.execute('ALTER TABLE retention ADD COLUMN coverage_since REAL')
            now = time.time()
            db.execute('INSERT OR IGNORE INTO retention (id,tracked_since,expired,capacity,coverage_since) VALUES (1,?,0,0,?)', (now, now))
            db.execute('UPDATE retention SET coverage_since=? WHERE coverage_since IS NULL', (now,))
            db.execute('CREATE INDEX IF NOT EXISTS probes_at ON probes(at)')
            db.commit()
            yield db

    def settings(self, db=None):
        if db is None:
            with self.connect() as db:
                return self.settings(db)
        result = dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())
        result.pop('id')
        for key in ('enabled', 'errors_only'): result[key] = bool(result[key])
        return result

    def trim(self, db, policy):
        expired = db.execute('DELETE FROM events WHERE at < ?', (time.time() - policy['retention_days'] * 86400,)).rowcount
        cutoff = db.execute('SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?', (policy['max_events'],)).fetchone()
        capacity = db.execute('DELETE FROM events WHERE id <= ?', (cutoff['id'],)).rowcount if cutoff else 0
        db.execute('UPDATE retention SET expired=expired+?,capacity=capacity+? WHERE id=1', (expired, capacity))

    def configure(self, payload):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.settings(db)
            if current['revision'] != payload.revision:
                raise HTTPException(409, 'Настройки журналов изменились. Обновите данные.')
            db.execute('UPDATE settings SET enabled=?,retention_days=?,max_events=?,errors_only=?,revision=revision+1 WHERE id=1',
                       (payload.enabled, payload.retention_days, payload.max_events, payload.errors_only))
            if current['enabled'] != payload.enabled or current['errors_only'] != payload.errors_only:
                db.execute('UPDATE retention SET coverage_since=? WHERE id=1', (time.time(),))
            self.trim(db, payload.model_dump())
            self.trim_probes(db, payload.model_dump())
            return self.settings(db)

    def append(self, event):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            policy = self.settings(db)
            self.trim(db, policy)
            if policy['enabled'] and (event.get('kind') == 'change' or not policy['errors_only'] or event['status'] >= 400):
                fields = ('at', 'request_id', 'service', 'route', 'method', 'status', 'duration_ms', 'reason', 'profile_id', 'kind',
                          'trace_id', 'parent_request_id', 'operation_id', 'error_code', 'stage', 'retryable', 'changed_fields', 'target_profile_id', 'component', 'operation_type')
                db.execute('INSERT INTO events (' + ','.join(fields) + ') VALUES (' + ','.join('?' for _ in fields) + ')', [event.get(key) for key in fields])
                self.trim(db, policy)

    def clear(self):
        with self.connect() as db:
            db.execute('DELETE FROM events')
            db.execute('DELETE FROM probes')
            now = time.time()
            db.execute('UPDATE retention SET tracked_since=?,coverage_since=?,expired=0,capacity=0 WHERE id=1', (now, now))

    def record_probe(self, result, profile_id=None):
        identity = uuid.uuid4().hex
        payload = json.dumps(result, ensure_ascii=False)
        if len(payload.encode('utf-8')) > 65536: raise ValueError('Probe evidence too large')
        with self.connect() as db:
            policy = self.settings(db)
            self.trim_probes(db, policy)
            if not policy['enabled']: return None
            db.execute('INSERT INTO probes VALUES (?,?,?,?,?)', (identity, time.time(), tracing.snapshot().get('trace_id'), profile_id, payload))
            self.trim_probes(db, policy)
        return identity

    def trim_probes(self, db, policy):
        db.execute('DELETE FROM probes WHERE at < ?', (time.time() - policy['retention_days'] * 86400,))
        db.execute('DELETE FROM probes WHERE id NOT IN (SELECT id FROM probes ORDER BY at DESC LIMIT 1000)')

    def probes(self, minutes=60, profile_id=None, limit=5):
        with self.connect() as db:
            self.trim_probes(db, self.settings(db))
            where, args = 'at >= ?', [time.time() - minutes * 60]
            if profile_id: where, args = where + ' AND profile_id=?', [*args, profile_id]
            rows = db.execute('SELECT * FROM probes WHERE ' + where + ' ORDER BY at DESC LIMIT ?', [*args, limit])
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]

    def read(self, minutes=60, identity=None, service=None, errors=False, limit=60, trace_id=None, operation_id=None, profile_id=None, changes=False, error_code=None, stage=None, kind=None, component=None, operation_type=None):
        with self.connect() as db:
            policy = self.settings(db)
            self.trim(db, policy)
            clauses, args = ['at >= ?'], [time.time() - minutes * 60]
            for field, value in [('request_id', identity), ('service', service), ('trace_id', trace_id), ('operation_id', operation_id), ('error_code', error_code), ('stage', stage), ('kind', kind), ('component', component), ('operation_type', operation_type)]:
                if value:
                    clauses.append(field + ' = ?')
                    args.append(value)
            if profile_id:
                clauses.append('(profile_id=? OR target_profile_id=?)')
                args.extend([profile_id, profile_id])
            if changes: clauses.append("kind='change'")
            where = ' AND '.join(clauses)
            counts = dict(db.execute('SELECT COUNT(*) AS requests, SUM(status>=400) AS errors, MIN(CASE WHEN status>=400 THEN at END) AS first_error_at, MAX(CASE WHEN status>=400 THEN at END) AS last_error_at, COUNT(DISTINCT CASE WHEN status>=400 THEN profile_id END) AS affected_profiles FROM events WHERE ' + where, args).fetchone())
            counts['errors'] = counts['errors'] or 0
            subscriptions = dict(db.execute("SELECT COUNT(*) AS attempts, COALESCE(SUM(status>=200 AND status<300),0) AS succeeded, COALESCE(SUM(status>=400),0) AS failed FROM events WHERE " + where + " AND kind='subscription'", args).fetchone())
            groups = [dict(row) for row in db.execute('SELECT service, reason, status, COUNT(*) AS count, MIN(at) AS first_at, MAX(at) AS last_at FROM events WHERE ' + where + ' AND status>=400 GROUP BY service, reason, status ORDER BY count DESC LIMIT 20', args)]
            rows = [dict(row) for row in db.execute('SELECT * FROM events WHERE ' + where + (' AND status>=400' if errors else '') + ' ORDER BY id DESC LIMIT ?', [*args, limit])]
            for row in rows:
                row['changed_fields'] = json.loads(row['changed_fields']) if row.get('changed_fields') else None
                if row['retryable'] is not None: row['retryable'] = bool(row['retryable'])
            retention = dict(db.execute('SELECT * FROM retention WHERE id=1').fetchone())
            retention.pop('id')
            retained = dict(db.execute('SELECT MIN(at) AS first_at,MAX(at) AS last_at,COUNT(*) AS retained_count FROM events').fetchone())
            return {'available': True, 'settings': policy, 'window_minutes': minutes, 'summary': counts, 'subscriptions': subscriptions, 'groups': groups, 'events': rows,
                    'storage_coverage': {**retention, **retained, 'requested_from': time.time() - minutes * 60,
                        'requested_until': time.time(), 'truncated': counts['errors' if errors else 'requests'] > len(rows),
                        'loss_scope': 'counters_since_tracking_or_last_clear; earlier_losses_and_failed_writes_unknown',
                        'successful_requests_recorded': policy['enabled'] and not policy['errors_only']},
                    'coverage': 'retained_events_only', 'affected_scope': 'known_profiles_with_HTTP_errors',
                    'limitations': ['История ограничена сроком и количеством событий; режим только ошибок исключает успешные запросы.',
                                    'Выдача подписки означает HTTP-доставку конфигурации, а не подключение клиента.',
                                    'Причина HTTP-ошибки не доказывает сбой DNS, outbound или сети клиента. Нужны журналы и измерения соответствующего слоя.']}


def reason(status, exception=False):
    if exception: return 'unhandled_exception'
    if status in (401, 403): return 'access_denied'
    if status == 404: return 'not_found'
    if status == 409: return 'conflict'
    if status in (400, 422): return 'invalid_request'
    if status == 429: return 'rate_limited'
    if status >= 500: return 'server_error'
    if status >= 400: return 'request_rejected'
    return 'completed'


def error_fields(request, status, component):
    state = getattr(request, 'state', None)
    context = getattr(state, 'diagnostic_context', {})
    code = getattr(state, 'evidence_reason', None)
    if code not in SUBSCRIPTION_REASONS: code = context.get('error_code') or reason(status)
    return {**tracing.details(code, component), **{key: context[key] for key in ('request_id', 'trace_id', 'operation_id') if tracing.valid(context.get(key))}}


def validation_response(request, exc, component):
    # Preserve useful field locations, never echo rejected input or error context.
    detail = [{key: row[key] for key in ('loc', 'msg', 'type') if key in row} for row in exc.errors()[:20]]
    return JSONResponse(status_code=422, content={'detail': detail, **error_fields(request, 422, component)})


class RequestEvidence:
    def __init__(self, app, root, service):
        self.app, self.root, self.service = app, root, service
        self.last_warning = 0

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        # Generate locally: callers cannot poison another request's identity.
        identity = uuid.uuid4().hex
        context = request_id.set(identity)
        trace = tracing.incoming({k.decode('latin1'): v.decode('latin1') for k, v in scope.get('headers', [])})
        trace['request_id'] = identity
        trace_context = tracing.current.set(trace)
        scope.setdefault('state', {})['request_id'] = identity
        scope['state']['trace_id'] = trace['trace_id']
        scope['state']['diagnostic_context'] = trace
        started, status, failed, content_type = time.monotonic(), 500, False, ''
        captured, too_large = bytearray(), False
        async def observed_receive():
            nonlocal too_large
            message = await receive()
            route = getattr(scope.get('route'), 'path', '')
            if message['type'] == 'http.request' and is_change(route, scope.get('method')):
                target = scope.get('path_params', {}).get('profile_id')
                if isinstance(target, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', target): trace['target_profile_id'] = target
                chunk = message.get('body', b'')
                if len(captured) + len(chunk) <= 16384 and not too_large: captured.extend(chunk)
                else:
                    captured.clear()
                    too_large = True
            return message
        async def observed_send(message):
            nonlocal status, content_type
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = [(key, value) for key, value in message.get('headers', []) if key.lower() != b'x-request-id']
                content_type = next((v.decode('latin1') for k, v in headers if k.lower() == b'content-type'), '')
                headers = [(k, v) for k, v in headers if k.lower() != b'x-trace-id']
                message = {**message, 'headers': [*headers, (b'x-request-id', identity.encode()), (b'x-trace-id', trace['trace_id'].encode())]}
            await send(message)
        try:
            await self.app(scope, observed_receive, observed_send)
        except Exception:
            failed, status = True, 500
            if not trace.get('error_code'): trace['error_code'] = 'unhandled_exception'
            # Messages/tracebacks may contain credentials; keep a controlled code.
            logging.getLogger('vps-control.requests').error('request_id=%s service=%s reason=unhandled_exception', identity, self.service)
            raise
        finally:
            route = getattr(scope.get('route'), 'path', '<unmatched>')
            # Instrumentation reads do not create their own evidence.
            if not route.startswith('/api/observability') or is_change(route, scope.get('method')):
                kind = 'subscription' if route == '/api/mihomo/subscriptions/{token}' and 'yaml' in content_type else 'request'
                if route == '/api/mihomo/subscriptions/{token}' and status >= 400: kind = 'subscription'
                if is_change(route, scope.get('method')): kind = 'change'
                profile = scope['state'].get('observed_profile')
                if not isinstance(profile, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', profile): profile = None
                event = {'at': time.time(), 'request_id': identity, 'service': self.service, 'route': route,
                         'method': scope.get('method') if scope.get('method') in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') else 'OTHER',
                         'status': status, 'duration_ms': round((time.monotonic() - started) * 1000, 2),
                         'reason': scope['state'].get('evidence_reason') if status >= 400 and scope['state'].get('evidence_reason') in
                         SUBSCRIPTION_REASONS else reason(status, failed),
                         'profile_id': profile, 'kind': kind,
                         'component': component_for_route(route, self.service),
                         'operation_type': 'catalog' if route == '/api/assistant/models' else 'chat' if route == '/api/assistant/chat' else 'mcp_tool' if route == '/api/assistant/control-tool' else 'mutation' if kind == 'change' else 'read' if scope.get('method') in ('GET', 'HEAD') else 'request'}
                code = event['reason']
                if status >= 400 and code not in SUBSCRIPTION_REASONS: code = trace.get('error_code', code)
                event.update(trace_id=trace['trace_id'], parent_request_id=trace.get('parent_request_id'), operation_id=trace.get('operation_id'),
                             error_code=code if status >= 400 else None, stage=tracing.details(code, self.service)['stage'] if status >= 400 else 'response',
                             retryable=tracing.details(code, self.service)['retryable'] if status >= 400 else None)
                target = scope.get('path_params', {}).get('profile_id')
                if is_change(route, scope.get('method')) and isinstance(target, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', target): event['target_profile_id'] = target
                if kind == 'change' and captured and not too_large:
                    try: event['changed_fields'] = json.dumps(tracing.changed_names(json.loads(captured)))
                    except (ValueError, UnicodeError, RecursionError): pass
                captured.clear()
                try:
                    await asyncio.to_thread(Store(self.root()).append, event)
                except (OSError, sqlite3.Error):
                    if time.monotonic() - self.last_warning > 60:
                        logging.getLogger('vps-control.requests').warning('Request evidence storage unavailable; service=%s', self.service)
                        self.last_warning = time.monotonic()
            request_id.reset(context)
            tracing.current.reset(trace_context)


def is_change(route, method):
    return (method in ('POST', 'PUT', 'PATCH', 'DELETE') and
            any(part in route for part in ('/settings', '/profiles', '/modules', '/services', '/network', '/dns', '/application/action', '/routing')) and
            not any(part in route for part in ('/check', '/diagnostic', '/stats', '/subscriptions/{token}')))


def component_for_route(route, service):
    if service == 'mihomo': return 'mihomo'
    for prefix, component in (('/api/assistant', 'nodera'), ('/api/mihomo', 'mihomo'),
                              ('/api/network', 'network'), ('/api/dns', 'dns'), ('/api/security', 'security'),
                              ('/api/application', 'application'), ('/api/services', 'services'), ('/api/protocol-images', 'components')):
        if route == prefix or route.startswith(prefix + '/'): return component
    return 'panel'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['clear'])
    parser.add_argument('root', type=Path)
    arguments = parser.parse_args()
    Store(arguments.root).clear()
