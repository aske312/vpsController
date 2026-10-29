"""Durable Relay preparation failures, separate from native profile provisioning."""
import re
import time

from application_operation import atomic_json, read
import relay_connections


def path(control, profile_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', str(profile_id)):
        raise ValueError('Invalid profile identity')
    return control.root / 'profiles' / f'{profile_id}.json'


def failures(control, profile_id):
    return read(path(control, profile_id)).get('failures', [])


def prepare(control, profile_id, connections, identity, direct_settings=None, previous=(), *, partial=False):
    """Never replay uncertain agent changes; report failures without dropping credentials."""
    groups = {}
    for connection in connections:
        selected = relay_connections.host_id(connection.get('settings', {}))
        if selected:
            groups.setdefault(selected, []).append(connection)
    if not groups and (partial or not path(control, profile_id).exists()):
        return []
    errors = [error for error in failures(control, profile_id) if error['host_id'] not in groups] if partial else []
    for host_id, items in groups.items():
        try:
            control.require_clear()
            relay_connections.prepare_mihomo(control, items, identity, direct_settings, previous)
        except (ValueError, OSError, RuntimeError):
            # Exception text may contain remote responses or credentials.
            errors.append({'host_id': host_id, 'reason': 'recovery_required' if control.pending.exists() else 'preparation_failed'})
    atomic_json(path(control, profile_id), {'failures': errors, 'checked_at': int(time.time())})
    return errors
