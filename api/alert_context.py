"""Bounded local context for alerts: configuration links never prove client connectivity."""
import json
import re
import time
import application_operation as operations


GOOD = {'running', 'applied', 'succeeded', 'listening', 'waiting'}
ZONES = {'application': 'Приложение', 'api': 'Панель / API', 'web': 'Веб-интерфейс', 'server': 'Сервер',
         'gateway': 'Сеть / Caddy', 'system': 'Система', 'ssh': 'Система / SSH',
         'fail2ban': 'Защита / Fail2ban', 'updates': 'Система / обновления',
         'mihomo': 'Mihomo', 'nodera': 'Nodera', 'dns': 'Сеть / DNS', 'network': 'Сеть', 'cdn': 'Сеть / CDN', 'relay': 'Сеть / Relay',
         'security': 'Защита', 'ai-agent': 'Nodera / ИИ', 'nodera-bot': 'Nodera / Telegram'}


def label(value, fallback='Не указано'):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()[:80] if isinstance(value, str) else fallback


def zone(component, rule=None):
    if rule == 'disk_pressure': return 'Система / диск'
    if rule == 'http_error_rate': return 'Панель / HTTP'
    if not isinstance(component, str): return 'Сервер'
    if component in ZONES: return ZONES[component]
    if isinstance(component, str) and re.fullmatch(r'transport-[a-z0-9_-]{1,60}', component):
        return 'Mihomo / ' + component.removeprefix('transport-')
    return 'Служба / ' + component if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', component) else 'Сервер'


def snapshot(api):
    import diagnostic_history
    try: current = diagnostic_history.load(api).get('current', {})
    except (OSError, ValueError, operations.OperationConflict): current = {}
    if not isinstance(current, dict): current = {}
    at = current.get('checked_at')
    fresh = type(at) in (int, float) and 0 <= time.time() - at <= 180
    services = current.get('services', {}) if fresh and isinstance(current.get('services'), dict) else {}
    services = {key: {**row, 'state': row.get('state') if isinstance(row.get('state'), str) else 'unknown'} for key, row in services.items() if isinstance(row, dict)}
    states = [row.get('state') for row in services.values() if isinstance(row, dict)]
    health = list(current['local_health'].values()) if fresh and isinstance(current.get('local_health'), dict) else []
    bad = sum(row.get('state') == 'error' or row.get('state') == 'stopped' and row.get('enabled') is True for row in services.values() if isinstance(row, dict)) + health.count('critical')
    system = 'есть сбой' if bad else 'локальные службы работают' if states and all(state in GOOD for state in states) and all(state == 'healthy' for state in health) else 'состояние не подтверждено'
    profiles = None
    target = api.DATA_DIR / 'mihomo/profiles.json'
    try:
        if not target.is_symlink() and target.stat().st_size <= 2 * 1024 * 1024:
            rows = json.loads(target.read_text(encoding='utf-8'))
            if isinstance(rows, list) and len(rows) <= 2000 and all(isinstance(row, dict) and isinstance(row.get('connections', []), list) for row in rows):
                profiles = [{'id': row.get('id'), 'name': label(row.get('name'), 'Профиль'),
                             'components': sorted({item['component'] for item in row.get('connections', []) if isinstance(item, dict) and isinstance(item.get('component'), str)}),
                             'gateway': any(isinstance(item, dict) and isinstance(item.get('settings'), dict) and item['settings'].get('route_mode') in ('cdn', 'tls', 'both') for item in row.get('connections', [])),
                             'cdn': any(isinstance(item, dict) and isinstance(item.get('settings'), dict) and item['settings'].get('route_mode') in ('cdn', 'both') for item in row.get('connections', []))}
                            for row in rows]
    except (OSError, ValueError, TypeError): pass
    return {'system': system, 'checked_at': at if fresh else None, 'bad_observations': bad, 'profiles': profiles,
            'services': services, 'fresh': fresh}


def lines(context, component, *, profile_id=None, rule=None):
    context = context or {}
    system = context.get('system', 'состояние не подтверждено')
    rows = context.get('profiles')
    if rows is None:
        profiles = 'настройки профилей недоступны; связь с устройствами не проверена'
    else:
        affected = [row for row in rows if profile_id and row['id'] == profile_id or not profile_id and
                    (component in row['components'] or component == 'mihomo' or rule == 'disk_pressure' or component == 'gateway' and row['gateway'] or component == 'cdn' and row.get('cdn'))]
        profiles = f'настроено {len(rows)}'
        if affected:
            profiles += f'; возможное влияние на {len(affected)}: ' + ', '.join(row['name'] for row in affected[:3]) + ('…' if len(affected) > 3 else '')
        else: profiles += '; прямая связь с зоной не установлена'
        profiles += '; связь с устройствами не проверена'
    at = context.get('checked_at')
    if type(at) in (int, float):
        from datetime import datetime, timezone
        system += ' · ' + datetime.fromtimestamp(at, timezone.utc).strftime('%H:%M:%S UTC')
    return [f'Система при проверке: {system}.', f'Профили: {profiles}.']
