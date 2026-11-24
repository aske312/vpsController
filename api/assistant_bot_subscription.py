"""Own-subscription controls using the same catalogs and profile fields as the panel."""
import hashlib
import json
import re
import time
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


def change_result(text, back='/manage', **extra):
    return result('⚙️ **Настройки подписки**\n' + text,
                  [[button('← К настройкам', back)], [button('Моя подписка', '/subscription')]], **extra)


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


def prepare_change(api, actor, method, path, body, preview, back):
    prepared = mihomo.call(api, actor, method, path, body)
    record = prepared_record(api, actor, prepared['operation_id'])
    with control.operations.locked(api.DATA_DIR):
        saved = commands.pending_path(api, actor, operation_id=record['operation_id'])
        record['subscription_ui'] = {'preview': preview, 'summary': '\n\n'.join(preview.split('\n\n')[:2]), 'back': back}
        control.operations.atomic_json(saved, record)
    return result('📝 **Проверьте изменение**\n\n' + preview
                  + '\n\nНастройки ещё не изменены. Нажмите «Продолжить», чтобы применить, или «Отменить», чтобы оставить как есть.',
                  [[button('Продолжить', '/manage confirm ' + record['code']),
                    button('Отменить', '/manage cancel ' + record['code'])]])


def change_status(api, record):
    ui = record['subscription_ui']
    back = ui['back']
    def output(text, *, pending=False):
        value = change_result(text + '\n\n' + ui['summary'], back, decision_complete=True)
        if pending:
            value['buttons'].insert(0, [button('Проверить результат', '/manage status ' + record['code'])])
        return value
    def applied(text, *, pending=False):
        next_step = ('Получите новую ссылку в «Моя подписка» и замените старую в приложениях.'
                     if record['path'].endswith('/subscription/rotate') else 'Обновите подписку в приложении, чтобы получить новые настройки.')
        return output(text + '\n\n' + ('После применения: ' if pending else '') + next_step, pending=pending)
    if record.get('completion_state') == 'saved':
        return applied('Изменения сохранены.')
    identity = record.get('completion_operation_id') or (record.get('operation_id') if record.get('submitted') else None)
    if identity:
        try: state = api.get_application_operation(identity, None).get('state')
        except (HTTPException, OSError): state = None
        if state == 'succeeded':
            return applied('Изменения применены.')
        if state in ('failed', 'cancelled'):
            return output('Не удалось применить изменения. Откройте настройки, проверьте текущее состояние и повторите выбор при необходимости.')
        if state in ('queued', 'running'):
            return applied('Изменения приняты и применятся позже.', pending=True)
    return output('Запрос отправлен, но результат пока неизвестен. Проверьте результат перед повторным изменением.', pending=True)


def decide_change(api, settings, actor, linked, action, args):
    if len(args) != 2 or not re.fullmatch(r'[0-9a-f]{8}', args[1]):
        raise HTTPException(422, 'Выберите действие кнопкой в сообщении подтверждения')
    saved = commands.pending_path(api, actor, code=args[1])
    record = control.operations.read(saved)
    if not record:
        return change_result('Этот запрос уже закрыт или истёк. Откройте настройки и выберите изменение заново.', decision_complete=True)
    if (not isinstance(record.get('subscription_ui'), dict) or record.get('kind') != 'mihomo'
            or record.get('path') not in ('/profiles/' + linked, '/profiles/' + linked + '/subscription/rotate')):
        raise HTTPException(403, 'Это подтверждение не относится к вашей подписке')
    back = record['subscription_ui']['back']
    if not record.get('submitted') and action != 'cancel' and record.get('expires', 0) <= time.time():
        return change_result('Время подтверждения истекло. Настройки не изменены. Откройте их и повторите выбор.', back, decision_complete=True)
    if action == 'status' or record.get('submitted'):
        return change_status(api, record) if record.get('submitted') else change_result('Изменение ещё не подтверждено. Используйте кнопки в сообщении с описанием.', back)
    try:
        if action == 'cancel':
            commands.execute(api, actor, '/cancel ' + record['code'])
            return change_result('Изменение отменено. Настройки остались прежними.', back, decision_complete=True)
        current = profile_item(api, settings, actor, linked)
        if not current or current.get('revision') != record['body'].get('expected_revision'):
            return change_result('Подписка уже изменилась. Этот запрос применять нельзя. Откройте настройки и выберите изменение заново.', back, decision_complete=True)
        commands.execute(api, actor, '/confirm ' + record['code'])
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            return change_result('Изменение недоступно: права могли измениться. Настройки не подтверждены.', back, decision_complete=True)
        current = control.operations.read(saved)
        if current.get('submitted'):
            return change_status(api, current)
        if exc.status_code in (404, 409, 422):
            return change_result('Подтверждение истекло или подписка изменилась. Откройте настройки и повторите выбор.', back, decision_complete=True)
        return change_status(api, control.operations.read(saved) or record)
    return change_status(api, control.operations.read(saved))


