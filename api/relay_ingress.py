"""Authenticated TCP entry credentials, kept outside the public Relay registry."""
import copy
import hashlib
import re
import secrets
import uuid

from application_operation import atomic_json, OperationConflict


def metadata(value):
    if (not isinstance(value, dict) or set(value) != {'type', 'username', 'credential_id'}
            or value['type'] != 'tls-connect' or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(value['username']))
            or not re.fullmatch(r'[0-9a-f]{32}', str(value['credential_id']))):
        raise ValueError('Некорректная защита TCP Relay')
    return dict(value)


def credential(control, route, *, create=False):
    import json
    folder = control.root/'ingress-credentials'
    path = folder/(route['id']+'.json')
    if path.is_symlink(): raise ValueError('Учётные данные Relay недоступны')
    try: value = json.loads(path.read_text())
    except FileNotFoundError:
        if not create: raise OperationConflict('Отсутствуют учётные данные защищённого Relay') from None
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = {'type': 'tls-connect', 'username': secrets.token_urlsafe(24), 'password': secrets.token_urlsafe(32), 'credential_id': uuid.uuid4().hex}
        atomic_json(path, value)
    public = metadata({key: value.get(key) for key in ('type', 'username', 'credential_id')})
    if not isinstance(value.get('password'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', value['password']):
        raise ValueError('Повреждены учётные данные Relay')
    if route.get('auth') and metadata(route['auth']) != public:
        raise OperationConflict('Учётные данные Relay изменились; экспорт остановлен')
    return value


def provision(control, route):
    value = credential(control, route, create=True)
    return metadata({key: value[key] for key in ('type', 'username', 'credential_id')})


def wire_config(control, route):
    body = {key: route[key] for key in ('transport', 'listen_port', 'target_ip', 'target_port')}
    if route.get('auth'):
        value = credential(control, route)
        body['auth'] = {**metadata(route['auth']), 'password_sha256': hashlib.sha256(value['password'].encode()).hexdigest()}
    return body


def security(value):
    import ipaddress
    if (not isinstance(value, dict) or value.get('state') not in ('ready', 'unpaired', 'legacy_exposed')
            or type(value.get('api_source_restricted')) is not bool
            or not isinstance(value.get('unauthenticated_external_routes'), list)
            or len(value['unauthenticated_external_routes']) > 64
            or any(not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', identity) for identity in value['unauthenticated_external_routes'])):
        raise ValueError('Некорректное состояние безопасности Relay')
    origin = value.get('controller_ip')
    if origin is not None:
        address = ipaddress.ip_address(origin)
        if not address.is_global or address.is_multicast: raise ValueError('Некорректная привязка Relay')
    if (value['state'] == 'unpaired') != (origin is None) or value['api_source_restricted'] != (origin is not None):
        raise ValueError('Неполная привязка Relay')
    return {key: copy.deepcopy(value[key]) for key in ('state', 'controller_ip', 'api_source_restricted', 'unauthenticated_external_routes')}


def bind_controller(client, status, origin, *, required=False):
    if 'controller-binding-v1' not in status.get('capabilities', []):
        if required: raise ValueError('Обновите Relay Agent: новый узел требует автоматическую защиту входов')
        return
    current = security(status.get('security'))
    if current['controller_ip'] == origin: return
    if current['controller_ip'] is not None: raise OperationConflict('Relay привязан к другому серверу PRO')
    try: client.request('PUT', '/v1/controller', {'origin_ip': origin})
    except Exception:
        # Read the outcome once; never replay a mutation after a lost reply.
        observed = security(client.request('GET', '/v1/status').get('security'))
        if observed['controller_ip'] != origin:
            raise OperationConflict('Привязка Relay не подтверждена; проверьте состояние узла') from None
    else:
        observed = security(client.request('GET', '/v1/status').get('security'))
    if observed['controller_ip'] != origin or not observed['api_source_restricted']:
        raise OperationConflict('Agent не подтвердил ограничение управляющего доступа')


def protect_subscription(control, text, selections):
    import yaml
    from relay_connections import host_id
    registry = control.read()
    protected = [route for route in registry['routes'] if route.get('auth')]
    if not protected: return text
    config = yaml.safe_load(text)
    helpers = {}
    for index, node in enumerate(config.get('proxies', [])):
        connection = selections.get(node.get('name'), {})
        settings = connection.get('settings', {})
        selected = host_id(settings)
        if not selected: continue
        route = next((row for row in protected if row['host_id'] == selected and row['transport'] == 'tcp'
                      and row['listen_port'] == node.get('port')), None)
        if not route: continue
        if node.get('dialer-proxy'): raise ValueError('Защищённый TCP Relay требует отдельный транспорт подключения')
        host = control.host(registry, selected)
        value = credential(control, route)
        name = '__312_transport_tcp_'+value['credential_id']
        helper = {'name': name, 'type': 'http', 'server': node['server'], 'port': route['listen_port'],
                  'username': value['username'], 'password': value['password'], 'tls': True,
                  'fingerprint': host['certificate_sha256'], 'skip-cert-verify': False, 'udp': False}
        helpers[name] = helper
        replacement = copy.deepcopy(node)
        replacement.update(server=route['target_ip'], port=route['target_port'])
        replacement['dialer-proxy'] = name
        config['proxies'][index] = replacement
    if any(node.get('name') in helpers for node in config.get('proxies', [])+config.get('proxy-groups', [])):
        raise ValueError('Имя транспорта защищённого Relay занято')
    config['proxies'].extend(helpers.values())
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
