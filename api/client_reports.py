"""V1 client observations. Never authoritative for billing or server health."""
from contextlib import closing
import json
import sqlite3
import time
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class Counters(BaseModel):
    model_config = {'extra': 'forbid'}
    upload: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)
    download: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)
    connections: int | None = Field(default=None, strict=True, ge=0, le=1_000_000)


class Event(Counters):
    time: AwareDatetime
    event: Literal['opened', 'traffic', 'closed']
    delta_upload: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)
    delta_download: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)


class Diagnostics(BaseModel):
    model_config = {'extra': 'forbid'}
    state: Literal['connected', 'disconnected', 'failed']
    mode: Literal['tun', 'proxy']
    core_version: str = Field(default='', max_length=40, pattern=r'^[0-9A-Za-z.+_-]*$')
    network_check: Literal['ok', 'error', 'checking', 'unverified'] = 'unverified'
    network_check_age_seconds: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    uptime_seconds: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    issues: list[Literal['core_start_failed', 'core_api_unavailable', 'https_probe_failed', 'ipv6_route_unavailable']] = Field(default_factory=list, max_length=4)


class Report(BaseModel):
    model_config = {'extra': 'forbid'}
    version: Literal[1]
    report_id: UUID
    session_id: UUID
    observed_at: AwareDatetime
    client_version: str = Field(min_length=1, max_length=40, pattern=r'^[0-9A-Za-z.+_-]+$')
    counters: Counters
    events: list[Event] = Field(default_factory=list, max_length=100)
    diagnostics: Diagnostics | None = None


class TooFrequent(Exception):
    pass


def accept(root, profile_id: str, device_id: str, report: Report, *, now=None):
    """Bounded storage, idempotency and per-device rate limit in one transaction."""
    now = time.time() if now is None else now
    directory = root / 'client-reports'
    if directory.is_symlink():
        raise OSError('Client report directory is a symlink')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'reports.sqlite3'
    if path.is_symlink():
        raise OSError('Client report store is a symlink')
    with closing(sqlite3.connect(path, timeout=2)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS reports (profile TEXT, device TEXT, id TEXT, at REAL, payload TEXT, PRIMARY KEY(profile, device, id))')
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM reports WHERE at < ?', (now - 7 * 86400,))
        key = (profile_id, device_id, str(report.report_id))
        if db.execute('SELECT 1 FROM reports WHERE profile=? AND device=? AND id=?', key).fetchone():
            return {'version': 1, 'accepted': True, 'duplicate': True}
        previous = db.execute('SELECT MAX(at) FROM reports WHERE profile=? AND device=?', key[:2]).fetchone()[0]
        if previous is not None and now - previous < 15:
            raise TooFrequent()
        db.execute('INSERT INTO reports VALUES (?,?,?,?,?)', (*key, now, report.model_dump_json()))
        db.execute('DELETE FROM reports WHERE profile=? AND device=? AND rowid NOT IN (SELECT rowid FROM reports WHERE profile=? AND device=? ORDER BY at DESC, rowid DESC LIMIT 100)', (*key[:2], *key[:2]))
        db.execute('DELETE FROM reports WHERE rowid NOT IN (SELECT rowid FROM reports ORDER BY at DESC, rowid DESC LIMIT 10000)')
    return {'version': 1, 'accepted': True, 'duplicate': False}


def read(root, profile_id, device_id=None, *, limit=10, now=None):
    """Read only the authorized profile; client clocks and counters are evidence, not authority."""
    now = time.time() if now is None else now
    path = root / 'client-reports' / 'reports.sqlite3'
    result = {'profile_id': profile_id, 'device_id': device_id, 'items': [], 'state': 'no_reports',
              'source': 'untrusted_client_observations', 'retention_days': 7,
              'instruction': 'Отчёты клиента не доказывают состояние сервера, причину сбоя или работу всех сайтов. Не суммируй снимки счётчиков и повторяющиеся события. null означает неизвестно. Учитывай received_at, observed_at, свежесть, session_id и network_check_age_seconds. Клиентские часы не являются доверенным временем сервера.'}
    if not path.exists(): return result
    if path.is_symlink() or path.parent.is_symlink(): raise OSError('Client report store is a symlink')
    where, params = 'profile=? AND at>=?', [profile_id, now - 7 * 86400]
    if device_id is not None:
        where += ' AND device=?'
        params.append(device_id)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        rows = db.execute('SELECT device, at, payload FROM reports WHERE ' + where + ' ORDER BY at DESC, rowid DESC LIMIT ?',
                          (*params, max(1, min(20, limit)))).fetchall()
    for device, received, payload in rows:
        report = Report.model_validate_json(payload)
        result['items'].append({'device_id': device, 'received_at': received,
                                'age_seconds': max(0, round(now - received)), 'fresh': now - received <= 90,
                                'client_time_offset_seconds': round(report.observed_at.timestamp() - received),
                                **report.model_dump(mode='json')})
    if rows: result['state'] = 'recent' if result['items'][0]['fresh'] else 'stale'
    while len(json.dumps(result, ensure_ascii=False).encode()) > 22000:
        if len(result['items']) > 1: result['items'].pop()
        elif result['items'][0]['events']: result['items'][0]['events'].pop(0)
        else: break
        result['truncated'] = True
    return result