def change_preview(item, device, key, raw, effective, action, rules, services):
    common = device['id'] == item.get('common_device_id')
    target = 'Общие настройки профиля' if common else str(device.get('name') or device['id'])[:80]
    if key == 'connection_mode':
        before = effective.get('connection_mode') or ('tun' if effective.get('tun_enabled') or effective.get('tun_force') else 'proxy')
        labels = {'proxy': 'Proxy', 'tun': 'TUN'}
        title = 'Режим подключения: ' + labels.get(before, before) + ' → ' + labels[raw]
        impact = ('TUN направляет трафик устройства через приложение. В приложении потребуется разрешение на VPN; какие сервисы идут через VPN, определяют правила маршрутизации.'
                  if raw == 'tun' else 'Proxy работает с трафиком, который направлен через клиент или системный прокси. Прочие приложения могут идти напрямую.')
    elif key == 'routing_scope':
        label = lambda value: 'Только выбранные сервисы' if value == 'selected' else 'По правилам'
        title = 'Маршрутизация: ' + label(effective.get(key)) + ' → ' + label(raw)
        impact = ('Через VPN пойдут выбранные сервисы, остальной трафик — напрямую.' if raw == 'selected' else 'Трафик будет распределяться по правилам подписки.')
        if raw == 'selected' and not effective.get('vpn_services'):
            impact += ' Сейчас сервисы не выбраны: сначала добавьте их в список, иначе трафик будет идти напрямую.'
    else:
        row = next((row for row in (services if action == 'service' else rules) if row.get('id', row.get('key')) == key), {})
        title = row.get('name') or row.get('title') or ('DNS по маршруту' if key == 'route_dns' else key)
        before = key in set(str(effective.get('vpn_services') or '').split(',')) if action == 'service' else bool(effective.get(key))
        title += ': ' + ('включено' if before else 'выключено') + ' → ' + ('включено' if raw == '1' else 'выключено')
        state = 'включено' if raw == '1' else 'выключено'
        impact = 'Правило будет ' + state + '. Назначение: ' + row.get('text', '')
        if key == 'route_dns':
            impact = ('DNS-запросы будут следовать маршрутам подписки.' if raw == '1' else 'DNS по маршруту будет выключен; останутся обычные правила DNS подписки.')
        if key in ('block_advertising', 'block_malware', 'block_privacy'):
            feature = {'block_advertising': 'рекламных доменов', 'block_malware': 'опасных и фишинговых доменов', 'block_privacy': 'трекеров'}[key]
            impact = 'Блокировка ' + feature + (' будет включена.' if raw == '1' else ' будет выключена.')
        if action == 'service':
            impact = ('Сервис добавится в список для VPN.' if raw == '1' else 'Сервис будет убран из списка для VPN.')
            if effective.get('routing_scope') != 'selected': impact += ' Список действует только в режиме «Только выбранные сервисы»; сейчас включён режим «По правилам».'
    scope = ('Общие значения унаследуют устройства без собственных настроек. Индивидуальные настройки имеют приоритет.'
             if common else 'Изменение касается только этого устройства.')
    return '📍 ' + target + '\n\n' + title + '\n\n' + impact + '\n' + scope + '\n\nПосле применения обновите подписку в приложении.'


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
    if action in ('confirm', 'cancel', 'status'):
        return decide_change(api, settings, actor, linked, action, args)
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
        rows += [[button('Заменить ссылку подписки', '/manage rotate ' + item['revision'])], [button('← Подписка', '/subscription')]]
        return result('⚙️ **Настройки подписки**\n' + str(item.get('name') or linked)[:80]
                      + '\n\n1. Выберите общие настройки или своё устройство.\n2. Выберите режим, сервисы или правила.\n3. Проверьте описание и нажмите «Продолжить».\n\nДо подтверждения настройки не меняются. После применения обновите подписку в приложении. Лимиты и набор подключений меняет администратор.', rows)
    if action != 'rotate' and not can_route: raise HTTPException(403, 'Нет права изменять маршрутизацию и настройки устройств')
    devices = item.get('devices', [])
    common_id = item.get('common_device_id')
    if action == 'targets' and len(args) <= 2:
        page = int(args[1]) if len(args) == 2 and args[1].isdigit() else 1
        page = max(1, min(page, max(1, (len(devices) + 7) // 8)))
        rows = [[button('Общие настройки профиля' if d['id'] == common_id else str(d.get('name') or d['id']), '/manage device ' + d['id'])] for d in devices[(page-1)*8:page*8]]
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
        back = '/manage'
        preview = ('🔗 **Замена ссылки подписки**\n' + str(item.get('name') or linked)[:80]
                   + '\n\nСтарая ссылка и ссылки устройств перестанут работать. Новую ссылку нужно будет получить в «Моя подписка» и заменить во всех приложениях.\n\nПодключения, устройства, правила и лимиты сохранятся.')
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
            label = 'Общие настройки профиля' if device['id'] == common_id else str(device.get('name') or device['id'])[:80]
            rows = [[button(('✓ ' if mode == m else '') + label, prefix + 'set' + suffix + ' connection_mode ' + m + ' ' + item['revision']) for m, label in [('proxy','Proxy'),('tun','TUN')]],
                    [button('Маршрутизация: ' + ('Только выбранное' if scope else 'По правилам'), prefix + 'set' + suffix + ' routing_scope ' + ('all' if scope else 'selected') + ' ' + item['revision'])],
                    [button('Приложения и сервисы через Proxy / VPN', prefix + 'services' + suffix)],
                    [button('Правила маршрутизации', prefix + 'rules' + suffix)],
                    [button('DNS: ' + ('по маршруту' if effective.get('route_dns') else 'общий'), prefix + 'set' + suffix + ' route_dns ' + ('0' if effective.get('route_dns') else '1') + ' ' + item['revision'])],
                    [button('← Устройства', '/manage targets')]]
            return result('⚙️ **' + label + '**\n\nСейчас: ' + mode.upper() + ' · ' + ('Только выбранные сервисы' if scope else 'По правилам')
                          + '\n\nProxy — для трафика, направленного через клиент.\nTUN — для трафика устройства; требует разрешения VPN в приложении.'
                          + '\n\nВыберите настройку: сначала покажу, что изменится. Сохранение — только после «Продолжить».', rows)
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
            text += '\n\n✓ — включено, ＋ — выключено. Нажмите на строку, чтобы увидеть изменение и подтвердить его.'
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
        back = prefix + 'device' + suffix
        preview = change_preview(item, device, key, raw, effective, action, rules, services)
        if action == 'service':
            current = key in set(str(effective.get('vpn_services') or '').split(','))
        elif key == 'connection_mode':
            current = effective.get(key) or ('tun' if effective.get('tun_enabled') or effective.get('tun_force') else 'proxy')
        elif key == 'routing_scope':
            current = effective.get(key, 'all')
        else:
            current = bool(effective.get(key))
        desired = raw if key in ('connection_mode', 'routing_scope') else raw == '1'
        if current == desired:
            return change_result('Эта настройка уже выбрана. Ничего менять не нужно.', back)
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
    pending = change_result('Готовлю описание изменения. Настройки ещё не изменены.', back)
    cached = remember_change(api, settings, actor, request_id, fingerprint, pending, claim=True)
    if cached: return cached
    try:
        output = prepare_change(api, actor, method, path, body, preview, back)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            output = change_result('Изменение недоступно. Проверьте выданные права у владельца подписки.')
        elif exc.status_code in (404, 409, 422):
            output = change_result('Не удалось подготовить изменение. Откройте настройки заново и повторите выбор.', back)
        else:
            output = change_result('Не удалось подготовить подтверждение. Настройки не отправлены на изменение. Откройте их заново.', back)
    remember_change(api, settings, actor, request_id, fingerprint, output)
    return output
