"""Explicit idempotent recovery, also usable with an installed PRO v0.2.20.

CLI requires an explicit --apply; ordinary reconciliation never invokes it.
"""
from pathlib import Path
import sys

# Permit running a downloaded recovery tool without replacing application files.
if __name__ == '__main__':
    sys.path.insert(0, '/opt/vps-control/api')

import json
import re
from application_operation import atomic_json, OperationConflict
from relay_client import AgentAPIError
from relay_manager import validate_config, route_snapshot, same_route, CONFIG_KEYS

def resume_intent(control, operation_id):
    """Explicit recovery only: read first, then send the SAME ID/config/revision.

    The agent checks revisions under its mutation lock and treats matching IDs
    idempotently. A delayed original request therefore cannot allocate a duplicate
    or overwrite an intervening mutation. Ordinary reconcile never calls this.
    """
    if not control.pending.exists():
        raise OperationConflict('Неподтверждённая операция уже завершена; обновите ROUTES')
    intent = json.loads(control.pending.read_text(encoding='utf-8'))
    if intent.get('operation_id') != operation_id:
        raise OperationConflict('Неподтверждённая операция изменилась; обновите ROUTES')
    route = intent.get('route', {})
    validate_config(route)
    if (intent.get('schema') != 1 or intent.get('action') not in ('put', 'delete')
            or not re.fullmatch(r'pro-[0-9a-f]{32}', route.get('id', ''))
            or type(intent.get('agent_revision')) is not int or intent['agent_revision'] < 0):
        raise ValueError('Повреждена запись восстановления relay')
    if route.get('cdn_domain'):
        import relay_cdn
        relay_cdn.domain(route['cdn_domain'])
        relay_cdn.edge_ip(route['target_ip'])
        if route['transport'] != 'tcp' or route['target_port'] != 443:
            raise OperationConflict('Некорректное назначение CDN')
    elif route['target_ip'] != control.origin:
        raise OperationConflict('Origin изменился; восстановление старого маршрута остановлено')
    host = control.host(control.read(), route['host_id'])
    client = control.client(host)
    remote = route_snapshot(client.request('GET', '/v1/routes'))
    found = next((item for item in remote['items'] if item['id'] == route['id']), None)
    if found and not same_route(found, route):
        raise OperationConflict('Маршрут агента изменён; восстановление остановлено')
    applied = found is None if intent['action'] == 'delete' else found is not None
    if applied or intent.get('rejected'):
        return control.reconcile()
    if remote['revision'] > intent['agent_revision']:
        # The old If-Match cannot apply anymore, even if its packet arrives later.
        intent['rejected'] = True
        atomic_json(control.pending, intent)
        return control.reconcile()
    if remote['revision'] != intent['agent_revision']:
        raise OperationConflict('Ревизия агента уменьшилась; требуется проверка его состояния')
    try:
        client.request('DELETE' if intent['action'] == 'delete' else 'PUT', '/v1/routes/'+route['id'],
                       None if intent['action'] == 'delete' else {key: route[key] for key in CONFIG_KEYS},
                       revision=intent['agent_revision'])
        intent['acknowledged'] = True
        intent.pop('last_error', None)
    except AgentAPIError as exc:
        # This reply belongs to the explicit retry. It does not by itself
        # rule out a delayed original request with the same revision.
        intent['last_error'] = f'agent_http_{exc.status}'
    except Exception:
        intent['last_error'] = 'response_unknown'
    atomic_json(control.pending, intent)
    return control.reconcile()


if __name__ == '__main__':
    import argparse
    import os
    import application_operation
    import main as api

    parser = argparse.ArgumentParser(description='Read or explicitly resume one pending Relay intent. Load the installed panel environment before use.')
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--apply', action='store_true', help='Read first, then explicitly retry the original idempotent request if necessary')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root with the installed application environment')
    identity = application_operation.operation_id(args.operation_id)
    control = api.relay_control()
    with application_operation.mutation_lock(api.DATA_DIR):
        if not control.pending.exists():
            parser.error('No pending Relay intent remains; nothing was sent')
        intent = json.loads(control.pending.read_text(encoding='utf-8'))
        if intent.get('operation_id') != identity:
            parser.error('The pending operation changed; nothing was sent')
        route = intent['route']
        validate_config(route)
        host = control.host(control.read(), route['host_id'])
        print(json.dumps({'operation_id': identity, 'host': host['name'], 'agent_url': host['agent_url'],
                          'action': intent['action'], 'transport': route['transport'], 'listen_port': route['listen_port'],
                          'target_ip': route['target_ip'], 'target_port': route['target_port'], 'apply': args.apply}), flush=True)
        if args.apply:
            try:
                resume_intent(control, identity)
            except (OperationConflict, OSError, ValueError, AgentAPIError) as exc:
                # Never print arbitrary remote text, tokens, or environment values.
                print(json.dumps({'result': 'not_confirmed', 'pending': control.pending.exists(), 'error_type': type(exc).__name__}))
                raise SystemExit(1)
            print(json.dumps({'result': 'route_confirmed', 'pending': control.pending.exists(), 'profile_save_required': True}))

