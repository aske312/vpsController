"""Declarative operation/service watches. No commands, network probes or repairs."""
import re
import math
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, model_validator

import alert_context
from operation_outcome import outcome, is_check


EVENTS = ('failed', 'unknown', 'stalled', 'succeeded', 'cancelled', 'recovered')


class Watch(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern='^[0-9a-f]{32}$')
    name: str = Field(min_length=1, max_length=80, pattern=r'^[^\x00-\x1f\x7f]+$')
    enabled: StrictBool = True
    source: Literal['operation', 'service'] = 'operation'
    target: str = Field(default='', pattern='^[a-zA-Z0-9_-]{0,80}$')
    operation_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    events: list[Literal['failed', 'unknown', 'stalled', 'succeeded', 'cancelled', 'recovered']] = Field(default_factory=lambda: ['failed', 'unknown', 'stalled'], min_length=1, max_length=6)
    after_seconds: int = Field(default=600, ge=60, le=86400)
    snoozed_until: int = Field(default=0, ge=0, le=4102444800)

    @model_validator(mode='before')
    @classmethod
    def service_defaults(cls, value):
        if isinstance(value, dict) and value.get('source') == 'service' and 'events' not in value:
            return {**value, 'events': ['failed', 'unknown', 'recovered']}
        return value

    @model_validator(mode='after')
    def scope(self):
        if len(set(self.events)) != len(self.events): raise ValueError('Repeated event')
        if self.source == 'service' and (not self.target or self.operation_id or set(self.events) - {'failed', 'unknown', 'recovered'}):
            raise ValueError('A service watch needs a service ID and failed/unknown/recovered events')
        return self


def epoch(value):
    from diagnostic_tools import epoch as parse
    if type(value) in (int, float): return value if math.isfinite(value) and 0 <= value <= 4102444800 else None
    return parse(value)


def operation_rows(api):
    from application_diagnostics import records
    rows, coverage = records(api, scan_limit=2000)
    rows.sort(key=lambda item: epoch(item[1].get('updated_at') or item[1].get('started_at')) or 0, reverse=True)
    return rows[:2000], coverage


