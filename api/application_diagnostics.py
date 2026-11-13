"""Passive application evidence and bounded, configured integration measurements."""
import http.client
import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.parse
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import application_operation as operations
import assistant_control as control
import diagnostic_context as tracing
import diagnostic_tools as reports
import observability
import operation_log
import operation_outcome
from service_state import observation_scope, prime_services

MAX_OPERATION_SCAN_BYTES = 8 * 1024 * 1024


class SummaryQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    component: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    minutes: int = Field(default=60, strict=True, ge=1, le=1440)


class TimelineQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    operation_id: str = Field(pattern='^[0-9a-f]{32}$')
    limit: int = Field(default=60, strict=True, ge=1, le=120)
    include_journal: bool = False


class OperationsQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    action: str | None = Field(default=None, max_length=100, pattern='^[a-zA-Z0-9_:-]+$')
    source: Literal['system', 'mihomo', 'cdn'] | None = None
    outcome: Literal['active', 'success', 'error', 'unknown', 'cancelled', 'skipped'] | None = None
    minutes: int = Field(default=1440, strict=True, ge=1, le=129600)
    include_checks: bool = False
    offset: int = Field(default=0, strict=True, ge=0, le=6000)
    limit: int = Field(default=20, strict=True, ge=1, le=50)


class IntegrationQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    integration_id: str = Field(pattern='^(ai|dns):[a-zA-Z0-9_-]{1,80}$')
    mode: Literal['dns', 'tls', 'api', 'functional'] = 'tls'
    model_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^\S+$')


