"""Bounded profile sources and server-side copy inputs; never render client YAML."""
from copy import deepcopy
import json
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CopySource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    device_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,48}$')
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    connection_ids: list[str] | None = Field(default=None, min_length=1, max_length=256)
    copy_routing: StrictBool = False


def profile(api, identity):
    item = next((item for item in api.profiles() if item.get('id') == identity), None)
    if item is None:
        raise HTTPException(404, 'Профиль не найден')
    return api.normalize_profile(deepcopy(item))


def summary(api, item):
    return {'id': item['id'], 'name': item.get('name', ''), 'revision': api.profile_revision(item),
            'common_device_id': item.get('common_device_id'),
            'connection_count': len(item.get('connections', [])), 'device_count': len(item.get('devices', [])),
            'create_operation_id': item.get('create_operation_id', '')}


def page(rows, offset, limit):
    result = []
    for row in rows[offset:offset + limit]:
        if len(json.dumps(result + [row], ensure_ascii=False)) > 32000:
            if not result:
                raise HTTPException(422, {'code': 'source_item_too_large', 'message': 'Используйте серверное копирование по ID без передачи настроек модели'})
            break
        result.append(row)
    next_offset = offset + len(result)
    return {'items': result, 'total': len(rows), 'next_offset': next_offset if next_offset < len(rows) else None}


def catalog(api, offset, limit):
    return page([summary(api, api.normalize_profile(deepcopy(item))) for item in api.profiles()], offset, limit)


def public_settings(value):
    if isinstance(value, dict):
        return {key: public_settings(item) for key, item in value.items()
                if not re.search(r'password|secret|token|credential|private.?key|preshared.?key|api.?key', key, re.I)}
    if isinstance(value, list):
        return [public_settings(item) for item in value]
    return value


def source(api, identity, device_id, section, offset, limit, expected_revision=None):
    item = profile(api, identity)
    api.check_profile_revision(item, expected_revision)
    devices = item.get('devices', [])
    if device_id is not None and not any(device['id'] == device_id for device in devices):
        raise HTTPException(404, 'Устройство не найдено')
    if section == 'devices':
        rows = [{key: device[key] for key in ('id', 'name', 'scope', 'manual') if key in device}
                for device in devices if device_id is None or device['id'] == device_id]
    else:
        rows = [{**{key: connection[key] for key in ('id', 'name', 'component', 'device_id') if key in connection},
                 'settings': public_settings(connection.get('settings', {}))}
                for connection in item.get('connections', []) if device_id is None or connection.get('device_id') == device_id]
    return {'profile': summary(api, item), 'section': section, 'device_id': device_id,
            'credentials_included': False, **page(rows, offset, limit)}


def resolve_copy(api, payload):
    spec = payload.copy_from
    if spec is None:
        return payload
    if (payload.connections is not None or payload.channels or payload.routing
            or payload.devices != [api.ProfileDeviceInput(id='profile-common', name='Общие настройки профиля')]):
        raise HTTPException(422, 'Для копирования передайте name, copy_from и при необходимости новые limits; подключения и устройство создаёт сервер')
    item = profile(api, spec.profile_id)
    api.check_profile_revision(item, spec.expected_revision)
    device = next((device for device in item.get('devices', []) if device['id'] == spec.device_id), None)
    if device is None:
        raise HTTPException(404, 'Исходное устройство не найдено')
    connections = [connection for connection in item.get('connections', []) if connection.get('device_id') == spec.device_id]
    if spec.connection_ids is not None:
        wanted = set(spec.connection_ids)
        if len(wanted) != len(spec.connection_ids) or not wanted <= {connection['id'] for connection in connections}:
            raise HTTPException(422, 'Выберите уникальные ID подключений исходного устройства')
        connections = [connection for connection in connections if connection['id'] in wanted]
    if not connections:
        raise HTTPException(409, 'У исходного устройства нет выбранных подключений')
    relay_hosts = {connection.get('settings', {}).get('relay_host_id') for connection in connections} - {None, ''}
    if relay_hosts:
        from relay_private_transport import active
        control = api.relay_connection_control()
        for host_id in relay_hosts:
            active(control, host_id)  # A not-ready encrypted Relay must not become an open copy.
    routing = deepcopy(api.device_routing(item, spec.device_id)) if spec.copy_routing else {}
    # Per-profile connection IDs can be retained for routing references; native
    # credentials are provisioned with a new profile ID, never copied from here.
    definitions = [{**{key: connection[key] for key in ('id', 'component', 'name') if key in connection},
                    'device_id': 'profile-common', 'settings': deepcopy(connection.get('settings', {}))}
                   for connection in connections]
    return api.ProfileCreate(name=payload.name, connections=definitions, routing=routing,
                             limits=payload.limits, operation_id=payload.operation_id)