def catalog(api):
    definitions = api.managed_services() if hasattr(api, 'managed_services') else {}
    rows, coverage = operation_rows(api)
    actions = sorted({row.get('action') for _, row in rows if isinstance(row.get('action'), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', row['action'])})
    return {'services': [{'id': key, 'name': alert_context.label(value.get('name'), key), 'unit': value.get('unit')}
                         for key, value in definitions.items()], 'operation_actions': actions,
            'events': list(EVENTS), 'watch_schema': Watch.model_json_schema(), 'sampling_seconds': 60, 'operation_coverage': coverage}


def configure_state(value, config, now):
    """New/changed rules start a fresh baseline; unrelated changes preserve deduplication."""
    old = {row['id']: row for row in value['settings'].get('watches', [])}
    state = value.get('watch_state', {})
    result = {}
    for row in config['watches']:
        result[row['id']] = state.get(row['id'], {}) if old.get(row['id']) == row else {'since': now, 'items': {}}
    if config['operations']:
        result['operations'] = state.get('operations', {}) if value['settings'].get('operations') and value['settings'].get('operation_timeout_seconds') == config['operation_timeout_seconds'] else {'since': now, 'items': {}}
    if config['snoozed_until'] > now:
        for record in result.values(): record['paused'] = True
    return result


def operation_component(row, source):
    component = row.get('component')
    if isinstance(component, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', component): return component
    if source == 'mihomo': return 'mihomo'
    if source == 'cdn': return 'cdn'
    action = row.get('action')
    if isinstance(action, str):
        if action in ('reboot', 'poweroff'): return 'system'
        for prefix, identity in (('mihomo', 'mihomo'), ('dns', 'dns'), ('network', 'network'), ('relay', 'relay'), ('ssh', 'ssh'), ('automation', 'updates'), ('security', 'security')):
            if action.startswith(prefix + '-'): return identity
    return 'application'


def evaluate(value, rows, context, now):
    config = value['settings']
    rules = [*config['watches']]
    if config['operations']:
        rules.append({'id': 'operations', 'name': 'Операции приложения', 'enabled': True, 'source': 'operation', 'target': '', 'operation_id': None,
                      'events': ['failed', 'unknown', 'stalled', 'recovered'], 'after_seconds': config['operation_timeout_seconds'], 'snoozed_until': 0})
    state = value.setdefault('watch_state', {})
    result = []
    for rule in rules:
        record = state.setdefault(rule['id'], {'since': now, 'items': {}})
        if not rule['enabled'] or rule['snoozed_until'] > now or config['snoozed_until'] > now:
            record['paused'] = True
            continue
        if record.pop('paused', False):
            record.update(since=now, items={})
            record.pop('unknown_since', None)
        record['checked_at'] = now
        observations = []
        observed_times = []
        if rule['source'] == 'service':
            observed = context.get('services', {}).get(rule['target'], {})
            service_state = observed.get('state')
            status = 'failed' if service_state in ('error', 'stopped') else 'recovered' if service_state in alert_context.GOOD else 'warning' if service_state in ('warning', 'warn', 'possible_issues', 'degraded') else 'unknown'
            if status == 'unknown':
                since = record.setdefault('unknown_since', now)
                if now - since < rule['after_seconds']: continue
            else: record.pop('unknown_since', None)
            observations.append((rule['target'], status, {'component': rule['target'], 'service_state': service_state}))
        else:
            for source, row in rows:
                if is_check(row) and not rule['target'] and not rule['operation_id']: continue
                if rule['target'] and row.get('action') != rule['target'] or rule['operation_id'] and row.get('id') != rule['operation_id']: continue
                at = epoch(row.get('updated_at') or row.get('finished_at') or row.get('started_at'))
                if at is not None and at > now + 60: at = None
                if at is not None: observed_times.append(at)
                status = outcome(row)
                started = epoch(row.get('started_at'))
                status = {'error': 'failed', 'success': 'succeeded'}.get(status, status)
                if status == 'active': status = 'stalled' if started is not None and now - started >= rule['after_seconds'] else 'running'
                if row.get('resolved_at') and status == 'failed': status = 'acknowledged'
                identity = source + ':' + row['id']
                pending_unknown = record.setdefault('operation_unknown_since', {})
                cutoff = max(record.get('cursor', record['since']), record['since'])
                if not rule['operation_id'] and identity not in record['items'] and identity not in pending_unknown and status in ('failed', 'unknown', 'succeeded', 'cancelled', 'acknowledged') and (at is None or at <= cutoff): continue
                if status == 'unknown':
                    since = pending_unknown.setdefault(identity, now)
                    if now - since < rule['after_seconds']: continue
                else: pending_unknown.pop(identity, None)
                # Explicit IDs can watch their current outcome; broad rules never replay old terminal history.
                observations.append((identity, status, {'component': operation_component(row, source),
                    'operation_id': row['id'], 'action': row.get('action'), 'profile_id': row.get('profile_id'), 'stage': row.get('stage'), 'error_code': row.get('error_code')}))
        for identity, status, details in observations:
            previous = record['items'].get(identity)
            if rule['source'] == 'service' and status == 'warning' and previous in ('failed', 'unknown'):
                continue  # A warning neither opens an incident nor proves its recovery.
            if previous == status: continue
            record['items'][identity] = status
            event = 'recovered' if status in ('recovered', 'succeeded') and previous in ('failed', 'unknown', 'stalled') and 'recovered' in rule['events'] else status
            if event == 'recovered' and previous not in ('failed', 'unknown', 'stalled'): continue
            if event not in rule['events']: continue
            result.append({'watch_id': rule['id'], 'watch_name': rule['name'], 'event': event, 'at': now, **details})
        record['items'] = dict(list(record['items'].items())[-2000:])
        if 'operation_unknown_since' in record:
            record['operation_unknown_since'] = dict(list(record['operation_unknown_since'].items())[-2000:])
        if rule['source'] == 'operation':
            record['cursor'] = max([record.get('cursor', record['since']), *observed_times])
    return result


def format_event(row, context):
    component = row.get('component')
    titles = {'failed': 'Ошибка', 'unknown': 'Результат не подтверждён', 'stalled': 'Операция выполняется дольше порога',
              'succeeded': 'Операция завершена', 'cancelled': 'Операция отменена', 'recovered': 'Состояние подтверждено повторной проверкой'}
    lines = [titles[row['event']] + ' · ' + row['watch_name'], 'Зона: ' + alert_context.zone(component),
             *alert_context.lines(context, component, profile_id=row.get('profile_id'))]
    service_state = {'error': 'ошибка выполнения', 'stopped': 'остановлена', 'running': 'работает', 'applied': 'настройки применены',
                     'succeeded': 'задача выполнена', 'listening': 'ожидает подключения', 'waiting': 'ожидает запуска'}.get(row.get('service_state'))
    if service_state: lines.append('Служба: ' + service_state + '.')
    if row.get('operation_id'): lines.append('Операция: ' + row['operation_id'])
    for key, title in (('action', 'Действие'), ('stage', 'Этап'), ('error_code', 'Код ошибки')):
        value = row.get(key)
        if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}', value): lines.append(title + ': ' + value)
    if row['event'] in ('failed', 'unknown', 'stalled'):
        lines.append('Что делать: проверьте историю операции и её журнал.' if row.get('operation_id') else 'Что делать: проверьте состояние и журнал службы.')
    return '\n'.join(lines)
