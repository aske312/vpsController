"""Actor-scoped Mihomo API bridge. Administrative credentials stay in the API process."""
import base64
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import HTTPException

import assistant_control as control
import assistant_roles as roles

ID = r'[a-zA-Z0-9_-]{1,64}'
READ_ROUTES = (
    r'/status', r'/modules', rf'/modules/{ID}/settings', r'/profiles',
    rf'/profiles/{ID}/(?:stats|subscription|config|client-verification)',
    r'/stats', r'/routing/schema', r'/dns/settings', r'/reconciliation', r'/action',
)
WRITE_ROUTES = {
    'POST': (r'/profiles', rf'/modules/{ID}/(?:install|update)', r'/routing/personal-rules', r'/reconciliation', r'/recovery'),
    'PATCH': (rf'/profiles/{ID}', rf'/modules/{ID}/settings', r'/dns/settings',
              r'/routing/settings', r'/routing/presets', rf'/routing/personal-rules/{ID}'),
    'DELETE': (rf'/profiles/{ID}', rf'/profiles/{ID}/devices/{ID}', rf'/modules/{ID}', rf'/routing/personal-rules/{ID}'),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def profile_for(settings, actor):
    return next((entry for entry in settings['accounts'] if str(entry['telegram_id']) == actor), {})


def administrator(settings, actor):
    return roles.allows(settings, actor, 'subscriptions_admin')


def request(method, path, body=None, operation_id=None, *, timeout=35):
    """Only this fixed loopback origin receives the panel's credential."""
    password = os.getenv('ADMIN_PASSWORD', '')
    if not password:
        raise HTTPException(503, 'Служебное подключение к Mihomo не настроено')
    credentials = (os.getenv('ADMIN_USER', 'admin') + ':' + password).encode()
    headers = {'Authorization': 'Basic ' + base64.b64encode(credentials).decode(), 'Content-Type': 'application/json'}
    if operation_id:
        headers['X-Operation-ID'] = operation_id
    data = json.dumps(body).encode() if body is not None and method != 'GET' else None
    req = urllib.request.Request('http://127.0.0.1:8791/api/mihomo' + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise HTTPException(502, 'Ответ Mihomo слишком большой')
            return json.loads(raw) if 'json' in response.headers.get('Content-Type', '') else raw.decode('utf-8')
    except urllib.error.HTTPError as exc:
        # Do not forward manager diagnostics, credentials or arbitrary response bodies.
        raise HTTPException(exc.code if exc.code in (404, 409, 422, 503) else 502,
                            'Mihomo отклонил запрос. Проверьте профиль, ревизию настроек и состояние операции.') from None
    except (OSError, ValueError):
        raise HTTPException(503, 'API Mihomo недоступен. Проверьте компонент и результат прежней операции перед повтором.') from None


def sanitize(value):
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        hidden = {'credential', 'subscription_token', 'subscriptions', 'retiring_connections', 'private_key', 'password',
                  'secret', 'api_key', 'bot_token', 'session', 'control_token', 'logs', 'log_path', 'command', 'payload'}
        return {key: sanitize(item) for key, item in value.items() if key not in hidden}
    return value


def authorize(settings, actor, method, path, body):
    if not isinstance(path, str) or not path.startswith('/') or '%' in path or '\\' in path or '#' in path:
        raise HTTPException(422, 'Укажите путь API Mihomo из списка возможностей')
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or '..' in parsed.path or len(path) > 256:
        raise HTTPException(422, 'Недопустимый путь Mihomo')
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in ('device_id', 'format') or len(values) != 1 or not re.fullmatch(ID, values[0]) for key, values in query.items()):
        raise HTTPException(422, 'Недопустимые параметры Mihomo')
    routes = READ_ROUTES if method == 'GET' else WRITE_ROUTES.get(method, ())
    if not any(re.fullmatch(pattern, parsed.path) for pattern in routes) or method != 'GET' and query:
        raise HTTPException(403, 'Этот маршрут недоступен через MCP Mihomo')
    if not isinstance(body, dict) or len(json.dumps(body)) > 64000:
        raise HTTPException(422, 'Некорректное или слишком большое тело запроса')
    roles.require(settings, actor, 'subscription_read' if method == 'GET' else 'subscription_manage')
    if method != 'GET':
        if re.fullmatch(rf'/profiles/{ID}', parsed.path) and method == 'PATCH':
            fields = {'name': 'subscription_name', 'channels': 'subscription_connections', 'connections': 'subscription_connections', 'devices': 'subscription_devices', 'routing': 'subscription_routing'}
            if set(body) - {*fields, 'limits', 'expected_revision', 'operation_id'}: raise HTTPException(403, 'Поле подписки недоступно')
            for field, permission in fields.items():
                if field in body: roles.require(settings, actor, permission)
            if 'limits' in body:
                if not roles.effective(settings, actor) & {'subscription_quota', 'subscription_speed', 'subscription_expiry'}: raise HTTPException(403, 'Пользователь не меняет квоту, скорость и срок подписки')
                limits = body['limits']
                if limits is not None and (not isinstance(limits, dict) or set(limits) - {'traffic_gb', 'speed_mbps', 'days'}): raise HTTPException(422, 'Некорректные лимиты подписки')
                for key, permission in {'traffic_gb': 'subscription_quota', 'speed_mbps': 'subscription_speed', 'days': 'subscription_expiry'}.items():
                    if limits is None or key in limits: roles.require(settings, actor, permission)
        elif method == 'DELETE' and re.fullmatch(rf'/profiles/{ID}/devices/{ID}', parsed.path): roles.require(settings, actor, 'subscription_devices')
        elif method == 'DELETE' and re.fullmatch(rf'/profiles/{ID}', parsed.path): roles.require(settings, actor, 'subscriptions_delete')
        elif method == 'POST' and parsed.path == '/profiles':
            for permission in ('subscriptions_create', 'subscription_quota', 'subscription_speed', 'subscription_expiry'): roles.require(settings, actor, permission)
        elif parsed.path.startswith('/modules/'): roles.require(settings, actor, 'modules_manage')
        elif parsed.path.startswith('/routing/'): roles.require(settings, actor, 'routing_manage')
        elif parsed.path.startswith('/dns/'): roles.require(settings, actor, 'dns_manage')
        elif parsed.path in ('/reconciliation', '/recovery'): roles.require(settings, actor, 'recovery')
    if administrator(settings, actor):
        return parsed.path
    user = profile_for(settings, actor)
    linked = user.get('mihomo_profile_id')
    if not linked:
        raise HTTPException(403, 'Подписка Mihomo не привязана к пользователю')
    own = '/profiles/' + linked
    if method == 'GET' and parsed.path == '/profiles':
        return parsed.path
    if method == 'GET' and parsed.path in ('/modules', '/routing/schema'):
        return parsed.path
    if method == 'GET' and re.fullmatch(re.escape(own) + r'/(stats|subscription|config|client-verification)', parsed.path):
        return parsed.path
    if method == 'PATCH' and parsed.path == own:
        allowed = {'name', 'channels', 'connections', 'devices', 'routing', 'limits', 'expected_revision', 'operation_id'}
        if not set(body) <= allowed:
            raise HTTPException(403, 'Лимиты и срок подписки изменяет администратор')
        # User-supplied HWID identity would allow adopting somebody else's device.
        for device in body.get('devices') or []:
            if not isinstance(device, dict) or set(device) - {'id', 'name', 'routing', 'manual', 'personal_rule_ids'}:
                raise HTTPException(403, 'Метаданные устройства определяет сервер')
        return parsed.path
    if method == 'DELETE' and re.fullmatch(re.escape(own) + rf'/devices/{ID}', parsed.path):
        return parsed.path
    raise HTTPException(403, 'Доступна только привязанная подписка и её устройства')


def profiles(api, actor='panel'):
    settings = control.read(api)
    roles.require(settings, actor, 'subscription_read')
    result = request('GET', '/profiles')
    items = result.get('items', [])
    if not administrator(settings, actor):
        linked = profile_for(settings, actor).get('mihomo_profile_id')
        items = [item for item in items if item.get('id') == linked]
    return {'items': sanitize(items)}


def capabilities(api, actor):
    settings = control.read(api)
    admin = administrator(settings, actor)
    linked = profile_for(settings, actor).get('mihomo_profile_id')
    return {
        'role': 'administrator' if admin else 'subscriber', 'profile_id': linked,
        'reads': list(READ_ROUTES) if admin else ['/profiles', '/modules', '/routing/schema', f'/profiles/{linked}/stats', f'/profiles/{linked}/subscription', f'/profiles/{linked}/config'],
        'writes': WRITE_ROUTES if admin else {'PATCH': [f'/profiles/{linked}'], 'DELETE': [f'/profiles/{linked}/devices/<id>']},
        'confirmation_required': True,
        'permissions': sorted(roles.effective(settings, actor)),
        'profile_fields': ['name', 'channels', 'connections', 'devices', 'routing'] + (['limits'] if admin else []),
        'connection_fields': {'id': 'optional existing ID', 'component': 'module ID', 'name': 'label', 'device_id': 'device ID', 'settings': 'module connection settings'},
        'device_fields': {'id': 'existing ID or new manual ID', 'name': 'label', 'manual': 'true for new manual device', 'routing': 'device policy', 'personal_rule_ids': 'array of IDs'},
        'settings_fields': ['values', 'expected_revision'],
        'instructions': 'Сначала прочитайте /profiles и схему настроек. При PATCH передавайте expected_revision из свежего ответа. '
                        'Для устройств передавайте полный список с сохранением остальных устройств; для подключений — полный список без credential. '
                        'Квота, скорость и срок — отдельные административные разрешения, пользователь их не меняет. Изменения требуют подтверждения собеседником.',
    }


def call(api, actor, method, path, body):
    settings = control.read(api)
    route = authorize(settings, actor, method, path, body)
    if method == 'GET':
        if route == '/profiles':
            return profiles(api, actor)
        # Config and subscription link belong to the already authorized profile.
        value = request(method, path)
        if not administrator(settings, actor) and route == '/modules':
            return {'items': [{**{key: module[key] for key in ('id', 'name', 'description') if key in module},
                               'connection_settings': [{key: field[key] for key in ('key', 'label', 'type', 'options', 'min', 'max', 'placeholder', 'required', 'help') if key in field} for field in module.get('connection_settings', [])]}
                              for module in value.get('items', [])]}
        if not administrator(settings, actor) and route == '/routing/schema':
            return {'schema': value.get('schema', []), 'presets': value.get('presets', [])}
        return {'config': value} if isinstance(value, str) else sanitize(value)
    import assistant_commands
    identity = uuid.uuid4().hex
    body = dict(body)
    if method == 'PATCH' and re.fullmatch(rf'/profiles/{ID}', route):
        current = next((entry for entry in profiles(api, actor)['items'] if route == '/profiles/' + entry['id']), None)
        if not current:
            raise HTTPException(404, 'Профиль не найден')
        if body.get('expected_revision') != current.get('revision') or not body.get('expected_revision'):
            raise HTTPException(409, 'Сначала прочитайте актуальный профиль и укажите его expected_revision')
    if method == 'PATCH' or method == 'POST' and route == '/profiles':
        body['operation_id'] = identity
    record = {'kind': 'mihomo', 'service': 'Mihomo ' + path, 'action': method,
              'method': method, 'path': path, 'body': body, 'code': secrets.token_hex(4),
              'operation_id': identity, 'expires': time.time() + 120, 'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        pending = assistant_commands.pending_path(api, actor)
        previous = control.operations.read(pending)
        if previous.get('submitted') and not previous.get('completed_text'):
            if not control.operations.read(api.DATA_DIR / 'operations' / (previous['operation_id'] + '.json')):
                raise HTTPException(409, 'Прежнее действие ещё не подтверждено сервером. Проверьте его результат')
        control.operations.atomic_json(pending, record)
    return {'confirmation_required': True, 'method': method, 'path': path, 'changes': sanitize(body),
            'message': 'Подтвердите действие кнопкой в чате. Запрос ещё не выполнен.'}


def confirm(api, actor, record):
    settings = control.read(api)
    authorize(settings, actor, record['method'], record['path'], record['body'])
    body = dict(record['body'])
    if record['method'] == 'PATCH' and re.fullmatch(rf'/profiles/{ID}', record['path']) and 'limits' in body:
        current = next((entry for entry in profiles(api, actor)['items'] if record['path'] == '/profiles/' + entry['id']), None)
        if not current or current.get('revision') != body.get('expected_revision'): raise HTTPException(409, 'Подписка изменилась; подготовьте изменение заново')
        if body['limits'] is not None: body['limits'] = {**current.get('limits', {}), **body['limits']}
    if not administrator(settings, actor) and 'devices' in body:
        current = next((entry for entry in profiles(api, actor)['items'] if record['path'] == '/profiles/' + entry['id']), None)
        if not current:
            raise HTTPException(404, 'Подписка больше не доступна')
        existing = {device['id']: device for device in current.get('devices', [])}
        metadata = ('hwid_hash', 'created_at', 'os', 'os_version', 'client_name', 'client_version', 'user_agent', 'last_seen_at', 'manual')
        devices = []
        for device in body['devices']:
            prior = existing.get(device['id'])
            if any(key in device and device[key] != (prior or {}).get(key) for key in ('routing', 'personal_rule_ids')): roles.require(settings, actor, 'subscription_routing')
            if not prior and device.get('manual') is not True:
                raise HTTPException(422, 'Новое устройство должно быть ручным; HWID регистрируется клиентом')
            devices.append({**({key: prior[key] for key in ('routing', 'personal_rule_ids') if key in prior} if prior else {}), **device, **{key: prior[key] for key in metadata if prior and key in prior}})
        body['devices'] = devices
    result = request(record['method'], record['path'], body, record['operation_id'])
    text = ('Операция ' + result['id'] + '\n/operation ' + result['id']) if isinstance(result, dict) and result.get('id') else 'Изменения Mihomo сохранены.'
    import assistant_commands
    with control.operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        current = control.operations.read(path)
        if current.get('operation_id') == record['operation_id']:
            control.operations.atomic_json(path, {**current, 'completed_text': text})
    return text
