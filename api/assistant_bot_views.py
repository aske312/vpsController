"""Small Telegram views; authorization stays in the API, mutations require confirmation."""
import hashlib
import json
import platform
import re
import time
from pathlib import Path
from datetime import datetime, timezone

from fastapi import HTTPException
import assistant_control as control
import assistant_roles as roles

_active = {}


def capabilities(api, settings, actor):
    grants = roles.effective(settings, actor)
    account = next((row for row in settings.get('accounts', []) if str(row['telegram_id']) == actor), {})
    products = roles.products(account)
    linked = account.get('mihomo_profile_id')
    active = False
    if linked and 'mihomo' in products and 'subscription_read' in grants:
        key = (str(api.DATA_DIR), settings.get('revision'), actor, linked)
        cached = _active.get(key)
        if cached and time.monotonic() - cached[0] < 10: active = cached[1]
        else:
            import assistant_mihomo
            try:
                item = next((row for row in assistant_mihomo.request('GET','/profiles',timeout=3).get('items',[]) if row['id'] == linked), {})
                active = item.get('enabled', True) is not False and item.get('limits_status', {}).get('state') == 'active'
            except (HTTPException, OSError): pass
            if len(_active) >= 500: _active.pop(next(iter(_active)))
            _active[key] = (time.monotonic(), active)
    return {'role':roles.role(account), 'permissions': sorted(grants), 'products': sorted(products), 'linked_profile': linked,
            'sections': {'subscription': active, 'subscription_setup': 'mihomo' in products and 'subscription_read' in grants and not linked,
                         'telegram': 'telegram_read' in grants, 'ai': 'assistant' in products and 'chat' in grants,
                         'server': bool(grants & {'server_read','server_manage','diagnostics','logs_read','network_read','security_read'}),
                         'admin': 'subscriptions_admin' in grants}}


def button(text, command=None, url=None):
    return {'text': str(text)[:60], **({'url':url} if url else {'command':command})}


def result(text, buttons=None, **extra):
    return {'text':text, 'buttons':buttons or [], **extra}


def data_size(value):
    from assistant_commands import size
    return size(value)


def profile_item(api, settings, actor, identity=None):
    import assistant_mihomo
    identity = identity or assistant_mihomo.profile_for(settings, actor).get('mihomo_profile_id')
    return next((row for row in assistant_mihomo.profiles(api, actor)['items'] if row['id'] == identity), None)


def subscription(api, settings, actor, command, args):
    import assistant_mihomo
    identity=args[0] if args else assistant_mihomo.profile_for(settings,actor).get('mihomo_profile_id')
    link=None
    if command=='/subscription':
        if not identity or not re.fullmatch(assistant_mihomo.ID,identity):
            return result('Подписка Mihomo\nПодписка ещё не оформлена. Передайте ваш Telegram ID владельцу продукта, чтобы он создал и привязал подписку.\n\nВаш ID: `' + actor + '`\n\nА пока — молите Императора.')
        link=assistant_mihomo.call(api,actor,'GET','/profiles/'+identity+'/subscription',{}).get('url')
    try: item = profile_item(api, settings, actor, identity)
    except HTTPException:
        if command!='/subscription': raise
        item=None
    if not item and command=='/subscription' and link: return result('🔗 **Моя подписка**\n'+link+'\n\nСтатистика временно недоступна.')
    if not item: return result('Подписка не привязана или недоступна. Обратитесь к администратору.')
    if command == '/devices':
        try: stats = assistant_mihomo.call(api, actor, 'GET', '/profiles/' + item['id'] + '/stats', {})
        except HTTPException: stats = {}
        devices = [row for row in item.get('devices', []) if row.get('scope') != 'common' and row.get('id') != item.get('common_device_id')]
        rows = ['📱 **Устройства · ' + str(item.get('name', item['id']))[:80] + '**']
        for device in devices[:12]:
            icon = {'windows':'💻','macos':'💻','linux':'🖥','android':'📱','ios':'📱'}.get(device.get('os'), '📟')
            usage = (stats.get('usage') or {}).get('devices', {}).get(device['id'], {})
            total = usage.get('used_bytes')
            rows.append(icon + ' ' + str(device.get('name') or device['id'])[:80] + '\n' + str(device.get('client_name') or device.get('os') or 'ОС не определена')[:60] + ' · ' + data_size(total))
        if not devices: rows.append('Отдельные устройства пока не зарегистрированы.')
        if len(devices) > 12: rows.append(f'Ещё устройств: {len(devices)-12}')
        return result('\n\n'.join(rows))
    status,limits=item.get('limits_status') or {},item.get('limits') or {}
    used=status.get('used_bytes')
    traffic=f'{used/1000000000:.2f} GB' if type(used) in (int,float) else 'нет данных'
    quota=str(limits['traffic_gb'])+' GB' if limits.get('traffic_gb') is not None else 'без ограничения'
    state={'active':'активна','expired':'срок истёк','exhausted':'трафик исчерпан'}.get(status.get('state'),'неизвестно')
    expiry=status.get('expires_at')
    try: expiry_text=datetime.fromtimestamp(expiry,timezone.utc).strftime('%d.%m.%Y') if type(expiry) in (int,float) else 'без ограничения' if limits.get('days') is None else 'неизвестно'
    except (ValueError,OverflowError,OSError): expiry_text='неизвестно'
    text='\n'.join([str(item.get('name') or item['id'])[:80], 'Состояние: '+state, 'Трафик: '+traffic+' / '+quota, 'Срок: '+expiry_text,
                   'Скорость: '+(str(limits['speed_mbps'])+' Мбит/с' if limits.get('speed_mbps') is not None else 'без ограничения'),
                   'Подключений: '+str(len(item.get('connections',[])))])
    if command == '/subscription':
        text = '🔗 **Моя подписка**\n' + (link or 'Публичная ссылка ещё не настроена.') + '\n\n' + '\n'.join(text.splitlines()[:4])
    return result(text)


