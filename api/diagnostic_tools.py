"""Evidence-first diagnostic reports. No model commands or arbitrary destinations."""
from datetime import datetime
import re
import time
import sqlite3
import math
import ipaddress
import urllib.parse

from fastapi import HTTPException
from pydantic import BaseModel, Field
from pydantic import ValidationError

import application_operation as operations
import observability
import diagnostic_context as tracing
import observability_control
from service_state import observation_scope, prime_services


class ChangesQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    minutes: int = Field(default=60, strict=True, ge=1, le=129600)
    limit: int = Field(default=40, strict=True, ge=1, le=100)
    trace_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    operation_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    profile_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')


def report(tool):
    return {'schema_version': 1, 'tool': tool, 'checked_at': time.time(), 'location': 'vps',
            'observations': [], 'confirmed': [], 'hypotheses': [], 'missing_data': [], 'next_steps': [], 'coverage': {}}


def observation(result, identity, source, data, state='observed', at=None):
    result['observations'].append({'id': identity, 'source': source, 'checked_at': time.time() if at is None else at,
                                   'location': 'vps', 'state': state, 'data': data})


def unknown(result, source, reason):
    observation(result, source, source, None, 'unknown')
    result['missing_data'].append({'source': source, 'reason': reason})


def epoch(value):
    try: return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError): return None


