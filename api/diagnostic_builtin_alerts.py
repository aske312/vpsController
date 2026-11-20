"""Basic resource and Relay conditions. Only fixed, pinned read-only Agent RPCs."""
import math
import re
import time

import application_operation as operations
import alert_context


DEFINITIONS = {
    'cpu_pressure': 'CPU usage reaches configured percentage',
    'memory_pressure': 'RAM usage reaches configured percentage',
    'load_pressure': '5-minute system load per CPU reaches configured threshold',
    'application_recovery': 'an application or profile recovery snapshot requires intervention',
    'relay_unavailable': 'configured Relay Agent API cannot be reached or validated; forwarding remains unverified',
    'relay_route': 'an owned Relay route is missing, changed or failed',
    'relay_address': 'Agent reports an unresolved or mismatched configured client address',
    'connection_errors': 'fresh Relay traffic interval contains at least the configured number of errors',
}


def numeric(value, minimum=0, maximum=1e12):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def fresh(value, now):
    from diagnostic_tools import epoch
    at = value if numeric(value) else epoch(value)
    return at is not None and 0 <= now - at <= 180


def resources_conditions(resources, policy, now):
    result = {}
    observations = resources.get('observations') or {}
    def available(group):
        observed = observations.get(group)
        return observed is None or isinstance(observed, dict) and observed.get('available') is True and fresh(observed.get('checked_at'), now)
    for rule, field, threshold in (('cpu_pressure', 'cpu_percent', 'cpu_percent'), ('memory_pressure', 'memory_used_percent', 'memory_percent')):
        value = resources.get(field)
        group = 'cpu' if rule == 'cpu_pressure' else 'memory'
        valid = numeric(value, maximum=100) and available(group)
        result[rule] = {'rule': rule, 'component': 'system', 'failing': value >= policy[threshold] if valid else None,
                        'details': {'value': value if valid else None, 'threshold': policy[threshold]}}
    load, count = resources.get('load5'), resources.get('cpu_count')
    valid = numeric(load) and numeric(count, 1, 65536) and all(available(key) for key in ('load', 'cpu'))
    value = round(load / count, 3) if valid else None
    result['load_pressure'] = {'rule': 'load_pressure', 'component': 'system', 'failing': value >= policy['load_per_cpu'] if valid else None,
                               'details': {'value': value, 'threshold': policy['load_per_cpu']}}
    return result


def recovery_owner_active(api, marker):
    """A snapshot is expected only while its own worker is observably alive."""
    try:
        if marker.is_symlink() or marker.stat().st_size > 32 * 1024 * 1024: return None
        identity = operations.read(marker).get('operation_id')
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity): return False
        uncertain = False
        for root in (api.DATA_DIR, api.DATA_DIR / 'mihomo'):
            row = operations.read(root / 'operations' / (identity + '.json'))
            if row.get('id') != identity or row.get('state') not in ('queued', 'running', 'unknown'): continue
            if row.get('boot_id') and operations.boot_id() and row['boot_id'] != operations.boot_id(): continue
            observed = operations.worker_observation(row, api.observe_service)
            if observed.get('active') is True: return True
            if observed.get('unit_present') is not False and observed.get('state') not in ('inactive', 'failed'): uncertain = True
        return None if uncertain else False
    except (OSError, ValueError, AttributeError):
        return None


