"""Local diagnostic baseline and observed alert transitions, never automatic remediation."""
import math
import re
import time

from pydantic import BaseModel, Field

import application_diagnostics as diagnostics
import application_operation as operations
import assistant_control as control
import diagnostic_tools as reports
import diagnostic_rules
import diagnostic_configuration

RULES = {'service_error': {'condition': 'systemd runtime=error or enabled service stopped', 'trigger_samples': 2, 'recovery_samples': 2},
         'local_health': {'condition': 'fresh local component health=critical', 'trigger_samples': 2, 'recovery_samples': 2},
         'disk_pressure': {'condition': 'root disk used >=95%', 'trigger_samples': 2, 'recovery_samples': 2},
         'http_error_rate': {'condition': 'HTTP errors >=10% with >=20 retained requests in 5 minutes; complete request coverage required', 'trigger_samples': 2, 'recovery_samples': 2}}
BASELINE_POLICY = 'local_v2: all observed services running/applied/succeeded/listening/waiting; fresh installed component health healthy; complete configured HTTP window with requests and zero errors; disk<95%; no unfinished operations. Functional measurements have separate scope and configuration binding.'


def rule_definitions(policy):
    definitions = {key: {**value, 'enabled': policy[key], 'trigger_samples': policy['trigger_samples'], 'recovery_samples': policy['recovery_samples']} for key, value in RULES.items()}
    definitions['disk_pressure']['condition'] = 'root disk used >= ' + str(policy['disk_percent']) + '%'
    definitions['http_error_rate']['condition'] = 'HTTP errors >= ' + str(policy['http_error_percent']) + '%; requests >= ' + str(policy['http_min_requests']) + '; window=' + str(policy['window_minutes']) + 'min; complete coverage required'
    return definitions


class HistoryQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    limit: int = Field(default=30, strict=True, ge=1, le=100)


class BaselineQuery(BaseModel):
    model_config = {'extra': 'forbid'}


def path(api): return api.DATA_DIR / 'diagnostic-history.json'


def load(api):
    target = path(api)
    if target.is_symlink() or target.exists() and target.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid diagnostic history')
    value = operations.read(target)
    if value and (value.get('schema_version') != 1 or not isinstance(value.get('alerts'), dict) or not isinstance(value.get('history'), list)):
        raise ValueError('Invalid diagnostic history')
    if value:
        if type(value.get('dropped')) is not int or value['dropped'] < 0:
            raise ValueError('Invalid diagnostic loss counter')
        for alert in value['alerts'].values():
            if (not isinstance(alert, dict) or type(alert.get('active')) is not bool or alert.get('rule') not in RULES
                    or alert['active'] and type(alert.get('observed_since')) not in (int, float)):
                raise ValueError('Invalid diagnostic alert')
        if any(not isinstance(row, dict) or type(row.get('at')) not in (int, float) for row in value['history']):
            raise ValueError('Invalid diagnostic transition')
        if value.get('baseline') is not None and (not isinstance(value['baseline'], dict) or type(value['baseline'].get('checked_at')) not in (int, float)):
            raise ValueError('Invalid diagnostic baseline')
    return value or {'schema_version': 1, 'alerts': {}, 'history': [], 'dropped': 0}