def changes(api, query):
    result = report('vps_changes')
    history = observability.Store(api.DATA_DIR).read(query.minutes, limit=query.limit, trace_id=query.trace_id,
                                                    operation_id=query.operation_id, profile_id=query.profile_id, changes=True)
    for event in history['events']:
        observation(result, 'http:' + str(event['id']), 'request_history', {
            key: event.get(key) for key in ('request_id', 'trace_id', 'operation_id', 'service', 'route', 'method', 'status', 'changed_fields', 'target_profile_id')}, at=event['at'])
    seen = set()
    for root, source in [(api.DATA_DIR, 'system'), (api.DATA_DIR / 'mihomo', 'mihomo'), (api.DATA_DIR / 'cdn', 'cdn')]:
        try: records = operations.history(root, limit=200)
        except (OSError, ValueError):
            unknown(result, source + '_operations', 'operation_history_unavailable')
            continue
        for record in records:
            identity = record.get('id')
            if not tracing.valid(identity) or identity in seen: continue
            at = epoch(record.get('updated_at') or record.get('started_at'))
            if at is None or at < time.time() - query.minutes * 60: continue
            if query.trace_id and record.get('trace_id') != query.trace_id: continue
            if query.operation_id and identity != query.operation_id: continue
            if query.profile_id and record.get('target_profile_id') != query.profile_id: continue
            seen.add(identity)
            action = str(record.get('action', '')).split(':', 1)[0]
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', action): action = 'unknown'
            state = record.get('state')
            if state not in ('queued', 'running', 'succeeded', 'failed', 'unknown', 'cancelled'): state = 'unknown'
            observation(result, 'operation:' + identity, 'operation_history', {
                'operation_id': identity, 'action': action, 'state': state, 'source': source,
                **{key: record[key] for key in ('trace_id', 'parent_request_id') if tracing.valid(record.get(key))},
                'target_profile_id': record.get('target_profile_id') if isinstance(record.get('target_profile_id'), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', record['target_profile_id']) else None,
            }, at=at)
    result['observations'].sort(key=lambda item: item['checked_at'], reverse=True)
    total = len(result['observations'])
    result['observations'] = result['observations'][:query.limit]
    result['coverage'] = {'window_minutes': query.minutes, 'limit': query.limit, 'truncated': total > query.limit or len(history['events']) == query.limit,
                          'operation_scan_limit_per_source': 200, 'settings': history['settings'],
                          'semantics': 'HTTP 202 is admission, not completion; changed_fields are requested field names, never values. Operation history owns final results.'}
    result['next_steps'] = [{'tool': 'vps_incident', 'arguments': {'minutes': query.minutes}, 'reason': 'correlate_changes_with_errors_without_assuming_causation'}]
    return result


class IncidentQuery(ChangesQuery):
    request_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    profile_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    include_connections: bool = True
    include_journal: bool = False
    journal_service: str = Field(default='application', max_length=80)
    services: list[str] = Field(default_factory=list, max_length=8)


def incident(api, query):
    result = report('vps_incident')
    trace = query.trace_id
    history = None
    try:
        if query.request_id:
            seed = observability.Store(api.DATA_DIR).read(query.minutes, identity=query.request_id, limit=1)['events']
            if seed and seed[0].get('trace_id'):
                if trace and trace != seed[0]['trace_id']: raise HTTPException(422, 'request_id и trace_id относятся к разным цепочкам')
                trace = seed[0]['trace_id']
            elif not seed: unknown(result, 'request_id', 'request_not_in_retained_window')
        history = observability.Store(api.DATA_DIR).read(query.minutes, identity=query.request_id if not trace else None,
                    limit=query.limit, trace_id=trace, operation_id=query.operation_id, profile_id=query.profile_id)
        observation(result, 'http_events', 'request_history', {key: history[key] for key in ('summary', 'subscriptions', 'groups', 'events')})
        if history['summary']['errors']:
            result['confirmed'].append({'claim': 'HTTP_errors_in_retained_window', 'evidence': ['http_events'],
                                        'count': history['summary']['errors'], 'affected_known_profiles': history['summary']['affected_profiles']})
        result['coverage'].update(settings=history['settings'], events_truncated=history['summary']['requests'] > len(history['events']))
    except (OSError, sqlite3.Error): unknown(result, 'request_history', 'storage_unavailable')
    try:
        change_report = changes(api, ChangesQuery(minutes=query.minutes, limit=query.limit, trace_id=trace, operation_id=query.operation_id, profile_id=query.profile_id))
        observation(result, 'changes', 'change_history', change_report['observations'])
        result['coverage']['changes'] = change_report['coverage']
        first = history['summary']['first_error_at'] if history else None
        near = [item['id'] for item in change_report['observations'] if first is not None and first - 900 <= item['checked_at'] <= first]
        if near:
            result['hypotheses'].append({'claim': 'preceding_change_may_be_related', 'evidence': ['http_events', 'changes'],
                                         'change_ids': near[:10], 'causation': 'not_proven'})
    except (OSError, sqlite3.Error): unknown(result, 'change_history', 'storage_unavailable')
    definitions = api.managed_services()
    chosen = list(dict.fromkeys(query.services or [key for key in ('api', 'web', 'gateway', 'mihomo') if key in definitions]))
    if any(key not in definitions for key in chosen): raise HTTPException(422, 'Выберите службы из vps_services')
    with observation_scope():
        prime_services([definitions[key]['unit'] for key in chosen])
        for key in chosen:
            value = api.observe_service(definitions[key]['unit'])
            state = value.get('runtime', {}).get('state', 'unknown')
            if state not in ('running', 'stopped', 'error', 'unknown', 'applied', 'waiting', 'listening'): state = 'unknown'
            observation(result, 'service:' + key, 'systemd', {'service': key, 'runtime_state': state, 'scope': 'current_runtime'},
                        'unknown' if state == 'unknown' else 'observed')
            if state in ('error', 'stopped'):
                result['confirmed'].append({'claim': 'service_currently_' + state, 'evidence': ['service:' + key], 'scope': 'current_runtime_only'})
    if query.include_connections:
        value = observability_control.connections(api)
        observation(result, 'connections', 'mihomo_transports', value, 'observed' if value['available'] else 'unknown')
        if not value['available'] or value.get('established_connections') is None:
            result['missing_data'].append({'source': 'connections', 'reason': 'complete_session_count_unavailable'})
    if query.include_journal:
        try:
            value = observability_control.journal(api, query.journal_service, min(query.minutes, 10080), 'warning', 40)
            observation(result, 'journal', 'journald', value)
        except HTTPException as exc:
            if exc.status_code == 422: raise
            unknown(result, 'journal', 'journald_unavailable')
    result['coverage'].update(window_minutes=query.minutes, trace_id=trace, scope='retained_HTTP_evidence_and_current_server_observations',
                              onset='first_retained_error_is_not_proven_incident_start')
    result['missing_data'].append({'source': 'client_network', 'reason': 'no_client_side_measurement'})
    try:
        recent = observability.Store(api.DATA_DIR).probes(query.minutes, query.profile_id)
        observation(result, 'recent_probes', 'probe_history', recent)
        result['coverage']['probe_history'] = 'up_to_5_recent_measurements; temporal_context_not_automatic_trace_or_cause_match'
    except (OSError, sqlite3.Error, ValueError): unknown(result, 'probe_history', 'storage_unavailable')
    result['next_steps'] += [{'tool': 'vps_probe', 'arguments': {'kind': 'mihomo_api'}, 'reason': 'test_manager_reachability'},
                             {'tool': 'vps_probe', 'arguments': {'kind': 'targets'}, 'reason': 'discover_configured_DNS_and_route_targets'}]
    return result


class CompareQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    left_profile_id: str = Field(pattern='^[a-zA-Z0-9_-]{1,64}$')
    right_profile_id: str = Field(pattern='^[a-zA-Z0-9_-]{1,64}$')
    left_connection_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,64}$')
    right_connection_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,64}$')


