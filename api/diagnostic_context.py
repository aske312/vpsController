"""Dependency-free correlation across HTTP, threads and admitted workers."""
from contextvars import ContextVar
import hashlib
import hmac
import os
import re
import time
import uuid

current = ContextVar('vps_diagnostic_context', default=None)
ID = re.compile(r'^[0-9a-f]{32}$')
ERRORS = {
    'command_missing': ('execution', False), 'command_timeout': ('execution', True),
    'command_failed': ('execution', False), 'mihomo_unavailable': ('mihomo_api', True),
    'mihomo_rejected': ('mihomo_api', False), 'invalid_request': ('validation', False),
    'access_denied': ('authorization', False), 'not_found': ('lookup', False),
    'conflict': ('admission', False), 'rate_limited': ('admission', True),
    'server_error': ('handling', None), 'unhandled_exception': ('handling', None),
    'subscription_expired': ('subscription_limits', False), 'subscription_exhausted': ('subscription_limits', False),
    'subscription_signer_unavailable': ('subscription_signing', True),
    'subscription_invalid_nonce': ('subscription_validation', False),
    'subscription_unsupported_format': ('client_compatibility', False),
    'subscription_client_mismatch': ('device_binding', False), 'subscription_invalid_device': ('device_binding', False),
    'subscription_render_failed': ('config_generation', None), 'request_rejected': ('handling', False),
}


def valid(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


def snapshot():
    value = current.get() or {}
    trace = value.get('trace_id') or os.getenv('VPS_CONTROL_TRACE_ID')
    parent = value.get('request_id') or os.getenv('VPS_CONTROL_PARENT_REQUEST_ID')
    operation = value.get('operation_id') or os.getenv('VPS_CONTROL_OPERATION_ID')
    result = {key: entry for key, entry in {'trace_id': trace, 'parent_request_id': parent, 'operation_id': operation}.items() if valid(entry)}
    target = value.get('target_profile_id')
    if isinstance(target, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', target): result['target_profile_id'] = target
    return result


def incoming(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    trace, parent = headers.get('x-vps-trace-id', ''), headers.get('x-vps-parent-id', '')
    stamp, signature = headers.get('x-vps-trace-at', ''), headers.get('x-vps-trace-proof', '')
    key = os.getenv('ADMIN_PASSWORD', '')
    if key and valid(trace) and valid(parent) and re.fullmatch(r'[0-9]{1,12}', stamp) and re.fullmatch(r'[0-9a-f]{64}', signature) and abs(time.time() - int(stamp)) <= 120:
        expected = hmac.new(key.encode(), f'{trace}:{parent}:{stamp}'.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return {'trace_id': trace, 'parent_request_id': parent}
    return {'trace_id': uuid.uuid4().hex}


def outgoing():
    value = snapshot()
    key = os.getenv('ADMIN_PASSWORD', '')
    if not key or not valid(value.get('trace_id')) or not valid(value.get('parent_request_id')): return {}
    trace, parent, stamp = value['trace_id'], value['parent_request_id'], str(int(time.time()))
    return {'X-VPS-Trace-ID': trace, 'X-VPS-Parent-ID': parent, 'X-VPS-Trace-At': stamp,
            'X-VPS-Trace-Proof': hmac.new(key.encode(), f'{trace}:{parent}:{stamp}'.encode(), hashlib.sha256).hexdigest()}


def failure(code):
    """Mutate the request's shared context, including from FastAPI's thread pool."""
    value = current.get()
    if value is not None and code in ERRORS: value['error_code'] = code


def operation(identity):
    value = current.get()
    if value is not None and valid(identity): value['operation_id'] = identity


def details(code, component):
    stage, retryable = ERRORS.get(code, ('handling', None))
    return {'error_code': code, 'component': component, 'stage': stage, 'retryable': retryable}


CHANGE_FIELDS = {'settings', 'values', 'name', 'connections', 'channels', 'devices', 'routing', 'limits', 'enabled', 'active',
                 'persistent', 'retention_days', 'max_events', 'errors_only', 'mode', 'provider', 'provider_id', 'primary',
                 'fallback', 'resolvers', 'dns', 'mtu', 'keepalive', 'port', 'transport', 'domain', 'address', 'action',
                 'cdn_domain', 'tls_relay_domain', 'udp_relay_domain', 'cdn_domains', 'tls_relay_domains', 'udp_relay_domains',
                 'route_mode', 'channel_mode', 'outbound', 'rules', 'presets', 'traffic_gb', 'speed_mbps', 'days',
                 'reboot', 'cleanup', 'update', 'schedule', 'protocol', 'component', 'automatic_cleanup'}


def changed_names(value, prefix='', depth=0):
    if not isinstance(value, dict) or depth > 2: return []
    result = []
    for key, entry in value.items():
        if key not in CHANGE_FIELDS: continue
        name = prefix + key
        result.append(name)
        if key in ('settings', 'values', 'routing', 'limits'):
            result += changed_names(entry, name + '.', depth + 1)
    return sorted(set(result))[:32]