def summary(api, actor, command):
    import assistant_diagnostics as diagnostics
    if command == '/status':
        from assistant_commands import number, used
        data = api.overview(None); server, metrics = data.get('server', {}), data.get('resources', {})
        os_name=server.get('os') or server.get('os_name') or server.get('os_pretty_name')
        if not os_name:
            try: os_name=platform.freedesktop_os_release().get('PRETTY_NAME')
            except OSError: os_name=platform.system()
        uptime = metrics.get('uptime_s')
        age = f'{int(uptime//86400)} д {int(uptime%86400//3600)} ч' if type(uptime) in (int,float) else 'нет данных'
        return result('\n'.join(['🖥 **' + str(server.get('name') or 'VPS')[:80] + '**',
            'ОС: ' + str(os_name or 'нет данных')[:100],
            'Адрес: ' + str(server.get('public_endpoint') or server.get('public_ip') or '—'),
            'Uptime: ' + age, 'CPU: ' + number(metrics.get('cpu_percent'), '%') + ' · ' + number(metrics.get('cpu_count')) + ' ядер',
            'ОЗУ: ' + used(metrics.get('memory_total'), metrics.get('memory_available')),
            'Диск: ' + used(metrics.get('disk_total'), metrics.get('disk_available')),
            'NET: ↓ ' + data_size(metrics.get('network_rx')) + ' · ↑ ' + data_size(metrics.get('network_tx'))]))
    if command == '/services':
        rows = []
        for identity, definition in list(api.managed_services().items())[:30]:
            observed = api.observe_service(definition['unit'])
            state = observed.get('runtime', {}).get('state') or observed.get('state') or 'unknown'
            label = {'running':'работает','active':'активна','stopped':'остановлена','inactive':'неактивна','failed':'ошибка','configured':'настроена','unknown':'неизвестно'}.get(state, state)
            rows.append(('🟢' if state in ('running','active','configured') else '🔴' if state=='failed' else '⚪') + ' ' + str(definition.get('name') or identity)[:50] + ' · ' + label)
        return result('⚙️ **Службы**\n' + ('\n'.join(rows) or 'Нет данных'))
    value = diagnostics.call(api, {'/network':'vps_network','/security':'vps_security','/diagnostics':'vps_diagnostics'}[command], {}, actor)
    if command == '/network':
        interfaces = value.get('interfaces') or []
        if isinstance(interfaces, dict): interfaces = list(interfaces.values())
        rows = ['🌐 **Сеть**']
        if (value.get('server') or {}).get('public_ip'): rows.append('IP: '+str(value['server']['public_ip'])[:80])
        if (value.get('route') or {}).get('label'): rows.append('Маршрут: '+str(value['route']['label'])[:100])
        if (value.get('edge') or {}).get('provider'): rows.append('Провайдер: '+str(value['edge']['provider'])[:80])
        if isinstance(value.get('resolvers'),list): rows.append('DNS: '+', '.join(str(row)[:50] for row in value['resolvers'][:3]))
        if isinstance(value.get('listeners'),list): rows.append('Слушающих портов: '+str(len(value['listeners'])))
        for row in interfaces[:6]:
            if isinstance(row, dict): rows.append(str(row.get('name') or row.get('interface') or 'Интерфейс')[:40] + ' · ' + str(row.get('state') or row.get('status') or 'нет данных')[:30])
        for key,label in [('public_ip','Внешний IP'),('default_gateway','Шлюз'),('dns_servers','DNS')]:
            if value.get(key): rows.append(label + ': ' + str(value[key])[:120])
        if len(rows)==1: rows.append('Сводные показатели недоступны; подробности — в разделе «Сеть» панели.')
        return result('\n'.join(rows)[:1000])
    if command == '/security':
        app, ssh, firewall = (value.get(key) or {} for key in ('application_security','ssh','firewall'))
        checks = [('Пароль панели',app.get('admin_password_strong')),('CORS',app.get('cors_restricted')),('Защита секретов',app.get('secrets_protected')),
                  ('Доступ к панели',(firewall.get('panel_access') or {}).get('consistent')),('SSH по ключам',None if ssh.get('password_authentication') not in ('yes','no') else ssh['password_authentication']=='no')]
        rows = ['🛡 **Безопасность**'] + [('✅' if state is True else '⚠️' if state is False else '❔') + ' ' + name for name,state in checks]
        updates = (value.get('updates') or {}).get('security')
        rows.append('Обновления безопасности: ' + (str(updates) if type(updates) is int else 'нет данных'))
        return result('\n'.join(rows))
    findings = value.get('findings') or []
    counts = {state:sum(row.get('state')==state for row in findings) for state in ('passed','risk','unknown')}
    rows = ['🔎 **Диагностика**', f"✅ {counts['passed']} · ⚠️ {counts['risk']} · ❔ {counts['unknown']}" if findings else 'Данные проверок не получены.']
    rows += [str(row.get('risk') or row.get('id'))[:130] for row in findings if row.get('state')=='risk'][:4]
    rows.append('Пассивная проверка. Полный отчёт — в панели.')
    return result('\n'.join(rows))