CONFIG_FIELDS = {'mtu', 'port', 'server_port', 'network', 'transport', 'route_mode', 'channel_mode', 'connection_mode',
                 'routing_scope', 'dns_mode', 'dns_provider', 'ipv6', 'tun_enabled', 'tun_force', 'block_ads',
                 'dns_fake_ip', 'dns_ipv6', 'dns_prefer_h3', 'dns_secure', 'dns_hijack_force',
                 'tunnel_privacy', 'traffic_gb', 'speed_mbps', 'days', 'keepalive', 'udp', 'multiplex', 'alpn'}


def safe_settings(value):
    if not isinstance(value, dict): return {}
    result = {}
    for key, entry in value.items():
        if key not in CONFIG_FIELDS: continue
        if entry is None or type(entry) is bool or (type(entry) in (int, float) and math.isfinite(entry) and abs(entry) <= 2**53): result[key] = entry
        elif isinstance(entry, str) and re.fullmatch(r'[a-zA-Z0-9_.:, -]{1,80}', entry): result[key] = entry
    return result


def profile_view(profile, connection_id):
    rows = profile.get('connections', [])
    if not isinstance(rows, list): raise ValueError('Invalid connections')
    if connection_id:
        rows = [row for row in rows if row.get('id') == connection_id]
        if not rows: raise HTTPException(404, 'Подключение профиля не найдено')
    channels = []
    for row in rows[:16]:
        component = row.get('component')
        if not isinstance(component, str) or not re.fullmatch(r'transport-[a-z0-9-]{1,40}', component): component = 'unknown'
        endpoints = {}
        for source in (row.get('credential'), row.get('settings')):
            if not isinstance(source, dict): continue
            for key in ('server', 'server_address', 'endpoint', 'cdn_domain', 'tls_domain', 'relay_address'):
                value = source.get(key)
                if not isinstance(value, str) or len(value) > 253 or any(character in value for character in ('/', '@', '?', '#', '\\')): continue
                try:
                    parsed = urllib.parse.urlsplit('//' + value)
                    host = parsed.hostname
                    if host and (re.fullmatch(r'[a-zA-Z0-9.-]+', host) or ipaddress.ip_address(host)):
                        endpoints[key] = value
                except ValueError: continue
            for key in ('port', 'server_port'):
                if type(source.get(key)) is int and 1 <= source[key] <= 65535: endpoints[key] = source[key]
        channels.append({'component': component, 'settings': safe_settings(row.get('settings')), 'endpoints': endpoints})
    versions = []
    for device in profile.get('devices', [])[:32]:
        item = {key: value for key in ('os', 'os_version', 'client_name', 'client_version')
                if isinstance(value := device.get(key), str) and re.fullmatch(r'[a-zA-Z0-9_.+ -]{1,80}', value)}
        if item and item not in versions: versions.append(item)
    return {'routing': safe_settings(profile.get('routing')), 'limits': safe_settings(profile.get('limits')),
            'connections': channels, 'clients': versions}, len(rows) > 16


