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
import diagnostic_context as tracing

from fastapi import HTTPException

import assistant_control as control
import assistant_roles as roles

ID = r'[a-zA-Z0-9_-]{1,64}'
READ_ROUTES = (
    r'/status', r'/modules', rf'/modules/{ID}/settings', r'/profiles', r'/profiles/schema',
    rf'/profiles/{ID}/(?:stats|subscription|config|client-verification|client-reports)',
    r'/stats', r'/routing/schema', r'/dns/settings', r'/reconciliation', r'/action',
)
WRITE_ROUTES = {
    'POST': (r'/profiles', rf'/profiles/{ID}/subscription/rotate', rf'/modules/{ID}/(?:install|update)', r'/routing/personal-rules', r'/reconciliation', r'/recovery'),
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
    headers.update(tracing.outgoing())
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
        tracing.failure('mihomo_rejected')
        # Do not forward manager diagnostics, credentials or arbitrary response bodies.
        message = 'Mihomo отклонил запрос. Проверьте профиль, ревизию настроек и состояние операции.'
        if exc.code == 409:
            message = 'Настройки Mihomo изменились. Попросите ИИ перечитать их и подготовить новое подтверждение.'
        if exc.code == 422:
            message = 'Mihomo: неверные параметры. Попросите ИИ исправить запрос и подготовить новое подтверждение.'
            try: detail = json.loads(exc.read(16000)).get('detail')
            except (ValueError, OSError, AttributeError): detail = None
            if isinstance(detail, list):
                from assistant_trace import safe_field
                issues = {'missing': 'Обязательное поле', 'dict_type': 'Нужен объект', 'list_type': 'Нужен список', 'string_type': 'Нужна строка', 'string_pattern_mismatch': 'Неверный формат строки', 'int_type': 'Нужно целое число', 'bool_type': 'Нужно true или false'}
                fields = []
                for row in detail[:8]:
                    if not isinstance(row, dict) or not isinstance(row.get('loc'), list): continue
                    field = safe_field('.'.join(str(part) for part in row['loc'] if part != 'body')) or '*'
                    issue = 'Возьмите актуальную ревизию из GET: 64 hex-символа' if field.endswith('expected_revision') else issues.get(row.get('type'), 'Проверьте значение по схеме')
                    fields.append({'field': field, 'issue': issue})
                raise HTTPException(422, {'code': 'mihomo_validation_error', 'message': 'Mihomo: проверьте параметры запроса — ' + '; '.join(row['field'] + ': ' + row['issue'] for row in fields), 'fields': fields}) from None
            if isinstance(detail, dict) and detail.get('code') == 'invalid_routing_policy':
                fields = [field for field in detail.get('fields', []) if isinstance(field, str) and re.fullmatch(r'(?:groups|rules)(?:\.[0-9]{1,3})?(?:\.(?:id|name|type|members|strategy|url|interval|timeout|max_failed_times|tolerance|kind|value|target))?(?:\.[0-9]{1,3})?', field)]
                message = 'Mihomo: проверьте поля правил и групп' + (': ' + ', '.join(fields[:8]) if fields else '') + '. Исправьте запрос перед новым подтверждением.'
        raise HTTPException(exc.code if exc.code in (404, 409, 422, 503) else 502,
                            message) from None
    except (OSError, ValueError):
        tracing.failure('mihomo_unavailable')
        raise HTTPException(503, 'API Mihomo недоступен. Проверьте компонент и результат прежней операции перед повтором.') from None


def sanitize(value):
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        hidden = {'credential', 'subscription_token', 'device_identity_secret', 'subscriptions', 'retiring_connections', 'private_key', 'password',
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
        elif method == 'DELETE' and re.fullmatch(rf'/profiles/{ID}', parsed.path):
            roles.require(settings, actor, 'subscriptions_delete')
            if set(body) - {'expected_revision'}: raise HTTPException(422, 'Для удаления допустим только expected_revision')
        elif method == 'POST' and parsed.path == '/profiles':
            for permission in ('subscriptions_create', 'subscription_quota', 'subscription_speed', 'subscription_expiry'): roles.require(settings, actor, permission)
        elif method == 'POST' and re.fullmatch(rf'/profiles/{ID}/subscription/rotate', parsed.path):
            if set(body) != {'expected_revision'}: raise HTTPException(422, 'Для перевыпуска ссылки нужен только expected_revision профиля')
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
    if method == 'GET' and parsed.path in ('/modules', '/routing/schema', '/profiles/schema'):
        return parsed.path
    if method == 'GET' and re.fullmatch(re.escape(own) + r'/(stats|subscription|config|client-verification|client-reports)', parsed.path):
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
    if method == 'POST' and parsed.path == own + '/subscription/rotate':
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
        'reads': list(READ_ROUTES) if admin else ['/profiles', '/profiles/schema', '/modules', '/routing/schema', f'/profiles/{linked}/stats', f'/profiles/{linked}/subscription', f'/profiles/{linked}/config', f'/profiles/{linked}/client-reports'],
        'writes': WRITE_ROUTES if admin else {'PATCH': [f'/profiles/{linked}'], 'DELETE': [f'/profiles/{linked}/devices/<id>'], 'POST': [f'/profiles/{linked}/subscription/rotate']},
        'confirmation_required': True,
        'profile_schema_path': '/profiles/schema',
        'account_assignment': {'read': '/accounts', 'assign': '/accounts/<telegram_id>/profile',
                               'permission': 'roles_manage', 'body': {'profile_id': 'existing profile ID or null to unlink'}} if roles.allows(settings, actor, 'roles_manage') else None,
        'subscription_rotation': {'method': 'POST', 'path': '/profiles/<id>/subscription/rotate', 'body': ['expected_revision'],
                                  'effect': 'Отзывает общую и все ссылки устройств. Сохраняет подключения, устройства, HWID, правила и лимиты. Получите новый URL через GET /profiles/<id>/subscription.'},
        'permissions': sorted(roles.effective(settings, actor)),
        'profile_fields': ['name', 'channels', 'connections', 'devices', 'routing'] + (['limits'] if admin else []),
        'connection_fields': {'id': 'optional existing ID', 'component': 'module ID', 'name': 'label', 'device_id': 'device ID', 'settings': 'module connection settings'},
        'device_fields': {'id': 'existing ID or new manual ID', 'name': 'label', 'manual': 'true for new manual device', 'routing': 'device policy', 'personal_rule_ids': 'array of IDs'},
        'settings_fields': ['values', 'expected_revision'],
        'routing_policy': {'schema_path': '/routing/schema', 'schema_field': 'structured_policy_schema',
                           'value_key': 'structured_policy', 'selection_key': 'structured_policy_selection',
                           'units': {'timeout': 'milliseconds', 'interval': 'seconds', 'tolerance': 'milliseconds'},
                           'inheritance': 'Unset group health fields inherit shared routing settings. Select has no health checks. max_failed_times is a failure threshold, not retries.',
                           'group_members': ['*', 'connection:<id>', 'group:<name>'],
                           'strategies': ['consistent-hashing', 'round-robin', 'sticky-sessions']},
        'instructions': 'Для создания прочитайте /profiles/schema и /modules: POST /profiles принимает name, connections, devices, routing, limits. '
                        'Профиль можно создать с подключениями и правилами сразу. Для другого пользователя: владелец читает /accounts, создаёт профиль, '
                        'после подтверждения и успешного завершения находит его по create_operation_id и отдельно подтверждает PATCH /accounts/<telegram_id>/profile. '
                        'Привязка существующего профиля не требует его пересоздания. DELETE /profiles/<id> удаляет подписку только после подтверждения. '
                        'Сначала прочитайте /profiles и схему настроек. При PATCH и перевыпуске ссылки передавайте expected_revision из свежего ответа. '
                        'Для устройств передавайте полный список с сохранением остальных устройств; для подключений — полный список без credential. '
                        'Квота, скорость и срок — отдельные административные разрешения, пользователь их не меняет. Изменения требуют подтверждения собеседником.',
    }


def call(api, actor, method, path, body):
    settings = control.read(api)
    if path == '/accounts' or isinstance(path, str) and re.fullmatch(r'/accounts/[1-9][0-9]{0,15}/profile', path):
        import assistant_role_actions
        roles.require(settings, actor, 'roles_manage')
        if method == 'GET' and path == '/accounts':
            return {'accounts': assistant_role_actions.call(api, actor, {'method': 'GET'})['accounts']}
        if method != 'PATCH' or path == '/accounts' or not isinstance(body, dict) or set(body) != {'profile_id'}:
            raise HTTPException(422, 'Для привязки передайте PATCH /accounts/<telegram_id>/profile и profile_id (null для отвязки)')
        telegram_id = int(path.split('/')[2])
        account = next((row for row in settings['accounts'] if row['telegram_id'] == telegram_id), None)
        if account is None: raise HTTPException(404, 'Учётная запись не найдена; сначала добавьте пользователя в Nodera')
        identity = body['profile_id']
        if identity is not None and (not isinstance(identity, str) or not re.fullmatch(ID, identity)):
            raise HTTPException(422, 'Некорректный ID профиля')
        if identity is not None and not any(row['id'] == identity for row in profiles(api, actor)['items']):
            raise HTTPException(404, 'Профиль не найден; дождитесь завершения его создания')
        changes = {'telegram_id': telegram_id, 'mihomo_profile_id': identity}
        if identity is not None: changes['products'] = sorted(roles.products(account) | {'mihomo'})
        return assistant_role_actions.call(api, actor, {'method': 'PATCH', 'body': changes})
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
            try: policy = json.loads(value.get('values', {}).get('structured_policy') or '{}')
            except (TypeError, ValueError): policy = {}
            if not isinstance(policy, dict): policy = {}
            names = {group.get('name') for group in policy.get('groups', [])}
            return {'schema': value.get('schema', []), 'presets': value.get('presets', []),
                    'structured_policy_schema': value.get('structured_policy_schema'),
                    'policy_groups': [{'id': group.get('id') or group['name'], 'name': group['name'], 'type': group.get('type', 'fallback'),
                                       'rule_count': sum(rule.get('target') == group['name'] for rule in policy.get('rules', []))} for group in policy.get('groups', [])],
                    'ungrouped_rule_count': sum(rule.get('target', 'GATE.312') not in names for rule in policy.get('rules', []))}
        return {'config': value} if isinstance(value, str) else sanitize(value)
    import assistant_commands
    identity = uuid.uuid4().hex
    body = dict(body)
    if method == 'POST' and route == '/profiles':
        if set(body) - {'name', 'channels', 'connections', 'devices', 'routing', 'limits', 'operation_id'}:
            raise HTTPException(422, 'Проверьте поля создания профиля по /profiles/schema')
        try: preview = request('POST', '/profiles/validate', body)
        except HTTPException as exc:
            if exc.status_code == 404: raise HTTPException(409, 'Обновите компонент Mihomo: проверка создания профиля недоступна') from None
            raise
        if not isinstance(preview, dict) or preview.get('valid') is not True:
            raise HTTPException(502, 'Mihomo не подтвердил проверку профиля; запрос подтверждения не создан')
    if method == 'PATCH' and route == '/routing/settings':
        try: preview = request('POST', '/routing/validate', body)
        except HTTPException as exc:
            if exc.status_code == 404: raise HTTPException(409, 'Обновите компонент Mihomo: проверка маршрутизации перед подтверждением недоступна') from None
            raise
        if not isinstance(preview, dict) or preview.get('valid') is not True: raise HTTPException(502, 'Mihomo не подтвердил проверку маршрутизации; запрос подтверждения не создан')
    profile_mutation = (method in ('PATCH', 'DELETE') and re.fullmatch(rf'/profiles/{ID}', route)) or (method == 'POST' and re.fullmatch(rf'/profiles/{ID}/subscription/rotate', route))
    if profile_mutation:
        current = next((entry for entry in profiles(api, actor)['items'] if route.split('/')[2] == entry['id']), None)
        if not current:
            raise HTTPException(404, 'Профиль не найден')
        if method == 'DELETE': body.setdefault('expected_revision', current.get('revision'))
        if body.get('expected_revision') != current.get('revision') or not body.get('expected_revision'):
            raise HTTPException(409, 'Сначала прочитайте актуальный профиль и укажите его expected_revision')
    if method == 'PATCH' or method == 'POST' and route == '/profiles':
        body['operation_id'] = identity
    record = {'kind': 'mihomo', 'service': 'Mihomo ' + path, 'action': method,
              'method': method, 'path': path, 'body': body, 'code': secrets.token_hex(4),
              'operation_id': identity, 'expires': time.time() + 120, 'revision': settings['revision'], 'submitted': False}
    with control.operations.locked(api.DATA_DIR):
        assistant_commands.enqueue(api, actor, record)
    return {'confirmation_required': True, 'operation_id': identity, 'method': method, 'path': path, 'changes': sanitize(body),
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
        path = assistant_commands.pending_path(api, actor, code=record['code'])
        current = control.operations.read(path)
        if current.get('operation_id') == record['operation_id']:
            control.operations.atomic_json(path, {**current, 'completed_text': text,
                                                   'completion_state': 'operation' if isinstance(result, dict) and result.get('id') else 'saved'})
    return text