def sample(api):
    policy = diagnostic_rules.read(api)
    report = diagnostics.summary(api, diagnostics.SummaryQuery(minutes=policy['window_minutes']))
    observations = {item['id']: item for item in report['observations']}
    service_items = {key[8:]: row['data'] for key, row in observations.items() if key.startswith('service:')}
    health = observations.get('health', {}).get('data') or {}
    resources = observations.get('resources', {}).get('data') or {}
    coverage = report['coverage'].get('http', {})
    stats = observations.get('errors', {}).get('data', {}).get('summary') or {}
    complete = bool(coverage.get('successful_requests_recorded') and coverage.get('first_at') is not None
                    and coverage['first_at'] <= coverage.get('requested_from', 0)
                    and coverage.get('tracked_since', math.inf) <= coverage.get('requested_from', 0)
                    and coverage.get('coverage_since', math.inf) <= coverage.get('requested_from', 0))
    requests, errors = stats.get('requests', 0), stats.get('errors', 0)
    conditions = {}
    service_states = {}
    for key, value in service_items.items():
        state = (value.get('execution') or {}).get('state') or value.get('runtime', {}).get('state', 'unknown')
        execution = value.get('execution') or {}
        service_states[key] = {'state': state, 'service_type': value.get('service_type'),
            'last_started_at': execution.get('last_started_at'), 'last_finished_at': execution.get('last_finished_at'),
            'last_success_at': execution.get('last_success_at'), 'last_error': execution.get('last_error')}
        conditions['service:' + key] = {'rule': 'service_error', 'component': key,
            'failing': True if state == 'error' or state == 'stopped' and value.get('enabled') is True else False if state in ('running', 'applied', 'succeeded', 'listening', 'waiting') else None}
    for key, value in health.items():
        at = reports.epoch(value.get('checked_at'))
        fresh = at is not None and 0 <= time.time() - at <= 180
        conditions['health:' + key] = {'rule': 'local_health', 'component': key,
            'failing': value.get('status') == 'critical' if fresh and value.get('status') in ('critical', 'healthy') else None}
    disk = resources.get('disk_used_percent')
    conditions['disk'] = {'rule': 'disk_pressure', 'component': 'system', 'failing': disk >= policy['disk_percent'] if type(disk) in (int, float) else None}
    conditions['http'] = {'rule': 'http_error_rate', 'component': 'application',
        'failing': errors / requests * 100 >= policy['http_error_percent'] if complete and requests >= policy['http_min_requests'] else None}
    settings = control.read(api)
    revision = settings.get('revision')
    configuration = diagnostic_configuration.inventory(api)
    snapshot = {'checked_at': time.time(), 'build': observations.get('build', {}).get('data'),
        'component_versions': observations.get('versions', {}).get('data'), 'services': service_states,
        'configuration_revisions': {'assistant': revision if isinstance(revision, str) and re.fullmatch('[0-9a-f]{32}', revision) else None, 'diagnostic_rules': policy['revision'],
                                    'HTTP_recording_policy': report['coverage'].get('http_policy_revision'),
                                    **{key: value['revision'] for key, value in configuration.items()}},
        'configuration_coverage': configuration,
        'functional_checks': observations.get('functional_checks', {}).get('data') or [],
        'local_health': {key: value.get('status') for key, value in health.items()},
        'http': {'window_minutes': policy['window_minutes'], 'requests': requests, 'errors': errors, 'error_rate': errors / requests if complete and requests else None,
                 'complete_coverage': complete}, 'disk_used_percent': disk}
    eligible = bool(service_states and observations.get('health', {}).get('state', 'observed') == 'observed' and 'health' in observations and complete and requests and not errors
        and all(value['state'] in ('running', 'applied', 'succeeded', 'listening', 'waiting') for value in service_states.values())
        and all(conditions['health:' + key]['failing'] is False for key in health)
        and type(disk) in (int, float) and disk < 95
        and observations.get('pending_operations', {}).get('data') == []
        and all(value.get('available') and not value.get('scan_truncated') and not value.get('unreadable') for value in report['coverage']['operations']['sources'].values())
        and not report['coverage']['operations']['truncated'] and not report['coverage']['service_inventory_truncated'])
    eligible = eligible and all(not value['unavailable'] and not value['truncated'] for value in configuration.values())
    for condition in conditions.values():
        if not policy[condition['rule']]: condition['failing'] = None
    return snapshot, conditions, eligible


def observe(api):
    snapshot, conditions, eligible = sample(api)
    now = snapshot['checked_at']
    with operations.locked(api.DATA_DIR):
        policy = diagnostic_rules.read(api)
        if snapshot.get('configuration_revisions', {}).get('diagnostic_rules', policy['revision']) != policy['revision']: return
        data = load(api)
        previous_at = data.get('checked_at', 0)
        # One observed minute per sample; gaps reset consecutive evidence.
        if 0 <= now - previous_at < 45: return
        contiguous = 45 <= now - previous_at <= 150 and data.get('rules_revision', policy['revision']) == policy['revision']
        for identity in list(data['alerts'].keys() - conditions.keys()):
            if data['alerts'][identity].get('active'):
                conditions[identity] = {'rule': data['alerts'][identity]['rule'], 'component': data['alerts'][identity]['component'], 'failing': None}
            else:
                del data['alerts'][identity]
        for identity, condition in conditions.items():
            alert = data['alerts'].setdefault(identity, {'id': identity, 'rule': condition['rule'], 'component': condition['component'], 'active': False})
            failing = condition['failing']
            previous = alert.get('measurement')
            alert['checked_at'] = now
            alert['measurement'] = 'unknown' if failing is None else 'failing' if failing else 'passed'
            same = contiguous and previous == alert['measurement']
            alert['consecutive_samples'] = alert.get('consecutive_samples', 0) + 1 if same and failing is not None else 1 if failing is not None else 0
            if not same: alert['candidate_since'] = now
            alert['enabled'] = policy[condition['rule']]
            alert['current_condition'] = rule_definitions(policy)[condition['rule']]['condition']
            if failing is True and not alert['active'] and alert['consecutive_samples'] >= policy['trigger_samples']:
                alert.update(active=True, opened_at=now, observed_since=alert['candidate_since'], fired_rules_revision=policy['revision'], fired_condition=alert['current_condition'])
                data['history'].append({'id': identity, 'rule': condition['rule'], 'component': condition['component'], 'transition': 'firing', 'at': now,
                                        'rules_revision': policy['revision'], 'condition': alert['current_condition']})
            elif failing is False and alert['active'] and alert['consecutive_samples'] >= policy['recovery_samples']:
                alert.update(active=False, recovered_at=now)
                data['history'].append({'id': identity, 'rule': condition['rule'], 'component': condition['component'], 'transition': 'recovered', 'at': now,
                                        'observed_duration_s': max(0, now - alert['observed_since']), 'rules_revision': policy['revision'], 'condition': alert['current_condition']})
        if eligible:
            data['baseline'] = snapshot
        data['checked_at'] = now
        data['rules_revision'] = policy['revision']
        service_history = data.setdefault('service_history', {})
        for component, observed in snapshot['services'].items():
            history = service_history.setdefault(component, {'observed_since': now})
            history['checked_at'] = now
            if observed.get('state') in ('running', 'applied', 'succeeded') and observed.get('last_started_at'):
                history['last_successful_start'] = observed['last_started_at']
            if observed.get('last_success_at'): history['last_successful_completion'] = observed['last_success_at']
            if observed.get('last_error'):
                history['last_error'] = {'reason': observed['last_error'], 'at': observed.get('last_finished_at'), 'observed_at': now}
        data['current'] = snapshot
        history = [item for item in data['history'] if item['at'] >= now - 30 * 86400][-500:]
        data['dropped'] += len(data['history']) - len(history)
        data['history'] = history
        # Keep active alerts when components disappear; absence cannot prove recovery.
        operations.atomic_json(path(api), data)


