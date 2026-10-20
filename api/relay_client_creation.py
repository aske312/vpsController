"""Durable native Direct result followed by automatic Relay preparation.

The native command is never repeated. An explicit resume only finishes Relay
for a fully captured native result. Client secrets stay in root-only files.
"""
import json
import re

from application_operation import atomic_json, OperationConflict
from relay_connections import direct_groups, direct_result


def result_path(api, identity):
    if not re.fullmatch(r'[0-9a-f]{32}', str(identity)):
        raise ValueError('Некорректный ID результата подключения')
    return api.DATA_DIR / 'relay' / 'clients' / (identity+'.json')


def perform(api, payload, identity, *, resume_id=None):
    control = api.relay_control()
    control.require_clear()
    control.checked(payload['expected_revision'])
    path = result_path(api, resume_id or identity)
    if path.exists():
        stored = json.loads(path.read_text())
        if stored.get('stage') == 'ready':
            return
        if not resume_id or stored.get('stage') != 'native-created' or not isinstance(stored.get('result'), dict):
            raise OperationConflict('Результат создания клиента не подтверждён; создание не повторено')
        settings, protocol = stored['settings'], stored['protocol']
        api.ComponentRegistry(api.DATA_DIR).require_managed(protocol)
        current = next((item for item in api.read_clients() if item.get('id') == stored['client_id'] and item.get('protocol') == protocol), None)
        if (not current or current.get('settings', {}).get('relay_host_id') != settings['relay_host_id']
                or (current.get('settings', {}).get('relay_address') or '') != (settings.get('relay_address') or '')):
            raise OperationConflict('Сохранённое подключение изменено или удалено; Relay не применён')
    else:
        if resume_id:
            raise ValueError('Результат подключения не найден')
        client = api.ClientCreate.model_validate(payload['client'])
        if client.protocol == 'vless-reality-xhttp' and client.settings.xray_protocol != 'vless' and 'cdn' in (client.vless_routes or []):
            raise ValueError('Для CDN через Relay используйте компонент Xray внутри Mihomo; в Direct через Relay доступен TLS')
        if not client.settings.relay_host_id or client.protocol == 'ikev2' or client.settings.channel_mode != 'direct':
            raise ValueError('Нужен совместимый протокол и выбранный Relay без старого режима relay')
        control.host(control.checked(payload['expected_revision']), client.settings.relay_host_id)
        settings, protocol = client.settings.model_dump(exclude_none=True), client.protocol
        api.ComponentRegistry(api.DATA_DIR).require_managed(protocol)
        control.ensure(settings['relay_host_id'], [], identity)
        stored = {'stage': 'creating-native', 'client_id': identity[:16], 'protocol': protocol, 'settings': settings}
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_json(path, stored)
        result = api.create_client_native(client, client_identity=identity[:16])
        if result.get('id') != stored['client_id']:
            raise OperationConflict('Не подтверждён ID созданного подключения; команда не повторяется')
        stored.update(stage='native-created', result=result)
        atomic_json(path, stored)
    groups = direct_groups(protocol, settings, stored['result'])
    stored['groups'] = groups
    atomic_json(path, stored)
    control.ensure(settings['relay_host_id'], groups, identity)
    result = {**direct_result(control, protocol, settings, stored['result']), 'protocol': protocol}
    # Persist the download before reporting success. A lost response is a GET.
    stored.update(stage='ready', result=result)
    atomic_json(path, stored)


def read_result(api, identity):
    path = result_path(api, identity)
    if not path.exists():
        return {'state': 'pending'}
    stored = json.loads(path.read_text())
    if stored.get('stage') == 'ready':
        return {'state': 'ready', 'result': stored['result']}
    return {'state': 'pending', 'can_resume': stored.get('stage') == 'native-created'}
