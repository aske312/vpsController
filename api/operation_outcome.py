"""Interpret lifecycle and result without equating a successful step with completion."""
import re
ACTIVE = {'queued', 'running', 'active', 'activating', 'rebooting', 'powering-off'}
ERRORS = {'failed', 'failure', 'exit-code', 'timeout', 'process-failed', 'interrupted', 'reboot-timeout', 'signal', 'dependency'}
CHECKS = {'ai-catalog', 'runtime-check', 'integration-functional-probe'}


def outcome(value):
    state, result = value.get('state'), value.get('result')
    if not isinstance(state, str): state = ''
    if state == 'cancelled': return 'cancelled'
    if state == 'failed': return 'error'
    if state in ACTIVE: return 'active'
    if state == 'unknown': return 'unknown'
    if result is not None and not isinstance(result, str): return 'unknown'
    if result in ERRORS: return 'error'
    if state in {'succeeded', 'finished', 'done'}:
        if result == 'skipped': return 'skipped'
        return 'success' if result in (None, '', 'success', 'passed') else 'unknown'
    return 'unknown'


def context(value):
    state = outcome(value)
    health = value.get('health_check')
    scope = health.get('scope') if isinstance(health, dict) else None
    if not isinstance(scope, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,100}', scope): scope = None
    return {'outcome': state, 'kind': 'check' if isinstance(value.get('action'), str) and value['action'] in CHECKS else 'operation',
            'completion_confirmed': state in {'success', 'error', 'cancelled', 'skipped'},
            'functional_health': 'not_inferred_from_operation_result',
            'scope': scope}