def recovery_condition(api):
    markers = ('recovery/application-update', 'mihomo/profile-recovery/manifest.json',
               'automation-recovery.json', 'dns-recovery.json', 'recovery/security-recovery.txt', 'relay/pending.json')
    try:
        pending = [name for name in markers if (api.DATA_DIR / name).exists() or (api.DATA_DIR / name).is_symlink()]
        profile_marker = 'mihomo/profile-recovery/manifest.json'
        if profile_marker in pending:
            owned = recovery_owner_active(api, api.DATA_DIR / profile_marker)
            if owned is False:
                return {'rule': 'application_recovery', 'component': 'application', 'failing': True, 'details': {'pending_recoveries': len(pending)}}
            if owned is not False and len(pending) == 1:
                return {'rule': 'application_recovery', 'component': 'application', 'failing': None, 'details': {}}
            if owned is True: pending.remove(profile_marker)
        target = api.DATA_DIR / 'application-action.json'
        if pending and (target.is_symlink() or target.exists() and target.stat().st_size > 128 * 1024): raise ValueError('Invalid operation state')
        current = operations.read(target) if pending else {}
        if pending and current.get('state') in ('queued', 'running'):
            # A live mutation legitimately owns snapshots. It does not prove recovery.
            return {'rule': 'application_recovery', 'component': 'application', 'failing': None, 'details': {}}
        return {'rule': 'application_recovery', 'component': 'application', 'failing': bool(pending),
                'details': {'pending_recoveries': len(pending)}}
    except (OSError, ValueError):
        return {'rule': 'application_recovery', 'component': 'application', 'failing': None, 'details': {}}


def relay_snapshot(api):
    from relay_client import RelayClient
    control = api.relay_control()
    # Per-request pinning and response validation are the same as in the network UI.
    control.client_factory = lambda url, token, pin: RelayClient(url, token, pin, timeout=2)
    return control.public()


def relay_conditions(value, policy, now):
    result = {}
    for host in value.get('hosts', [])[:8]:
        identity = host.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity): continue
        connected = host.get('state') == 'connected'
        details = {'host_id': identity, 'target': alert_context.label(host.get('name'), 'Relay')}
        result['relay:' + identity] = {'rule': 'relay_unavailable', 'component': 'relay', 'failing': not connected, 'details': details}
        routes = host.get('routes', [])[:64]
        failed = [row for row in routes if row.get('state') in ('failed', 'missing', 'drift')]
        known = connected and bool(routes) and all(row.get('state') in ('listening', 'failed', 'missing', 'drift') for row in routes)
        result['relay-route:' + identity] = {'rule': 'relay_route', 'component': 'relay',
            'failing': bool(failed) if known or connected and failed else None,
            'details': {**details, 'failed_routes': len(failed), 'ports': [row['listen_port'] for row in failed[:6] if type(row.get('listen_port')) is int]}}
        errors, complete = 0, connected and bool(routes)
        for route in routes:
            window = (route.get('diagnostics') or {}).get('interval') or {}
            valid = connected and route.get('state') == 'listening' and numeric(window.get('errors')) and numeric(window.get('seconds'), 45, 60)
            if valid: errors += window['errors']
            else: complete = False
        result['relay-errors:' + identity] = {'rule': 'connection_errors', 'component': 'relay',
            'failing': errors >= policy['connection_error_count'] if complete or errors >= policy['connection_error_count'] else None,
            'details': {**details, 'errors': errors, 'threshold': policy['connection_error_count'], 'complete': complete}}
        checks = host.get('address_checks', [])[:64]
        bad = [row for row in checks if row.get('state') in ('unresolved', 'mismatch')]
        complete = connected and bool(checks) and all(row.get('state') in ('verified', 'unresolved', 'mismatch') for row in checks)
        result['relay-address:' + identity] = {'rule': 'relay_address', 'component': 'relay',
            'failing': bool(bad) if complete or connected and bad else None,
            'details': {**details, 'failed_addresses': len(bad), 'addresses': [row['domain'] for row in bad[:3] if isinstance(row.get('domain'), str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,253}', row['domain'])]}}
    return result


def collect(api, resources, policy):
    now = time.time()
    conditions = resources_conditions(resources, policy, now)
    conditions['application-recovery'] = recovery_condition(api)
    if any(policy[key] for key in ('relay_unavailable', 'relay_route', 'relay_address', 'connection_errors')) and hasattr(api, 'relay_control'):
        try: conditions.update(relay_conditions(relay_snapshot(api), policy, now))
        except Exception:
            conditions['relay-inventory'] = {'rule': 'relay_unavailable', 'component': 'relay', 'failing': None, 'details': {}}
    for condition in conditions.values():
        if not policy[condition['rule']]: condition['failing'] = None
    return conditions