def prepared_record(api, actor, identity):
    import assistant_commands as commands
    file=commands.pending_path(api,actor,operation_id=identity)
    with control.operations.locked(api.DATA_DIR):
        record=control.operations.read(file)
        if record.get('operation_id')!=identity or record.get('kind')!='mihomo': raise HTTPException(409,'Подтверждение не найдено')
        # A direct human bot command is independent of opt-in AI/MCP tools.
        # Live Mihomo permissions and the explicit confirmation still apply.
        record['source']='bot-command'
        control.operations.atomic_json(file,record)
    return record


def channel_choices(api, actor):
    import assistant_mihomo as mihomo
    modules=mihomo.call(api,actor,'GET','/modules',{}).get('items',[])
    return result('Выберите первый канал для подписки.', [[button(str(row.get('name') or row['id']), '/admin channel ' + row['id'])] for row in modules if row.get('installed') and row['id'].startswith('transport-')][:12] + [[button('Без каналов','/admin channel none'),button('Отмена','/admin cancel')]])


def admin(api, settings, actor, args, request_id):
    import assistant_contacts as contacts
    import assistant_mihomo as mihomo
    import assistant_commands as commands
    roles.require(settings, actor, 'subscriptions_admin')
    value = contacts.profile(api, settings, actor)
    cache = value.get('admin_receipts', {})
    fingerprint = hashlib.sha256(json.dumps(args).encode()).hexdigest()
    if request_id in cache:
        if cache[request_id]['fingerprint'] != fingerprint: raise HTTPException(409, 'ID сообщения уже использован')
        return cache[request_id]['result']
    action = args[0] if args else 'list'
    identity = args[1] if len(args)>1 else None
    state = value.get('admin_dialog') or {}
    if action == 'cancel':
        value.pop('admin_dialog', None)
        output = result('Администрирование Mihomo', [[button('Подписки', '/admin list'),button('Создать', '/admin create')]])
    elif action == 'create':
        roles.require(settings,actor,'subscriptions_create')
        value['admin_dialog'] = {'action':'create'}
        output = result('Название новой подписки?', [[button('Отмена','/admin cancel')]])
    elif action in ('channels','input') and state.get('action')=='channel':
        output=channel_choices(api,actor)
    elif action == 'input' and state:
        text = ' '.join(args[1:]).strip()
        operation = state['action']
        if operation in ('create','name'):
            if not 1<=len(text)<=80: raise HTTPException(422,'Название: от 1 до 80 символов')
            body = {'name':text}
        else:
            try: number = float(text.replace(',','.'))
            except ValueError: raise HTTPException(422,'Введите число; 0 — без ограничения') from None
            maximum={'days':36500,'speed_mbps':100000,'traffic_gb':1000000}[operation]
            if not 0<=number<=maximum or not number.is_integer(): raise HTTPException(422,'Нужно целое число от 0 до '+str(maximum))
            body = {'limits':{operation: int(number) or None}}
        if operation == 'create':
            value['admin_dialog'] = {'action':'channel','name':text}
            output = channel_choices(api,actor)
        else:
            item = profile_item(api,settings,actor,state['id'])
            if not item or item.get('revision')!=state['revision']: raise HTTPException(409,'Профиль изменился. Откройте его заново.')
            body['expected_revision'] = state['revision']
            prepared = mihomo.call(api,actor,'PATCH','/profiles/'+state['id'],body)
            record = prepared_record(api,actor,prepared['operation_id'])
            label={'name':'Название','traffic_gb':'Квота, GB','speed_mbps':'Скорость, Мбит/с','days':'Срок, дней'}[operation]
            preview = body.get('name') if operation=='name' else str(body['limits'][operation] or 'без ограничения')
            output = result('Изменение подписки «' + str(item.get('name') or state['id'])[:80] + '»\n' + label + ': ' + preview + '\n/confirm ' + record['code'] + '\n/cancel ' + record['code'])
            value.pop('admin_dialog',None)
    elif action == 'channel' and state.get('action')=='channel' and identity and re.fullmatch(mihomo.ID,identity):
        prepared = mihomo.call(api,actor,'POST','/profiles',{'name':state['name'],'channels':[] if identity=='none' else [identity]})
        record = prepared_record(api,actor,prepared['operation_id'])
        output = result('Создание подписки «' + state['name'] + '».\nКанал: ' + ('без каналов' if identity=='none' else identity) + '\n/confirm ' + record['code'] + '\n/cancel ' + record['code'])
        value.pop('admin_dialog',None)
    elif action in ('show','name','traffic_gb','speed_mbps','days','delete','rotate') and identity and re.fullmatch(mihomo.ID,identity):
        item = profile_item(api,settings,actor,identity)
        if not item: raise HTTPException(404,'Подписка не найдена')
        if action=='show':
            rows = [[button('Название','/admin name '+identity)]] if roles.allows(settings,actor,'subscription_name') else []
            for key,label,permission in [('traffic_gb','Квота','subscription_quota'),('speed_mbps','Скорость','subscription_speed'),('days','Срок','subscription_expiry')]:
                if roles.allows(settings,actor,permission): rows.append([button(label,'/admin '+key+' '+identity)])
            if roles.allows(settings,actor,'subscription_manage'): rows.append([button('Перевыпустить ссылку','/admin rotate '+identity)])
            if roles.allows(settings,actor,'subscriptions_delete'): rows.append([button('Удалить','/admin delete '+identity)])
            rows.append([button('← Подписки','/admin list')])
            limits=item.get('limits') or {}
            output = result(str(item.get('name') or identity) + '\n' + '\n'.join(label+': '+(str(limits[key])+unit if limits.get(key) is not None else 'без ограничения') for key,label,unit in [('traffic_gb','Квота',' GB'),('speed_mbps','Скорость',' Мбит/с'),('days','Срок',' дн.')]),rows)
        elif action in ('delete','rotate'):
            method,path = ('DELETE','/profiles/'+identity) if action=='delete' else ('POST','/profiles/'+identity+'/subscription/rotate')
            prepared = mihomo.call(api,actor,method,path,{'expected_revision':item['revision']})
            record = prepared_record(api,actor,prepared['operation_id'])
            output = result(('Удаление подписки' if action=='delete' else 'Перевыпуск ссылки') + ' «' + str(item.get('name') or identity) + '».' + ('\nСтарая ссылка перестанет работать.' if action=='rotate' else '') + '\n/confirm ' + record['code'] + '\n/cancel ' + record['code'])
        else:
            roles.require(settings,actor,{'name':'subscription_name','traffic_gb':'subscription_quota','speed_mbps':'subscription_speed','days':'subscription_expiry'}[action])
            value['admin_dialog'] = {'action':action,'id':identity,'revision':item['revision']}
            label={'name':'Название','traffic_gb':'Квота, GB','speed_mbps':'Скорость, Мбит/с','days':'Срок, дней'}[action]
            output = result('Введите новое название.' if action=='name' else label + ': введите целое число; 0 — без ограничения.',[[button('Отмена','/admin cancel')]])
    else:
        items = mihomo.profiles(api,actor)['items']
        page = int(identity) if identity and identity.isdigit() else 1
        page = max(1,min(page,max(1,(len(items)+5)//6)))
        rows = [[button(str(row.get('name') or row['id']), '/admin show '+row['id'])] for row in items[(page-1)*6:page*6]]
        nav=[]
        if page>1: nav.append(button('← Назад','/admin list '+str(page-1)))
        if page*6<len(items): nav.append(button('Далее →','/admin list '+str(page+1)))
        if nav: rows.append(nav)
        if roles.allows(settings,actor,'subscriptions_create'): rows.append([button('Создать подписку','/admin create')])
        value.pop('admin_dialog',None)
        output = result(f'Подписки Mihomo · {len(items)}',rows)
    if request_id:
        cache[request_id] = {'fingerprint':fingerprint,'result':output}
        value['admin_receipts'] = dict(list(cache.items())[-32:])
    if not value.get('snapshot'):
        value['snapshot'] = {'telegram_id':int(actor),'username':'','name':'','seen_at':time.time()}
    with control.operations.locked(api.DATA_DIR):
        current = contacts.profile(api,settings,actor)
        for key in ('admin_dialog','admin_receipts'): current.pop(key,None)
        current.update({key:value[key] for key in ('admin_dialog','admin_receipts','snapshot') if key in value})
        contacts.save_profile(api,settings,actor,current)
    return output


def view(api, actor, prompt, request_id=None):
    parts = prompt.strip().split(); command = parts[0].split('@')[0].lower() if parts else ''; args=parts[1:]
    permissions = {'/status':'server_read','/services':'server_read','/network':'network_read','/security':'security_read','/diagnostics':'diagnostics',
                   '/apps':'subscription_read','/subscription':'subscription_read','/profile':'subscription_read','/devices':'subscription_read','/logs':'logs_read','/logfile':'logs_read','/admin':'subscriptions_admin','/telegram':'telegram_read'}
    if command not in permissions: return None
    settings = control.read(api); roles.require(settings,actor,permissions[command])
    if command=='/telegram':
        import assistant_bot_telegram
        return assistant_bot_telegram.view(api, actor, prompt, request_id)
    if command=='/admin': return admin(api,settings,actor,args,request_id)
    if command in ('/subscription','/profile','/devices'): return subscription(api,settings,actor,command,args)
    if command=='/apps':
        import connection_apps
        if not args: return result('Выберите платформу.',[[button(label,'/apps '+key)] for key,label in connection_apps.PLATFORMS.items()])
        if len(args)!=1 or args[0] not in connection_apps.PLATFORMS: raise HTTPException(422,'Выберите платформу')
        catalog=json.loads((Path(__file__).resolve().parents[1]/'protocol-images/mihomo/client-apps.json').read_text(encoding='utf-8'))
        return result('Приложения · '+connection_apps.PLATFORMS[args[0]],[[button(row['name']+' · '+row['label'],url=row['url'])] for row in catalog if args[0] in row['platforms']])
    if command=='/logs' and (not args or len(args)==1 and args[0].isdigit()):
        services = {'application':'Приложение','system':'Система','ssh':'SSH', **{key:row.get('name',key) for key,row in api.managed_services().items()}}
        items=list(services.items()); pages=max(1,(len(items)+5)//6)
        page=max(1,min(int(args[0]) if args else 1,pages))
        rows=[[button(label,'/logfile '+key)] for key,label in items[(page-1)*6:page*6]]
        nav=[]
        if page>1: nav.append(button('← Назад','/logs '+str(page-1)))
        if page<pages: nav.append(button('Далее →','/logs '+str(page+1)))
        if nav: rows.append(nav)
        return result('Выберите журнал для выгрузки.',rows)
    if command in ('/logs','/logfile'):
        import assistant_diagnostics as diagnostics
        service=args[0] if len(args)==1 else ''
        if not re.fullmatch(r'[a-zA-Z0-9_.:@-]{1,80}',service): raise HTTPException(422,'Выберите журнал')
        value=diagnostics.call(api,'vps_logs',{'service':service,'lines':120},actor)
        safe=diagnostics.sanitize(api,value,max_chars=60000,max_string=8000)
        lines=safe.get('lines') or safe.get('entries') or safe.get('logs')
        content='\n'.join(str(row) for row in lines) if isinstance(lines,list) else json.dumps(safe,ensure_ascii=False,indent=2)
        return result('Журнал · '+service,document={'name':'logs-'+service.replace(':','-').replace('@','-')+'.txt','text':content[:60000] or 'Нет записей'})
    if args: raise HTTPException(422,'Команда не принимает аргументы')
    return summary(api,actor,command)
