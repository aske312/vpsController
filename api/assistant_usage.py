"""Private per-request AI counters; no prompts, answers, credentials or price guesses."""
from contextlib import contextmanager
import json
import re
import sqlite3
import time

RETENTION_DAYS = 90


def path(api):
    return api.DATA_DIR / 'assistant' / 'usage' / 'usage.sqlite3'


@contextmanager
def database(api):
    file = path(api)
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(file, timeout=5)
    try:
        file.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, channel TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL,
            status TEXT NOT NULL, model_calls INTEGER NOT NULL, reported_calls INTEGER NOT NULL,
            prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
            retries INTEGER NOT NULL, complete INTEGER NOT NULL, event_count INTEGER NOT NULL)''')
        connection.execute('CREATE INDEX IF NOT EXISTS requests_created ON requests(created)')
        yield connection
        connection.commit()
    finally:
        connection.close()


def store_trace(api, file):
    # A trace is bounded to 256 KiB by assistant_trace. Only counters leave it.
    with file.open(encoding='utf-8') as stream:
        lines = stream.read(262144 + 4096).splitlines()
    if not lines: return
    meta = json.loads(lines[0])
    events = []
    for line in lines[1:]:
        try:
            event = json.loads(line)
            if isinstance(event, dict): events.append(event)
        except ValueError: break
    store_events(api, meta, events)


def store_events(api, meta, events):
    if (not isinstance(meta, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(meta.get('request_id', '')))
            or not re.fullmatch(r'panel|[1-9][0-9]{0,15}', str(meta.get('actor', '')))
            or meta.get('channel') not in ('panel', 'bot')): return
    calls = sum(e.get('stage') in ('model_start', 'summary_start', 'provider_retry') for e in events)
    retries = sum(e.get('stage') == 'provider_retry' for e in events)
    measured = [e for e in events if e.get('stage') in ('model_result', 'summary_result')
                and all(type(e.get(k)) is int and 0 <= e[k] <= 10**9 for k in ('prompt_tokens', 'completion_tokens'))]
    finished = [e for e in events if e.get('stage') == 'request_finished']
    runtime = next((e for e in reversed(events) if e.get('stage') == 'runtime_started'), {})
    status = finished[-1].get('status', 'unknown') if finished else 'unknown'
    complete = len(measured) == calls and not any(e.get('stage') == 'trace_truncated' for e in events)
    created = meta.get('created_at')
    if type(created) not in (int, float) or not 0 < created <= time.time() + 60: return
    row = (meta['request_id'], str(meta['actor']), meta['channel'], runtime.get('provider_id', ''),
           runtime.get('model_id', ''), created, status, calls, len(measured),
           sum(e['prompt_tokens'] for e in measured), sum(e['completion_tokens'] for e in measured), retries, int(complete), len(events))
    with database(api) as db:
        # A concurrent backfill must not overwrite a newer runtime checkpoint.
        columns = ('id', 'actor', 'channel', 'provider', 'model', 'created', 'status', 'model_calls',
                   'reported_calls', 'prompt_tokens', 'completion_tokens', 'retries', 'complete', 'event_count')
        updates = ','.join(f'{name}=excluded.{name}' for name in columns[1:])
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                   'ON CONFLICT(id) DO UPDATE SET ' + updates +
                   ' WHERE excluded.event_count >= requests.event_count', row)
        db.execute('DELETE FROM requests WHERE created < ?', (time.time() - RETENTION_DAYS * 86400,))


def backfill(api):
    """Import the retained diagnostic window once; never invent older history."""
    from assistant_trace import directory, recent_files
    with database(api) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        if db.execute("SELECT 1 FROM metadata WHERE key='backfilled'").fetchone(): return
    for file, _ in recent_files(directory(api)):
        try: store_trace(api, file)
        except (OSError, ValueError, TypeError, sqlite3.Error): continue
    with database(api) as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('backfilled', '1')")


def report(api, days=30):
    backfill(api)
    since = time.time() - days * 86400
    # Deleting an account must not erase its consumption or assign it elsewhere.
    with database(api) as db:
        db.execute('DELETE FROM requests WHERE created < ?', (time.time() - RETENTION_DAYS * 86400,))
        rows = db.execute('''SELECT actor, channel, provider, model, COUNT(*) AS requests,
            SUM(status IN ('success','awaiting_confirmation')) AS completed,
            SUM(status IN ('partial','incomplete')) AS incomplete,
            SUM(status = 'failed') AS failed, SUM(status = 'unknown') AS unknown,
            SUM(model_calls) AS model_calls, SUM(reported_calls) AS reported_calls,
            SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens,
            SUM(retries) AS retries, SUM(complete = 0) AS unmeasured_requests
            FROM requests WHERE created >= ?
            GROUP BY actor, channel, provider, model ORDER BY requests DESC, actor''', (since,)).fetchall()
        earliest = db.execute('SELECT MIN(created) FROM requests').fetchone()[0]
    return {'days': days, 'since': since, 'earliest_record_at': earliest, 'retention_days': RETENTION_DAYS,
            'rows': [dict(row) for row in rows], 'cost_available': False}
