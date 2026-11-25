"""Reversible data-plane activation with durable, exact-operation readback."""
import json
import re

from application_operation import atomic_json, OperationConflict
from relay_client import AgentAPIError


def receipt(value, identity):
    if (not isinstance(value, dict) or value.get('id') != identity
            or type(value.get('enabled')) is not bool or type(value.get('observed_enabled')) is not bool
            or value.get('state') not in ('pending', 'applied', 'failed')
            or type(value.get('revision')) is not int or value['revision'] < 0):
        raise ValueError('Не удалось подтвердить состояние Relay')
    return value


def validate_intent(intent):
    if (intent.get('schema') != 1 or intent.get('action') != 'activation'
            or not re.fullmatch(r'[0-9a-f]{32}', str(intent.get('host_id', '')))
            or not re.fullmatch(r'[0-9a-f]{32}', str(intent.get('operation_id', '')))
            or type(intent.get('enabled')) is not bool
            or type(intent.get('agent_revision')) is not int or intent['agent_revision'] < 0):
        raise ValueError('Повреждена запись переключения Relay')


def reconcile(control, intent, *, resume=False):
    from relay_manager import status_snapshot, route_snapshot
    validate_intent(intent)
    registry = control.read();host = control.host(registry, intent['host_id']);client = control.client(host)
    identity = intent['operation_id'];path = '/v1/activation/'+identity
    try:
        result = receipt(client.request('GET', path), identity)
    except AgentAPIError as exc:
        if exc.status != 404: raise
        status = status_snapshot(client.request('GET', '/v1/status'))
        if status['revision'] > intent['agent_revision'] or intent.get('rejected'):
            host['enabled'] = status.get('enabled', True);control.write(registry);control.pending.unlink()
            raise OperationConflict('Agent не применил переключение Relay; состояние прочитано, обновите данные') from None
        if status['revision'] != intent['agent_revision']:
            raise OperationConflict('Ревизия Agent изменилась; переключение не подтверждено') from None
        if not resume:
            raise OperationConflict('Результат переключения Relay неизвестен; команда не повторяется') from None
        # Explicit recovery only; the same ID, body and revision fence delayed requests.
        try: client.request('PUT', path, {'enabled': intent['enabled']}, revision=intent['agent_revision'])
        except Exception: pass
        return reconcile(control, intent)
    if result['enabled'] != intent['enabled']:
        raise OperationConflict('Ответ Agent относится к другому состоянию Relay')
    if result['state'] == 'pending':
        raise OperationConflict('Agent ещё переключает Relay; повторная команда не отправлена')
    status = status_snapshot(client.request('GET', '/v1/status'))
    if type(status.get('enabled')) is not bool or status['enabled'] != result['observed_enabled']:
        raise OperationConflict('Состояние Relay изменилось после переключения; проверьте результат')
    if result['state'] == 'applied' and result['observed_enabled'] != intent['enabled']:
        raise OperationConflict('Agent не подтвердил запрошенное состояние Relay')
    if not status['enabled']:
        routes = route_snapshot(client.request('GET', '/v1/routes'))
        if any(row['state'] != 'disabled' or row.get('sessions', 0) for row in routes['items']):
            raise OperationConflict('Не подтверждено закрытие всех маршрутов Relay')
    host['enabled'] = status['enabled'];control.write(registry);control.pending.unlink()
    if result['state'] == 'failed':
        raise OperationConflict('Переключение не завершено; Relay оставлен отключённым, настройки сохранены')


def perform(control, payload, identity):
    from relay_manager import status_snapshot
    control.require_clear();registry = control.checked(payload['expected_revision'])
    host = control.host(registry, payload['host_id']);client = control.client(host)
    status = status_snapshot(client.request('GET', '/v1/status'))
    if 'activation-v1' not in status.get('capabilities', []):
        raise ValueError('Для отключения без удаления требуется обновление Relay Agent')
    intent = {'schema': 1, 'action': 'activation', 'host_id': host['id'], 'operation_id': identity,
              'enabled': payload['enabled'], 'agent_revision': status['revision']}
    atomic_json(control.pending, intent)
    # Exclude from new selection until the requested state is proven.
    host['enabled'] = False;control.write(registry)
    try:
        receipt(client.request('PUT', '/v1/activation/'+identity, {'enabled': payload['enabled']}, revision=status['revision']), identity)
    except AgentAPIError as exc:
        intent['rejected'] = exc.status in (401, 403, 409, 422, 428, 495)
        intent['last_error'] = f'agent_http_{exc.status}'
    except Exception:
        intent['last_error'] = 'response_unknown'
    atomic_json(control.pending, intent)
    return reconcile(control, intent)
