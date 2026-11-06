"""Validated optional Agent 1.2 status, compatible with older Agents."""
ERROR_CODES = {'tcp_connect_timeout', 'tcp_connect_error', 'tcp_transfer_timeout', 'tcp_transfer_error',
               'udp_upstream_error', 'udp_listener_error', 'udp_buffer_full', 'udp_receive_buffer_unavailable',
               'udp_pending_full', 'udp_session_error', 'session_limit'}


def diagnostics(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid Agent diagnostics')
    result = {}
    for key in ('sent_packets', 'received_packets', 'dropped_packets', 'last_error_at', 'last_sent_at', 'last_received_at'):
        item = value.get(key)
        maximum = 253402300799 if key.endswith('_at') else 2**53 - 1
        if item is not None and (type(item) is not int or not 0 <= item <= maximum):
            raise ValueError('Invalid Agent counter')
        if key.endswith('_packets') and item is None:
            raise ValueError('Missing Agent packet counter')
        result[key] = item
    if value.get('last_error') is not None and value['last_error'] not in ERROR_CODES:
        raise ValueError('Invalid Agent error code')
    result['last_error'] = value.get('last_error')
    counts = value.get('error_counts', {})
    if not isinstance(counts, dict) or any(key not in ERROR_CODES or type(number) is not int or not 0 <= number <= 2**53 - 1 for key, number in counts.items()):
        raise ValueError('Invalid Agent error counters')
    result['error_counts'] = counts
    if 'return_state' in value:
        if value['return_state'] not in ('no_return', 'waiting', 'flowing', 'idle') or type(value.get('no_return_sessions')) is not int or not 0 <= value['no_return_sessions'] <= 65536:
            raise ValueError('Invalid return traffic observation')
        result.update(return_state=value['return_state'], no_return_sessions=value['no_return_sessions'])
    for key in ('udp_receive_buffer_bytes', 'udp_receive_allocated_bytes', 'udp_receive_budget_bytes',
                'udp_receive_budget_used_bytes', 'session_budget_used', 'session_budget_limit',
                'route_session_limit', 'statistics_since'):
        if key in value:
            number = value[key]
            maximum = 65536 if key in ('session_budget_used', 'session_budget_limit', 'route_session_limit') else 253402300799 if key == 'statistics_since' else 2**53-1
            if type(number) is not int or not 0 <= number <= maximum: raise ValueError('Invalid Agent resource counter')
            result[key] = number
    for used, limit in (('udp_receive_budget_used_bytes', 'udp_receive_budget_bytes'), ('session_budget_used', 'session_budget_limit')):
        if used in result and (limit not in result or result[used] > result[limit]): raise ValueError('Invalid Agent resource budget')
    if 'interval' in value:
        window = value['interval']
        if not isinstance(window, dict) or set(window) != {'seconds', 'errors', 'dropped_packets', 'new_sessions', 'error_counts'}:
            raise ValueError('Invalid Agent interval')
        for key in ('seconds', 'errors', 'dropped_packets', 'new_sessions'):
            if type(window[key]) is not int or not 0 <= window[key] <= (60 if key == 'seconds' else 2**53-1): raise ValueError('Invalid Agent interval counter')
        counts = window['error_counts']
        if not isinstance(counts, dict) or any(key not in ERROR_CODES or type(number) is not int or not 0 <= number <= 2**53-1 for key, number in counts.items()) or sum(counts.values()) != window['errors'] or window['dropped_packets'] > window['errors']:
            raise ValueError('Invalid Agent interval errors')
        result['interval'] = {**window, 'error_counts': dict(counts)}
    return result


def updates(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool or value.get('interval_hours') != 24 or value.get('state') not in ('unchecked', 'current', 'updated', 'disabled', 'failed', 'unknown'):
        raise ValueError('Invalid Agent update status')
    result = {key: value[key] for key in ('enabled', 'interval_hours', 'state')}
    import re
    for key, pattern in (('checked_at', r'\d{4}-\d\d-\d\dT[0-9:.+Z-]{8,35}'), ('version', r'\d+\.\d+\.\d+')):
        if key in value:
            if not isinstance(value[key], str) or not re.fullmatch(pattern, value[key]):
                raise ValueError('Invalid Agent update metadata')
            result[key] = value[key]
    if 'manual' in value:
        manual = value['manual']
        if not isinstance(manual, dict) or manual.get('state') not in ('queued', 'running', 'updated', 'failed', 'unknown'):
            raise ValueError('Invalid manual update state')
        for key, pattern in [('id', r'[0-9a-f]{32}'), ('version', r'\d{1,6}\.\d{1,6}\.\d{1,6}'), ('digest', r'sha256:[0-9a-f]{64}')]:
            if not isinstance(manual.get(key), str) or not re.fullmatch(pattern, manual[key]): raise ValueError('Invalid manual update metadata')
        if type(manual.get('requested_at')) not in (float, int) or not 0 <= manual['requested_at'] <= 253402300799:
            raise ValueError('Invalid update time')
        result['manual'] = {key: manual[key] for key in ('id', 'version', 'digest', 'requested_at', 'state')}
    return result


def address_checks(value):
    from relay_addresses import normalize_address
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('Invalid Agent DNS checks')
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get('state') not in ('verified', 'dns_only', 'unresolved', 'mismatch'):
            raise ValueError('Invalid Agent DNS state')
        name = normalize_address(item.get('domain'))
        if ':' in name or name.replace('.', '').isdigit():
            raise ValueError('Expected Agent domain')
        result.append({'domain': name, 'state': item['state']})
    return result