def baseline(api):
    result = reports.report('diagnostic_baseline')
    current, _, eligible = sample(api)
    reports.observation(result, 'current', 'passive_local_snapshot', current)
    result['coverage'] = {'policy': BASELINE_POLICY, 'current_eligible': eligible, 'causation': 'not_proven',
                          'config_scope': 'application_environment_DNS_network_security_Mihomo_relay_gateway_assistant; metadata_generation_not_semantic_change; no_values_or_secret_hashes', 'functional_health': 'separate_measurements'}
    try: stored = load(api)
    except (OSError, ValueError):
        reports.unknown(result, 'baseline', 'storage_unavailable')
        return result
    baseline = stored.get('baseline')
    if baseline:
        reports.observation(result, 'baseline', 'diagnostic_monitor', baseline, at=baseline['checked_at'])
        fields = ('build', 'component_versions', 'services', 'configuration_revisions', 'local_health', 'functional_checks')
        delta = reports.differences({key: baseline.get(key) for key in fields}, {key: current.get(key) for key in fields})
        reports.observation(result, 'differences', 'snapshot_comparison', delta[:100])
        result['coverage']['differences_truncated'] = len(delta) >= 60
        if delta: result['confirmed'].append({'claim': 'local_snapshot_fields_differ', 'evidence': ['current', 'baseline', 'differences']})
        if not baseline.get('http', {}).get('complete_coverage') or not current['http']['complete_coverage']:
            result['missing_data'].append({'source': 'error_rate_comparison', 'reason': 'incomplete_request_coverage'})
        elif baseline['http'].get('window_minutes') != current['http'].get('window_minutes'):
            result['missing_data'].append({'source': 'error_rate_comparison', 'reason': 'different_window_sizes'})
        else:
            left, right = baseline['http'].get('error_rate'), current['http'].get('error_rate')
            reports.observation(result, 'error_rate_comparison', 'retained_HTTP_counts', {
                'baseline': baseline['http'], 'current': current['http'],
                'difference': right - left if left is not None and right is not None else None})
    else: reports.unknown(result, 'baseline', 'no_snapshot_satisfies_local_policy_yet')
    return result


def service_history(api):
    try: return load(api).get('service_history', {})
    except (OSError, ValueError): return {}


def alerts(api, query):
    result = reports.report('diagnostic_alerts')
    data = load(api)
    now = time.time()
    policy = diagnostic_rules.read(api)
    result['rules'] = {'definitions': rule_definitions(policy), 'settings': policy}
    result['active'] = [{**value, 'observed_duration_s': max(0, now - value['observed_since'])} for value in data['alerts'].values() if value.get('active')][:100]
    result['history'] = data['history'][-query.limit:]
    result['coverage'] = {'checked_at': data.get('checked_at'), 'stale': not 0 <= now - data.get('checked_at', 0) <= 180,
        'retention_days': 30, 'max_transitions': 500, 'dropped': data.get('dropped', 0), 'truncated': len(data['history']) > query.limit,
        'rule_changes': 'diagnostic_alert_rules_PATCH_requires_human_confirmation', 'scope': 'local_rules; unknown_disabled_or_missing_measurements_never_confirm_recovery'}
    if result['coverage']['stale']: result['missing_data'].append({'source': 'monitor', 'reason': 'no_recent_sample; empty_active_list_does_not_prove_health'})
    return result