def stats_view(value, connection_id):
    if not isinstance(value, dict): return None
    rows = value.get('connections')
    if not isinstance(rows, dict): return None
    if connection_id: rows = {key: row for key, row in rows.items() if key == connection_id}
    result = []
    for row in list(rows.values())[:16]:
        if not isinstance(row, dict): continue
        item = {}
        for key in ('active', 'activity_available', 'stats_available'):
            item[key] = row.get(key) if type(row.get(key)) is bool else None
        for key in ('active_connections', 'rx_bytes', 'tx_bytes', 'handshake_age_s', 'latency_ms'):
            number = row.get(key)
            item[key] = number if type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 2**53 else None
        if item['activity_available'] is False:
            for key in ('active', 'active_connections', 'handshake_age_s'): item[key] = None
        if item['stats_available'] is False:
            for key in ('rx_bytes', 'tx_bytes'): item[key] = None
        result.append(item)
    return result or None


def differences(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in sorted(set(left) | set(right)):
            result += differences(left.get(key), right.get(key), prefix + ('.' if prefix else '') + key)
        return result[:60]
    if left == right: return []
    return [{'field': prefix, 'left': left, 'right': right}]


def compare(api, query):
    import assistant_mihomo
    result = report('vps_compare')
    try:
        inventory = assistant_mihomo.request('GET', '/profiles', timeout=8).get('items')
        if not isinstance(inventory, list) or any(not isinstance(row, dict) for row in inventory): raise ValueError('Invalid inventory')
        selected = []
        for identity, connection in [(query.left_profile_id, query.left_connection_id), (query.right_profile_id, query.right_connection_id)]:
            profile = next((row for row in inventory if row.get('id') == identity), None)
            if profile is None: raise HTTPException(404, 'Профиль сравнения не найден')
            view, truncated = profile_view(profile, connection)
            selected.append((profile, connection, view, truncated))
    except (HTTPException, OSError, ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, HTTPException) and exc.status_code == 404: raise
        unknown(result, 'profile_inventory', 'mihomo_inventory_unavailable')
        return result
    stats = []
    try:
        value = assistant_mihomo.request('GET', '/stats', timeout=10)
        stats = value.get('items', [])
        if not isinstance(stats, list) or any(not isinstance(row, dict) for row in stats): raise ValueError('Invalid stats')
    except (HTTPException, OSError, ValueError, TypeError, AttributeError):
        stats = []
        unknown(result, 'transport_stats', 'mihomo_stats_unavailable')
    after = []
    try:
        after = assistant_mihomo.request('GET', '/profiles', timeout=8).get('items', [])
        if not isinstance(after, list) or any(not isinstance(row, dict) for row in after): after = []
    except (HTTPException, OSError, ValueError, TypeError, AttributeError): unknown(result, 'configuration_consistency', 'revision_recheck_unavailable')
    stable = True
    for side, (profile, connection, view, truncated) in zip(('left', 'right'), selected):
        latest = next((row for row in after if row.get('id') == profile['id']), {})
        revision = profile.get('revision')
        consistent = isinstance(revision, str) and bool(re.fullmatch('[0-9a-f]{64}', revision)) and latest.get('revision') == revision
        stable = stable and consistent
        observation(result, side + '_config', 'mihomo_profiles', view, 'observed' if consistent else 'unknown')
        observation(result, side + '_identity', 'selection', {'profile_id': profile['id'], 'connection_id': connection, 'configuration_unchanged': consistent})
        row = next((row for row in stats if row.get('id') == profile['id']), None)
        values = stats_view(row, connection) if consistent else None
        observation(result, side + '_telemetry', 'mihomo_transport_counters', values, 'observed' if values is not None else 'unknown')
        if values is None: result['missing_data'].append({'source': side + '_telemetry', 'reason': 'counters_missing_or_configuration_changed'})
        result['coverage'][side + '_truncated'] = truncated
    diff = differences(selected[0][2], selected[1][2])
    observation(result, 'differences', 'configuration_comparison', diff, 'observed' if stable else 'unknown')
    if diff and stable: result['confirmed'].append({'claim': 'selected_configuration_fields_differ', 'evidence': ['left_config', 'right_config', 'differences']})
    result['coverage'].update(configuration_unchanged=stable, scope='selected_safe_settings_and_server_telemetry',
                              equivalence='no_differences_does_not_prove_full_equivalence', client_network='not_measured')
    result['missing_data'].append({'source': 'per_client_network', 'reason': 'requires_measurement_from_device'})
    result['missing_data'].append({'source': 'inherited_global_DNS_policy', 'reason': 'comparison_contains_profile_DNS_overrides_only; inspect_mihomo_api_GET_/dns/settings_for_global_policy'})
    result['next_steps'] = [{'tool': 'vps_incident', 'arguments': {'profile_id': query.left_profile_id}, 'reason': 'correlate_profile_errors'},
                            {'tool': 'vps_probe', 'arguments': {'kind': 'targets'}, 'reason': 'select_DNS_provider_or_transport_target_before_running'}]
    return result


def call(api, name, arguments, actor='panel'):
    import assistant_control
    import assistant_roles as roles
    import diagnostic_probes
    settings = assistant_control.read(api)
    permissions = {
        'vps_changes': ('logs_read', 'server_read'),
        'vps_incident': ('diagnostics', 'logs_read', 'server_read'),
        'vps_probe': ('diagnostics', 'network_read', 'server_read'),
        'vps_compare': ('diagnostics', 'subscription_read', 'subscriptions_admin'),
    }
    if name not in permissions: raise HTTPException(404, 'Диагностический инструмент не найден')
    for permission in permissions[name]: roles.require(settings, actor, permission)
    if arguments.get('method', 'GET') != 'GET': raise HTTPException(422, 'Диагностические инструменты не изменяют настройки')
    models = {'vps_changes': ChangesQuery, 'vps_incident': IncidentQuery, 'vps_probe': diagnostic_probes.ProbeQuery, 'vps_compare': CompareQuery}
    try: query = models[name].model_validate(arguments.get('body') or {})
    except ValidationError: raise HTTPException(422, 'Некорректные параметры диагностического инструмента') from None
    if name == 'vps_probe':
        value = diagnostic_probes.probe(api, query, actor)
        if query.kind == 'targets': return value
        profile = None
        if query.consumer_id and re.fullmatch(r'mihomo:[a-zA-Z0-9_-]{1,64}:[a-zA-Z0-9_-]{1,64}', query.consumer_id):
            profile = query.consumer_id.split(':')[1]
        try:
            identity = observability.Store(api.DATA_DIR).record_probe(value, profile)
            value['recording'] = {'stored': identity is not None, 'probe_id': identity}
        except (OSError, sqlite3.Error, ValueError): value['recording'] = {'stored': False, 'reason': 'probe_storage_unavailable'}
        return value
    return {'vps_changes': changes, 'vps_incident': incident, 'vps_compare': compare}[name](api, query)
