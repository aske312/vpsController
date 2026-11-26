"""Controller-only resolution of opaque Relay diagnostic references.

References contain no profile/device/peer identity. Shared forwarding routes
identify a set of configured consumers, never an authenticated end user.
"""
import hashlib
import hmac
import json
import re
import time


def route_reference(token, route):
    generation = [route.get(k) for k in ('id', 'transport', 'listen_port', 'target_ip', 'target_port')]
    generation.append(route.get('auth', {}).get('credential_id'))
    body = b'relay-diagnostic-route-v1\0' + json.dumps(generation, separators=(',', ':')).encode()
    key = hashlib.sha256(token.encode()).digest()
    return hmac.new(key, body, hashlib.sha256).hexdigest()[:32]


def configured_mappings(control, profiles):
    import relay_connections
    registry = control.read()
    hosts = [{**h, 'routes':[r for r in registry['routes'] if r['host_id'] == h['id']]} for h in registry['hosts']]
    summaries = relay_connections.profile_summaries(profiles, hosts, control.origin)
    connections = {(str(p['id']), str(c['id'])): c for p in profiles for c in p.get('connections', [])}
    by_route = {}
    for profile in summaries:
        for channel in profile['channels']:
            connection_id = channel['id'].split(':', 2)[2]
            c = connections.get((profile['id'], connection_id), {})
            for route_id in channel['route_ids']:
                by_route.setdefault(route_id, []).append({'profile_id': profile['id'], 'profile_name': profile['name'],
                    'connection_id': connection_id, 'connection_name': channel['label'], 'device_id': c.get('device_id')})
    mappings = []
    for host in hosts:
        for route in host['routes']:
            candidates = by_route.get(route['id'], [])
            mappings.append({'route_ref': route_reference(host['token'], route), 'host_id': host['id'], 'route_id': route['id'],
                'scope': 'current_configuration_candidates', 'client_attribution': 'not_proven',
                'shared_route': len(candidates) > 1, 'candidates': candidates,
                'limitations': ['route_reference_does_not_authenticate_a_client',
                                'shared_or_copied_credentials_do_not_identify_a_physical_device']})
    return mappings


def resolve(control, host_id, reference, profiles):
    if not isinstance(reference, str) or not re.fullmatch(r'[0-9a-f]{32}', reference):
        raise ValueError('Invalid Relay diagnostic reference')
    current = next((r for r in configured_mappings(control, profiles)
                    if r['host_id'] == host_id and hmac.compare_digest(r['route_ref'], reference)), None)
    history = archived(control, host_id, reference)
    if current:
        if history: current['candidate_history'] = history['candidate_history']
        return current
    return history


def read_archive(control):
    path = control.root / 'diagnostic-bindings.json'
    if path.is_symlink(): raise ValueError('Unsafe diagnostic mapping file')
    if not path.exists(): return {'schema': 1, 'items': []}
    if path.stat().st_size > 4*1024*1024: raise ValueError('Diagnostic mapping file too large')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('items'), list)
            or len(value['items']) > 4096):
        raise ValueError('Invalid diagnostic mappings')
    for row in value['items']:
        if (not isinstance(row, dict) or not isinstance(row.get('host_id'), str)
                or not isinstance(row.get('route_ref'), str) or not re.fullmatch(r'[0-9a-f]{32}', row['route_ref'])
                or not isinstance(row.get('mapping'), dict) or not isinstance(row['mapping'].get('candidates'), list)
                or any(type(row.get(k)) not in (int, float) for k in ('observed_at', 'last_observed_at'))):
            raise ValueError('Invalid diagnostic mapping observation')
    return value


def remember(control, profiles, *, now=None):
    """Local, bounded observations, retained across route deletion/token rotation.

    Candidate snapshots are observations of configuration, not evidence of use.
    The ledger and its customer metadata are never uploaded to a Relay.
    """
    from application_operation import atomic_json, locked
    now = time.time() if now is None else now
    with locked(control.root / 'diagnostic-mapping-lock'):
        value = read_archive(control)
        old = value['items']
        items = [r for r in old if r.get('last_observed_at', 0) >= now-7*86400]
        indices = {(r['host_id'], r['route_ref']):i for i,r in enumerate(items)}
        for observation in configured_mappings(control, profiles):
            host_id, ref = observation['host_id'], observation['route_ref']
            index = indices.get((host_id, ref))
            previous = items[index] if index is not None else None
            if previous and previous['mapping'] == observation:
                if now-previous['last_observed_at'] >= 600:
                    items[index] = {**previous, 'last_observed_at':now}
            else:
                indices[(host_id, ref)] = len(items)
                items.append({'host_id': host_id, 'route_ref': ref, 'observed_at': now,
                              'last_observed_at': now, 'mapping': observation})
        items = sorted(items, key=lambda r: r['last_observed_at'])[-4096:]
        # Keep the byte bound even with unusually long profile/device labels.
        while items and len(json.dumps({'schema':1,'items':items}).encode('utf-8')) > 4*1024*1024:
            items = items[1:]
        if items != old:
            atomic_json(control.root / 'diagnostic-bindings.json', {'schema': 1, 'items': items})


def archived(control, host_id, reference):
    items = [r for r in read_archive(control)['items'] if r['host_id'] == host_id and r['route_ref'] == reference
             and r.get('last_observed_at', 0) >= time.time()-7*86400]
    if not items: return None
    latest = max(items, key=lambda r:r['last_observed_at'])
    return {**latest['mapping'], 'scope': 'archived_configuration_candidates',
            'observed_at': latest['observed_at'], 'last_observed_at': latest['last_observed_at'],
            'candidate_history': [{'observed_at':r['observed_at'], 'last_observed_at':r['last_observed_at'],
                                   'candidates':r['mapping']['candidates']} for r in items[-16:]]}
