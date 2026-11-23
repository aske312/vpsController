"""Own-subscription controls using the same catalogs and profile fields as the panel."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
import assistant_control as control
import assistant_roles as roles
import assistant_mihomo as mihomo
import assistant_contacts as contacts
import assistant_commands as commands
from assistant_bot_views import button, result, profile_item, prepared_record

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {'block_malware', 'block_advertising', 'block_privacy', 'quic_compatibility'}
DESKTOP = {'windows', 'macos', 'linux'}
PLATFORM_RULES = {'windows_geolocation', 'direct_games_enabled', 'direct_p2p_enabled'}


def change_result(text):
    return result('⚙️ **Настройки подписки**\n' + text,
                  [[button('Настройки подписки', '/manage')], [button('Моя подписка', '/subscription')]])


def remember_change(api, settings, actor, request_id, fingerprint, output, *, claim=False):
    if not request_id: return None
    with control.operations.locked(api.DATA_DIR):
        current = contacts.profile(api, settings, actor)
        receipts = current.get('subscription_receipts', {})
        prior = receipts.get(request_id)
        if prior and prior['fingerprint'] != fingerprint:
            raise HTTPException(409, 'ID сообщения уже использован')
        if claim and prior: return prior['result']
        receipts[request_id] = {'fingerprint': fingerprint, 'result': output}
        current['subscription_receipts'] = dict(list(receipts.items())[-32:])
        contacts.save_profile(api, settings, actor, current)
    return None


def apply_change(api, actor, method, path, body):
    prepared = mihomo.call(api, actor, method, path, body)
    record = prepared_record(api, actor, prepared['operation_id'])
    with control.operations.locked(api.DATA_DIR):
        saved = commands.pending_path(api, actor, operation_id=record['operation_id'])
        record['automatic'] = True
        control.operations.atomic_json(saved, record)
    # The human's settings button is the instruction. Reuse the single-send
    # executor and its live authorization without exposing its confirmation UI.
    commands.execute(api, actor, '/confirm ' + record['code'])
    completed = control.operations.read(saved)
    def applied(text, *, pending=False):
        if path.endswith('/subscription/rotate'):
            return change_result(text + (' После применения получите' if pending else ' Получите') + ' новую ссылку в «Моя подписка» и замените в приложениях.')
        return change_result(text + (' После применения обновите' if pending else ' Обновите') + ' подписку в приложении.')
    if completed.get('completion_state') == 'saved':
        return applied('Изменения сохранены.')
    identity = completed.get('completion_operation_id')
    if identity:
        try: state = api.get_application_operation(identity, None).get('state')
        except (HTTPException, OSError): state = None
        if state == 'succeeded':
            return applied('Изменения применены.')
        if state in ('failed', 'cancelled'):
            return change_result('Не удалось применить изменения. Откройте настройки и попробуйте позже.')
    return applied('Изменения приняты и применятся позже.', pending=True)


def catalog(name):
    return json.loads((ROOT / 'protocol-images' / 'mihomo' / name).read_text(encoding='utf-8'))


def routing_rules(device, effective, common):
    items = json.loads((Path(__file__).with_name('resources') / 'profile-routing-rules.json').read_text(encoding='utf-8'))
    allowed = set(catalog('client-capabilities.json')['mihomo']['rules'])
    if common: allowed -= PLATFORM_RULES
    if not common and (device.get('os') != 'windows' or device.get('scope') != 'hwid'): allowed.discard('windows_geolocation')
    if not common and device.get('os') not in DESKTOP: allowed -= {'direct_games_enabled', 'direct_p2p_enabled'}
    mode = effective.get('connection_mode') or ('tun' if effective.get('tun_enabled') or effective.get('tun_force') else 'proxy')
    if mode == 'proxy': allowed -= PLATFORM_RULES
    if effective.get('routing_scope') == 'selected': allowed &= FUNCTIONS | {'direct_local_network'}
    return [row for row in items if row['key'] in allowed]


def view(api, settings, actor, args, request_id):
    roles.require(settings, actor, 'subscription_read')
    roles.require(settings, actor, 'subscription_manage')
    # Even an administrator uses only the profile linked to their Telegram account.
    linked = mihomo.profile_for(settings, actor).get('mihomo_profile_id')
    if not linked: raise HTTPException(403, 'Подписка не привязана к вашей учётной записи')
    action = args[0] if args else 'show'
    can_route = all(roles.allows(settings, actor, p) for p in ('subscription_routing', 'subscription_devices'))
    if action not in ('show', 'rotate') and not can_route:
        raise HTTPException(403, 'Нет права изменять маршрутизацию и настройки устройств')
    fingerprint = hashlib.sha256(json.dumps([linked, args]).encode()).hexdigest()
    if action in ('set', 'service', 'rotate') and request_id:
        cached = contacts.profile(api, settings, actor).get('subscription_receipts', {}).get(request_id)
        if cached:
            if cached['fingerprint'] != fingerprint: raise HTTPException(409, 'ID сообщения уже использован')
            return cached['result']
    item = profile_item(api, settings, actor, linked)
    if not item: raise HTTPException(404, 'Подписка недоступна')
    if action == 'show' and len(args) <= 1:
        rows = [[button('Маршрутизация, Proxy / TUN и правила', '/manage targets')]] if can_route else []
        rows += [[button('Перевыпустить ссылку', '/manage rotate ' + item['revision'])], [button('← Подписка', '/subscription')]]
        return result('⚙️ **Настройки подписки**\n' + str(item.get('name') or linked)[:80], rows)
    if action != 'rotate' and not can_route: raise HTTPException(403, 'Нет права изменять маршрутизацию и настройки устройств')
    devices = item.get('devices', [])
    common_id = item.get('common_device_id')
    if action == 'targets' and len(args) <= 2:
        page = int(args[1]) if len(args) == 2 and args[1].isdigit() else 1
        page = max(1, min(page, max(1, (len(devices) + 7) // 8)))
        rows = [[button('Все устройства' if d['id'] == common_id else str(d.get('name') or d['id']), '/manage device ' + d['id'])] for d in devices[(page-1)*8:page*8]]
        nav = []
        if page > 1: nav.append(button('← Назад', '/manage targets ' + str(page-1)))
        if page*8 < len(devices): nav.append(button('Далее →', '/manage targets ' + str(page+1)))
        if nav: rows.append(nav)
        return result('⚙️ **Маршрутизация**\nОбщие настройки действуют там, где у устройства нет собственных настроек.', rows + [[button('← Настройки подписки', '/manage')]])

    if action == 'rotate':
        if len(args) != 2: raise HTTPException(422, 'Откройте настройки подписки заново')
        revision = args[1]
        method, path = 'POST', '/profiles/' + linked + '/subscription/rotate'
        body = {'expected_revision': revision}
    else:
        if len(args) < 2: raise HTTPException(422, 'Выберите устройство')
        device = next((d for d in devices if d['id'] == args[1]), None)
        if not device: raise HTTPException(404, 'Устройство не принадлежит вашей подписке')
        common = next((d for d in devices if d['id'] == common_id), {})
        schema = mihomo.call(api, actor, 'GET', '/routing/schema', {})
        defaults = schema.get('profile_defaults', schema.get('values', {}))
        own = device.get('routing') or {}
        base = {**(item.get('routing') or {}), **(common.get('routing') or {})}
        effective = {**defaults, **base, **own}
        # These functions are opt-in per profile, as in the panel and YAML generator.
        for key in FUNCTIONS: effective[key] = bool(own.get(key, base.get(key, False)))
        services = catalog('vpn-services.json')['services']
        services_by_id = {s['id']: s for s in services}
        rules = routing_rules(device, effective, device['id'] == common_id)
        prefix = '/manage '
        suffix = ' ' + device['id']
        back = [button('← Устройство', prefix + 'device' + suffix)]
        if action == 'device' and len(args) == 2:
            mode = effective.get('connection_mode') or ('tun' if effective.get('tun_enabled') or effective.get('tun_force') else 'proxy')
            scope = effective.get('routing_scope') == 'selected'
            label = 'Все устройства' if device['id'] == common_id else str(device.get('name') or device['id'])[:80]
            rows = [[button(('✓ ' if mode == m else '') + label, prefix + 'set' + suffix + ' connection_mode ' + m + ' ' + item['revision']) for m, label in [('proxy','Proxy'),('tun','TUN')]],
                    [button('Маршрутизация: ' + ('Только выбранное' if scope else 'По правилам'), prefix + 'set' + suffix + ' routing_scope ' + ('all' if scope else 'selected') + ' ' + item['revision'])],
                    [button('Приложения и сервисы через Proxy / VPN', prefix + 'services' + suffix)],
                    [button('Правила маршрутизации', prefix + 'rules' + suffix)],
                    [button('DNS: ' + ('по маршруту' if effective.get('route_dns') else 'общий'), prefix + 'set' + suffix + ' route_dns ' + ('0' if effective.get('route_dns') else '1') + ' ' + item['revision'])],
                    [button('← Устройства', '/manage targets')]]
            return result('⚙️ **' + label + '**\nИзменения применятся после обновления подписки в приложении.\nTUN также требует разрешения VPN в приложении. В Proxy трафик должен быть направлен через клиент.', rows)
        if action in ('rules', 'services') and len(args) in (2, 3):
            page = int(args[2]) if len(args) == 3 and args[2].isdigit() else 1
            entries = services if action == 'services' else rules
            page = max(1, min(page, max(1, (len(entries)+7)//8)))
            selected = set(str(effective.get('vpn_services') or '').split(','))
            rows = []
            for row in entries[(page-1)*8:page*8]:
                key = row['id'] if 'id' in row else row['key']
                on = key in selected if action == 'services' else bool(effective.get(key))
                cmd = 'service' if action == 'services' else 'set'
                rows.append([button(('✓ ' if on else '＋ ') + row.get('name', row.get('title', key)), prefix + cmd + suffix + ' ' + key + ' ' + ('0' if on else '1') + ' ' + item['revision'])])
            nav = []
            if page > 1: nav.append(button('← Назад', prefix + action + suffix + ' ' + str(page-1)))
            if page*8 < len(entries): nav.append(button('Далее →', prefix + action + suffix + ' ' + str(page+1)))
            if nav: rows.append(nav)
            text = 'Приложения и сервисы через Proxy / VPN\nВыбор действует в режиме «Только выбранное». Остальной трафик идёт напрямую. Список совпадает с интерфейсом подписки.' if action == 'services' else 'Правила маршрутизации\nДоступные правила учитывают ОС, Proxy/TUN и режим маршрутизации.'
            return result(text, rows + [back])
        if action not in ('set', 'service') or len(args) != 5: raise HTTPException(422, 'Выберите настройку кнопкой')
        key, raw, revision = args[2:]
        change = {}
        if action == 'service' and key in services_by_id and raw in ('0','1'):
            selected = set(str(effective.get('vpn_services') or '').split(',')) - {''}
            if raw == '1': selected.add(key)
            else: selected.discard(key)
            change['vpn_services'] = ','.join(s['id'] for s in services if s['id'] in selected)
        elif action == 'set' and key == 'connection_mode' and raw in ('proxy','tun'):
            change = {'connection_mode':raw, 'tun_enabled':raw == 'tun', 'tun_force':False}
        elif action == 'set' and key == 'routing_scope' and raw in ('all','selected'):
            change = {'routing_scope':raw, 'mode':'rule'}
        elif action == 'set' and raw in ('0','1') and key in {'route_dns', *(r['key'] for r in rules)}:
            change[key] = raw == '1'
        else: raise HTTPException(403, 'Эта настройка недоступна для выбранного устройства')
        # Full device collection is revision guarded; identities and metadata stay server-owned.
        safe = [{key:d[key] for key in ('id','name','routing','manual','personal_rule_ids') if key in d} for d in devices]
        if mihomo.administrator(settings, actor):
            for saved, original in zip(safe, devices):
                saved.update({key:original[key] for key in ('hwid_hash','created_at','os','os_version','client_name','client_version','user_agent','last_seen_at') if key in original})
        target = next(d for d in safe if d['id'] == device['id'])
        target['routing'] = {**own, **change}
        body = {'devices':safe, 'expected_revision':revision}
        if device['id'] == common_id:
            # The panel saves both copies: the root is the inheritance base for HWID devices.
            target['routing'] = {**base, **change}
            body['routing'] = dict(target['routing'])
        method, path = 'PATCH', '/profiles/' + linked

    if revision != item.get('revision'): raise HTTPException(409, 'Подписка изменилась. Откройте настройки заново.')
    pending = change_result('Изменение обрабатывается. Проверьте настройки позже.')
    cached = remember_change(api, settings, actor, request_id, fingerprint, pending, claim=True)
    if cached: return cached
    try:
        output = apply_change(api, actor, method, path, body)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            output = change_result('Изменение недоступно. Проверьте выданные права у владельца подписки.')
        elif exc.status_code in (404, 409, 422):
            output = change_result('Не удалось применить изменение. Откройте настройки заново и повторите выбор.')
        else:
            output = change_result('Пока не удалось подтвердить применение изменений. Проверьте настройки позже.')
    remember_change(api, settings, actor, request_id, fingerprint, output)
    return output
