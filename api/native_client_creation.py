"""Capture a Direct export once; an interrupted creation is never replayed."""
import time
import re
from types import SimpleNamespace

from application_operation import atomic_json, read, operation_id, OperationConflict

RESULT_TTL = 24 * 60 * 60


def result_path(api, identity):
    return api.DATA_DIR / 'native-client-results' / (operation_id(identity) + '.json')


def perform(api, payload, identity):
    path = result_path(api, identity)
    stored = read(path)
    if stored:
        if stored.get('state') == 'ready':
            return
        raise OperationConflict('Создание уже запускалось; повторная команда заблокирована')
    client = api.ClientCreate.model_validate(payload)
    if client.settings.relay_host_id:
        raise ValueError('Для Relay используйте управляемое создание подключения')
    api.ComponentRegistry(api.DATA_DIR).require_managed(client.protocol)
    # Fsync the intent before any protocol changes, including certificate issuance.
    atomic_json(path, {'state': 'unknown', 'client_id': identity[:16]})
    result = api.create_client_native(client, client_identity=identity[:16])
    if result.get('id') != identity[:16]:
        raise OperationConflict('ID созданного подключения не подтверждён')
    atomic_json(path, {'state': 'ready', 'client_id': identity[:16],
                       'expires_at': time.time() + RESULT_TTL, 'result': result})


def read_result(api, identity):
    path = result_path(api, identity)
    stored = read(path)
    if not stored:
        return {'state': 'pending'}
    if stored.get('state') == 'ready':
        if stored.get('expires_at', 0) <= time.time():
            atomic_json(path, {'state': 'expired', 'client_id': stored['client_id']})
            return {'state': 'expired'}
        return {'state': 'ready', 'result': stored['result']}
    return {'state': stored.get('state', 'unknown')}


def expire_results(data_dir):
    """Remove expired exports during normal operation-history maintenance."""
    for path in (data_dir / 'native-client-results').glob('*.json'):
        if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}', path.stem):
            continue
        try:
            read_result(SimpleNamespace(DATA_DIR=data_dir), path.stem)
        except (OSError, ValueError):
            continue