def inventory(api):
    # Never include addresses, credentials or model names in diagnostic inventory.
    settings = control.read(api)
    return [{'id': 'ai:' + row['id'], 'type': 'ai_provider', 'configured': bool(row.get('provider_url') and row.get('api_key')),
             'modes': ['dns', 'tls', 'api'], 'functional_test': 'not_checked; api mode reads model catalog only'}
            for row in control.providers(settings) if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', row['id'])][:32] + [
                {'id': 'dns:' + row['id'], 'type': 'dns_provider', 'configured': True, 'modes': ['dns']}
                for row in api.dns_provider_list() if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', row['id'])][:32]


def versions(api):
    result = {}
    for component in control.MODULES:
        try: version = (control.runtime_directory(component) / 'version').read_text(encoding='utf-8').strip()
        except OSError: version = ''
        result[component] = version if re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', version) else None
    try:
        metadata = json.loads((api.INSTALL_DIR / 'package.json').read_text(encoding='utf-8'))
        version = metadata.get('version', '')
        result['application'] = version if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', version) else None
    except (OSError, ValueError, AttributeError): result['application'] = None
    try:
        version = api.mihomo_core_installed_version()
        result['mihomo_core'] = version if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+', version) else None
    except (OSError, ValueError, AttributeError): result['mihomo_core'] = None
    return result


def records(api, scan_limit=200):
    rows, coverage = [], {}
    budget = MAX_OPERATION_SCAN_BYTES
    roots = [('system', api.DATA_DIR / 'operations'), ('mihomo', api.DATA_DIR / 'mihomo/operations'),
             ('cdn', api.DATA_DIR / 'cdn-operations')]
    for source, root in roots:
        try: paths = sorted(root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            coverage[source] = {'available': False}
            continue
        coverage[source] = {'available': True, 'scan_truncated': len(paths) > scan_limit, 'unreadable': 0}
        for path in paths[:scan_limit]:
            if not tracing.valid(path.stem): continue
            try:
                size = path.stat().st_size
                if path.is_symlink() or size > 128 * 1024: raise ValueError('unsafe record')
                if size > budget:
                    coverage[source].update(scan_truncated=True, byte_limit_reached=True)
                    break
                budget -= size
                value = operations.read(path)
                if value.get('id') != path.stem: raise ValueError('identity mismatch')
                rows.append((source, operations.diagnostic_state(value)))
            except (OSError, ValueError, RecursionError):
                coverage[source]['unreadable'] += 1
                rows.append((source, {'id': path.stem, 'state': 'unknown'}))
    return rows, coverage


def safe_record(value, source, api=None):
    value = operations.diagnostic_state(value)
    def component_list(name):
        rows = value.get(name)
        return [key for key in (rows if isinstance(rows, list) else [])[:32] if isinstance(key, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', key)]
    def revisions(name):
        rows = value.get(name)
        return {key: entry for key, entry in (rows.items() if isinstance(rows, dict) else []) if isinstance(entry, str) and re.fullmatch('[0-9a-f]{64}', entry)}
    result = {'operation_id': value.get('id'), 'source': source,
            'context': operation_outcome.context(value),
            'origin_unit': value.get('origin_unit') if isinstance(value.get('origin_unit'), str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9@_.-]{0,150}\.service', value['origin_unit']) else None,
            'systemd_invocation_id': value.get('systemd_invocation_id') if isinstance(value.get('systemd_invocation_id'), str) and re.fullmatch(r'[0-9a-f]{32}', value['systemd_invocation_id']) else None,
            'integration_id': value.get('integration_id') if isinstance(value.get('integration_id'), str) and re.fullmatch(r'ai:[0-9a-f]{32}', value['integration_id']) else None,
            **{key: operation_log.public_text(value.get(key)) if isinstance(value.get(key), str) else '' for key in
               ('action', 'state', 'result', 'stage', 'stage_result', 'error_code', 'message', 'unit', 'reason', 'started_at', 'updated_at', 'finished_at')},
            'health_check': operation_log.safe_health(value['health_check']) if isinstance(value.get('health_check'), dict) else None,
            'changed_components': component_list('changed_components'),
            'runtime_changed_components': component_list('runtime_changed_components'),
            'affected_components': component_list('affected_components'),
            'configuration_before': revisions('configuration_before'),
            'configuration_after': revisions('configuration_after'),
            **{key: value[key] for key in ('trace_id', 'parent_request_id') if tracing.valid(value.get(key))}}
    if api is not None:
        import assistant_diagnostics
        return assistant_diagnostics.sanitize(api, result, max_chars=5000, max_string=1200)
    return result


def timeline(api, query):
    result = reports.report('operation_timeline')
    found = False
    events = []
    legacy = False
    authoritative = None
    parent_unavailable = False
    for source, directory in [('system', api.DATA_DIR / 'operations'), ('mihomo', api.DATA_DIR / 'mihomo/operations'),
                              ('cdn', api.DATA_DIR / 'cdn-operations')]:
        path = directory / (query.operation_id + '.json')
        try:
            if path.is_symlink(): raise ValueError('unsafe record')
            record = operations.read(path)
            if record:
                if record.get('id') != query.operation_id: raise ValueError('identity mismatch')
                found = True
                if authoritative is None: authoritative = (source, record)
                reports.observation(result, 'operation:' + source, source + '_operation_history', safe_record(record, source, api))
                if record.get('action') == 'integration-functional-probe' and isinstance(record.get('diagnostic_report'), dict):
                    reports.observation(result, 'functional_report', 'confirmed_diagnostic_execution', record['diagnostic_report'])
            journal_path = operation_log.location(api.DATA_DIR, source, query.operation_id)
            if journal_path.is_symlink() or journal_path.exists() and journal_path.stat().st_size > operation_log.MAX_BYTES:
                raise ValueError('unsafe journal')
            journal = operations.read(journal_path)
            if journal:
                if journal.get('id') != query.operation_id or journal.get('source') != source: raise ValueError('identity mismatch')
                dropped = journal.get('dropped')
                result['coverage'][source] = {'dropped': dropped, 'journal_available': True, 'schema_version': journal.get('schema_version', 1)}
                legacy = legacy or journal.get('schema_version', 1) < 2
                for index, event in enumerate(journal.get('events', [])[-256:]):
                    if not isinstance(event, dict): continue
                    at = reports.epoch(event.get('at'))
                    if at is None: continue
                    data = {key: operation_log.public_text(event.get(key)) for key in
                            ('state', 'message', 'stage', 'result', 'component', 'error_code', 'command', 'dependency', 'span_id') if event.get(key) is not None}
                    if type(event.get('progress')) is int: data['progress'] = event['progress']
                    for key in ('exit_code', 'duration_ms'):
                        if type(event.get(key)) in (int, float): data[key] = event[key]
                    if isinstance(event.get('health_check'), dict): data['health_check'] = operation_log.safe_health(event['health_check'])
                    if isinstance(event.get('changed_components'), list): data['changed_components'] = event['changed_components'][:32]
                    events.append({'id': f'{source}:{index}', 'at': at, 'source': source + '_stage_journal', 'data': data})
                if dropped: result['missing_data'].append({'source': source + '_stage_journal', 'reason': 'older_stages_dropped'})
            elif record:
                result['missing_data'].append({'source': source + '_stage_journal', 'reason': 'not_recorded_or_expired'})
        except (OSError, ValueError, TypeError):
            if source == 'system' and (path.exists() or path.is_symlink()): parent_unavailable = True
            reports.unknown(result, source + '_operation_history', 'record_unavailable')
    try:
        history = observability.Store(api.DATA_DIR).read(minutes=129600, operation_id=query.operation_id, limit=query.limit)
        result['coverage']['http'] = history['storage_coverage']
        result['coverage']['http_policy_revision'] = history['settings']['revision']
        for event in history['events']:
            events.append({'id': 'http:' + str(event['id']), 'at': event['at'], 'source': 'request_history',
                           'data': {key: event.get(key) for key in ('request_id', 'trace_id', 'service', 'status', 'stage', 'error_code', 'kind', 'changed_fields')}})
    except (OSError, ValueError, sqlite3.Error): reports.unknown(result, 'request_history', 'storage_unavailable')
    if query.include_journal:
        import observability_control
        try:
            journal = observability_control.journal(api, operation_id=query.operation_id, minutes=10080, limit=query.limit)
            reports.observation(result, 'journal', 'journald', journal)
            for row in journal.get('records', []):
                if row.get('operation_id') == query.operation_id or journal['coverage'].get('operation_unit_selected'):
                    events.append({**row, 'relation': 'operation_unit_or_explicit_ID'})
            result['coverage']['journald'] = journal.get('coverage')
        except (OSError, ValueError, HTTPException): reports.unknown(result, 'journal', 'journal_unavailable')
    events.sort(key=lambda row: (row['at'], row['id']))
    spans = {}
    for event in events:
        data = event.get('data', {})
        span = data.get('span_id')
        if not tracing.valid(span): continue
        invocation = spans.setdefault(span, {'span_id': span, 'command': data.get('command'), 'dependency': data.get('dependency'), 'result': 'unknown'})
        if data.get('result') == 'running': invocation['started_at'] = event['at']
        else: invocation.update(finished_at=event['at'], result=data.get('result', 'unknown'), exit_code=data.get('exit_code'), duration_ms=data.get('duration_ms'))
    result['command_invocations'] = list(spans.values())[-query.limit:]
    incomplete = sum('finished_at' not in row or 'started_at' not in row for row in spans.values())
    if incomplete: result['missing_data'].append({'source': 'command_spans', 'reason': 'missing_start_or_completion_in_retained_evidence', 'count': incomplete})
    result['replay_requests'] = sum(event.get('data', {}).get('stage') == 'admission_replay' for event in events)
    result['timeline'] = events[-query.limit:]
    result['coverage'].update(operation_id=query.operation_id, limit=query.limit, truncated=len(events) > query.limit,
                              semantics='parent system operation owns final result; timestamps alone do not establish causation')
    if not found: result['missing_data'].append({'source': 'operation', 'reason': 'not_found_or_expired'})
    if legacy: result['missing_data'].append({'source': 'unrecorded_stages', 'reason': 'legacy_journal_has_no_structured_stage_contract'})
    result['conflicts'] = [{'request_id': row['data'].get('request_id'), 'trace_id': row['data'].get('trace_id'),
        'error_code': row['data'].get('error_code'), 'at': row['at']} for row in events if row.get('data', {}).get('status') == 409]
    result['current_admission'] = admission_context(api)
    if parent_unavailable:
        result['authoritative_result'] = {'operation_id': query.operation_id, 'source': 'system', 'state': 'unknown',
            'reason': 'parent_record_unavailable; child_result_does_not_confirm_parent_completion'}
    elif authoritative:
        source, value = authoritative
        result['authoritative_result'] = safe_record(value, source, api)
        if isinstance(value.get('unit'), str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9@_.-]{0,150}\.service', value['unit']):
            try:
                worker = operations.worker_observation(value, api.observe_service)
                reports.observation(result, 'current_worker', 'systemd_current_state', {key: worker.get(key) for key in ('unit_present', 'active', 'state', 'substate', 'result')})
                result['coverage']['worker_scope'] = 'current_observation; collected_units_and_previous_boots_do_not_prove_historical_success_or_failure'
            except (OSError, ValueError, AttributeError): reports.unknown(result, 'current_worker', 'worker_observation_unavailable')
    return result


def admission_context(api):
    markers = ('relay/pending.json', 'dns-recovery.json', 'automation-recovery.json', 'mihomo/profile-recovery/manifest.json', 'recovery/security-recovery.txt', 'recovery/application-update')
    result = {'recovery_markers': [name for name in markers if (api.DATA_DIR / name).exists() or (api.DATA_DIR / name).is_symlink()],
              'lock_owner': 'not_observed; process_or_recovery_presence_is_not_proof_of_lock_ownership'}
    path = api.DATA_DIR / 'application-action.json'
    try:
        if path.is_symlink() or path.exists() and path.stat().st_size > 128 * 1024: raise ValueError()
        value = operations.diagnostic_state(operations.read(path))
        if tracing.valid(value.get('id')): result['current_operation'] = safe_record(value, 'system', api)
    except (OSError, ValueError, TypeError): result['current_operation'] = {'state': 'unknown'}
    return result


def operation_inventory(api, query):
    result = reports.report('operation_inventory')
    rows, coverage = records(api, scan_limit=2000)
    selected, seen = [], set()
    for source, value in rows:
        identity = value.get('id')
        if query.source and source != query.source: continue
        if not tracing.valid(identity) or identity in seen: continue
        seen.add(identity)
        action = value.get('action') if isinstance(value.get('action'), str) else ''
        if not query.include_checks and not query.action and action in operation_outcome.CHECKS: continue
        if query.action and query.action not in str(action): continue
        at = reports.epoch(value.get('started_at') or value.get('updated_at'))
        if at is not None and at < time.time() - query.minutes * 60: continue
        if query.outcome and operation_outcome.outcome(value) != query.outcome: continue
        item = safe_record(value, source, api)
        item['next_step'] = {'tool': 'operation_timeline', 'arguments': {'operation_id': identity}}
        selected.append((at or 0, item))
    selected.sort(key=lambda row: row[0], reverse=True)
    result['items'] = [row[1] for row in selected[query.offset:query.offset + query.limit]]
    more = len(selected) > query.offset + query.limit
    result['next_offset'] = query.offset + query.limit if more else None
    result['current_admission'] = admission_context(api)
    result['coverage'] = {'sources': coverage, 'scan_limit_per_source': 2000, 'matches_in_scan': len(selected),
                          'window_minutes': query.minutes,
                          'more_matching_records': more, 'include_checks': query.include_checks or bool(query.action),
                          'scope': 'retained_operation_records; successful_catalog_check_is_not_a_chat_test; parent_system_record_owns_final_result'}
    return result


def summary(api, query):
    result = reports.report('application_diagnostics')
    definitions = api.managed_services()
    if query.component and query.component not in definitions: raise HTTPException(422, 'Выберите component из vps_services')
    chosen = [query.component] if query.component else list(definitions)[:16]
    reports.observation(result, 'build', 'installed_build', {'commit': api.installed_build_commit(), 'channel': api.expected_application_branch()})
    reports.observation(result, 'versions', 'installed_local_metadata', versions(api))
    with observation_scope():
        prime_services([definitions[key]['unit'] for key in chosen], fallback=False)
        for key in chosen:
            value = api.observe_service(definitions[key]['unit'])
            reports.observation(result, 'service:' + key, 'systemd', {'component': key, **{
                field: value.get(field) for field in ('state', 'reason', 'service_type', 'runtime', 'execution', 'enabled')}},
                'unknown' if value.get('runtime', {}).get('state') == 'unknown' else 'observed')
            if value.get('runtime', {}).get('state', 'unknown') == 'unknown':
                result['missing_data'].append({'source': 'service:' + key, 'reason': 'runtime_unavailable_or_transitioning'})
    try:
        import component_checks
        health = {row['id']: row for row in component_checks.visible(api)}
        reports.observation(result, 'health', 'component_health_cache', {
            key: value for key, value in health.items() if not query.component or key == query.component})
        result['coverage']['health'] = 'cached; inspect checked_at; older_than_180_seconds_is_stale; no_new_active_checks'
        for key, value in health.items():
            if value.get('status') in ('unknown', 'pending'):
                result['missing_data'].append({'source': 'health:' + key, 'reason': 'no_fresh_completed_local_check'})
    except (OSError, ValueError, AttributeError): reports.unknown(result, 'health', 'cache_unavailable')
    try: reports.observation(result, 'integrations', 'configured_inventory', inventory(api))
    except (OSError, ValueError): reports.unknown(result, 'integrations', 'inventory_unavailable')
    try:
        history = observability.Store(api.DATA_DIR).read(query.minutes, errors=True, limit=8)
        reports.observation(result, 'errors', 'request_history', {'summary': history['summary'], 'groups': history['groups'][:8], 'events': history['events']})
        result['coverage']['http'] = history['storage_coverage']
        result['coverage']['http_policy_revision'] = history['settings']['revision']
    except (OSError, sqlite3.Error): reports.unknown(result, 'errors', 'storage_unavailable')
    operation_records, operation_coverage = records(api)
    pending = [safe_record(value, source) for source, value in operation_records if value.get('state') not in operations.TERMINAL | {'done'}
               and not (value.get('action') == 'integration-functional-probe' and value.get('state') == 'unknown')]
    reports.observation(result, 'pending_operations', 'operation_history', pending[:10])
    recent_functional = []
    settings_revision = control.read(api).get('revision')
    for source, value in operation_records:
        if value.get('action') != 'integration-functional-probe': continue
        at = reports.epoch(value.get('finished_at'))
        if at is None or at < time.time() - query.minutes * 60: continue
        health = operation_log.safe_health(value.get('health_check', {}))
        if value.get('configuration_revision') != settings_revision:
            health = {'state': 'unknown', 'checks': [], 'reason': 'configuration_changed_since_measurement'}
        recent_functional.append({'operation_id': value['id'], 'checked_at': at, 'health_check': health,
                                  'scope': value.get('functional_scope', 'legacy_unknown_scope')})
    reports.observation(result, 'functional_checks', 'confirmed_operation_history', recent_functional[:5])
    result['coverage']['operations'] = {'scan_limit_per_source': 200, 'truncated': len(pending) > 10,
                                       'sources': operation_coverage,
                                       'scope': 'all_components; operation_target_may_not_be_recorded; recorded_state_not_live_worker_reconciliation'}
    for source, coverage in operation_coverage.items():
        if not coverage.get('available') or coverage.get('unreadable') or coverage.get('scan_truncated'):
            result['missing_data'].append({'source': source + '_operations', 'reason': 'partial_or_unavailable_history'})
    try:
        resources = api.system_resources()
        measured = {key: resources.get(key) for key in ('cpu_percent', 'uptime_s', 'observations')}
        for group in ('memory', 'disk'):
            total, available = resources.get(group + '_total'), resources.get(group + '_available')
            measured[group + '_used_percent'] = round(100 * (total - available) / total, 2) if type(total) in (int, float) and total > 0 and type(available) in (int, float) and 0 <= available <= total else None
        reports.observation(result, 'resources', 'system_resources', measured)
    except (OSError, ValueError, AttributeError): reports.unknown(result, 'resources', 'measurement_unavailable')
    result['coverage'].update(mode='passive', service_inventory_truncated=not query.component and len(definitions) > 16,
                              component_filter_scope='services_and_cached_health_only')
    result['missing_data'].append({'source': 'functional_health', 'reason': 'not_checked; process_or_local_health_does_not_prove_external_function'})
    result['next_steps'] = [{'tool': 'vps_incident', 'arguments': {'minutes': query.minutes}, 'reason': 'investigate_retained_errors'}]
    result['next_steps'].append({'tool': 'operation_inventory', 'arguments': {'minutes': query.minutes}, 'reason': 'discover_operation_IDs_results_and_recovery_context_before_timeline'})
    result['next_steps'] += [{'tool': 'operation_timeline', 'arguments': {'operation_id': row['operation_id']},
                             'reason': 'inspect_pending_operation'} for row in pending[:3]]
    return result


_probe_lock = threading.Lock()
_probe_recent = {}


def integration_probe(api, query, actor='panel', *, confirmed=False):
    settings = control.read(api)
    kind, identity = query.integration_id.split(':', 1)
    if kind == 'dns':
        if query.mode != 'dns': raise HTTPException(422, 'DNS-интеграция поддерживает только mode=dns')
        import diagnostic_probes
        result = diagnostic_probes.probe(api, diagnostic_probes.ProbeQuery(kind='dns', provider_id=identity), actor)
        result['tool'] = 'integration_probe'
        result['coverage'].update(integration_id=query.integration_id, mode=query.mode)
        return result
    provider = next((row for row in control.providers(settings) if row['id'] == identity), None)
    if not provider: raise HTTPException(404, 'Настроенная интеграция не найдена')
    if query.mode == 'functional' and not confirmed:
        import diagnostic_execution
        return diagnostic_execution.prepare(api, query, actor)
    if not _probe_lock.acquire(blocking=False): raise HTTPException(429, 'Проверка интеграции уже выполняется')
    try:
        now = time.monotonic()
        if now - _probe_recent.get(str(actor), -100) < 3: raise HTTPException(429, 'Повторите проверку через три секунды')
        _probe_recent[str(actor)] = now
        while len(_probe_recent) > 256: del _probe_recent[next(iter(_probe_recent))]
        return measure_provider(provider, query.mode, model_id=query.model_id)
    finally: _probe_lock.release()


def measure_provider(provider, mode, *, model_id=None):
    """Public-only pinned TLS. No redirects, generated tokens, request/response bodies in evidence."""
    from assistant_provider import base_url
    result = reports.report('integration_probe')
    result['coverage'] = {'integration_id': 'ai:' + provider['id'], 'mode': mode, 'vantage_point': 'vps',
                          'functional_operation': 'not_checked', 'api_test': 'GET_model_catalog; generation_only_in_confirmed_functional_mode', 'configuration_changed': False,
                          'total_network_budget_s': 12}
    stages = {key: {'state': 'not_checked'} for key in ('dns', 'tcp', 'tls', 'http', 'catalog')}
    reports.observation(result, 'integration', 'active_configured_provider_probe', stages)
    stream = secure = connection = None
    timed_out = threading.Event()
    collected_since = time.monotonic()
    def expire():
        timed_out.set()
        target = secure if secure is not None else stream
        if target is not None:
            try: target.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            target.close()
    deadline = threading.Timer(12, expire)
    deadline.daemon = True
    deadline.start()
    stage = 'dns'
    started = time.monotonic()
    try:
        parsed = urllib.parse.urlsplit(base_url(provider.get('provider_url', '')))
        host = parsed.hostname
        try: addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            raw = subprocess.run(['getent', 'ahosts', host], capture_output=True, text=True, timeout=3, check=False)
            addresses = list(dict.fromkeys(line.split()[0] for line in raw.stdout.splitlines()[:64] if line.split()))
        if not addresses: raise socket.gaierror()
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            stages['dns'] = {'state': 'failed', 'error_code': 'non_public_destination'}
            return result
        stages['dns'] = {'state': 'passed', 'duration_ms': round((time.monotonic() - started) * 1000, 2)}
        if mode == 'dns': return result
        stage, started = 'tcp', time.monotonic()
        stream = socket.create_connection((addresses[0], 443), timeout=3)
        stages['tcp'] = {'state': 'passed', 'duration_ms': round((time.monotonic() - started) * 1000, 2)}
        stage, started = 'tls', time.monotonic()
        stream.settimeout(3)
        secure = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
        stages['tls'] = {'state': 'passed', 'certificate_verified': True, 'duration_ms': round((time.monotonic() - started) * 1000, 2)}
        if mode == 'tls': return result
        stage, started = 'http', time.monotonic()
        if not provider.get('api_key'):
            stages['http'] = {'state': 'skipped', 'error_code': 'credential_not_configured'}
            return result
        connection = http.client.HTTPSConnection(host, timeout=3)
        connection.sock = secure
        connection.request('GET', parsed.path.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + provider['api_key'], 'Accept': 'application/json'})
        response = connection.getresponse()
        stages['http'] = {'state': 'passed' if response.status == 200 else 'failed', 'status': response.status,
                          'duration_ms': round((time.monotonic() - started) * 1000, 2),
                          'error_code': None if response.status == 200 else 'authentication_failed' if response.status in (401, 403) else 'rate_limited' if response.status == 429 else 'upstream_http_error'}
        if response.status == 200:
            stage = 'catalog'
            started = time.monotonic()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024: raise ValueError('catalog_size')
            data = json.loads(raw)
            from assistant_provider import model_id as valid_model_id
            count = sum(1 for row in data.get('data', []) if isinstance(row, dict) and valid_model_id(row.get('id'))) if isinstance(data, dict) and isinstance(data.get('data'), list) else 0
            stages['catalog'] = {'state': 'passed' if count else 'failed', 'models_available': min(count, 10000),
                                 'duration_ms': round((time.monotonic() - started) * 1000, 2),
                                 'error_code': None if count else 'invalid_catalog'}
            if mode == 'functional' and count:
                selected = model_id or provider.get('model')
                available = {row.get('id') for row in data['data'] if isinstance(row, dict)}
                if not selected or selected not in available:
                    stages['generation'] = {'state': 'not_checked', 'error_code': 'select_available_model'}
                    result['missing_data'].append({'source': 'model', 'reason': 'select_model_from_configured_provider_catalog'})
                    return result
                # A new pinned TLS connection is intentional: the catalog server may close the first.
                connection.close()
                connection = secure = None
                stage = 'generation_connection'
                if timed_out.is_set(): raise TimeoutError()
                stream = socket.create_connection((addresses[0], 443), timeout=3)
                stream.settimeout(3)
                secure = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
                if timed_out.is_set(): raise TimeoutError()
                connection = http.client.HTTPSConnection(host, timeout=3)
                connection.sock = secure
                stage, started = 'generation', time.monotonic()
                body = json.dumps({'model': selected, 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                                   'max_completion_tokens': 1, 'stream': False})
                connection.request('POST', parsed.path.rstrip('/') + '/chat/completions', body=body,
                    headers={'Authorization': 'Bearer ' + provider['api_key'], 'Content-Type': 'application/json'})
                response = connection.getresponse()
                raw = response.read(65537)
                valid = False
                if response.status == 200 and len(raw) <= 65536:
                    answer = json.loads(raw)
                    valid = isinstance(answer, dict) and isinstance(answer.get('choices'), list) and bool(answer['choices'])
                stages['generation'] = {'state': 'passed' if valid else 'failed', 'status': response.status,
                    'duration_ms': round((time.monotonic() - started) * 1000, 2), 'max_completion_tokens': 1,
                    'error_code': None if valid else 'generation_contract_failed'}
                result['coverage']['functional_operation'] = 'minimal_chat_completion_without_tools_or_user_content'
    except (OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError) as exc:
        code = 'timeout' if timed_out.is_set() or isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'tls_certificate_invalid' if isinstance(exc, ssl.SSLCertVerificationError) else 'dns_resolution_failed' if isinstance(exc, socket.gaierror) else stage + '_failed'
        stages[stage] = {'state': 'failed', 'error_code': code}
    finally:
        deadline.cancel()
        result['coverage']['duration_ms'] = round((time.monotonic() - collected_since) * 1000, 2)
        if connection: connection.close()
        elif secure: secure.close()
        elif stream: stream.close()
        for name, item in stages.items():
            if item['state'] in ('passed', 'failed'):
                result['confirmed'].append({'claim': name + '_' + item['state'], 'evidence': ['integration'], 'scope': 'configured_endpoint_at_measurement_time'})
        if mode != 'functional' or stages.get('generation', {}).get('state') != 'passed':
            result['missing_data'].append({'source': 'application_function', 'reason': 'chat_completion_not_verified'})
    return result


def call(api, name, arguments, actor):
    import diagnostic_history
    import assistant_roles as roles
    import diagnostic_checks
    import diagnostic_rules
    from pydantic import ValidationError
    if name == 'diagnostic_alert_rules': return diagnostic_rules.call(api, arguments, actor)
    settings = control.read(api)
    for permission in ('diagnostics', 'server_read', 'logs_read'):
        roles.require(settings, actor, permission)
    if name == 'integration_probe': roles.require(settings, actor, 'network_read')
    if arguments.get('method', 'GET') != 'GET': raise HTTPException(422, 'Диагностика не изменяет настройки')
    models = {'application_diagnostics': SummaryQuery, 'operation_timeline': TimelineQuery, 'operation_inventory': OperationsQuery,
              'integration_probe': IntegrationQuery, 'diagnostic_baseline': diagnostic_history.BaselineQuery,
              'diagnostic_alerts': diagnostic_history.HistoryQuery, 'component_check': diagnostic_checks.CheckQuery}
    try: query = models[name].model_validate(arguments.get('body') or {})
    except ValidationError: raise HTTPException(422, 'Некорректные параметры диагностики') from None
    if name == 'component_check':
        if query.level in ('dependency', 'functional'): roles.require(settings, actor, 'network_read')
        value = diagnostic_checks.check(api, query, actor)
        if not value.get('confirmation_required'):
            try: value['recording'] = {'probe_id': observability.Store(api.DATA_DIR).record_probe(value)}
            except (OSError, ValueError, sqlite3.Error): value['recording'] = {'stored': False}
        return value
    if name in ('diagnostic_baseline', 'diagnostic_alerts'):
        try: return diagnostic_history.baseline(api) if name == 'diagnostic_baseline' else diagnostic_history.alerts(api, query)
        except (OSError, ValueError):
            value = reports.report(name)
            reports.unknown(value, 'diagnostic_history', 'storage_unavailable')
            return value
    if name == 'integration_probe':
        value = integration_probe(api, query, actor)
        try:
            identity = observability.Store(api.DATA_DIR).record_probe(value)
            value['recording'] = {'stored': identity is not None, 'probe_id': identity}
        except (OSError, ValueError, sqlite3.Error): value['recording'] = {'stored': False, 'reason': 'probe_storage_unavailable'}
        return value
    if name == 'operation_inventory': return operation_inventory(api, query)
    return summary(api, query) if name == 'application_diagnostics' else timeline(api, query)
