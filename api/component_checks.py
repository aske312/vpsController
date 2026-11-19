"""Automatic local component checks, independent from paid AI and user traffic."""
import ast
import asyncio
from datetime import datetime, timezone

import application_operation as operations
import assistant_control as control


def cached(api):
    try: return operations.read(api.DATA_DIR / 'component-health.json').get('items', {})
    except (OSError, operations.OperationConflict): return {}


def visible(api):
    images = api.protocol_image_manifests(include_versions=False)
    records = cached(api)
    result = []
    for identity, image in images.items():
        if identity not in ('nodera', 'mihomo') or not image.get('installed') or image.get('management', {}).get('retained'): continue
        item = dict(records.get(identity, {'status': 'pending', 'reason': 'Автоматическая проверка запускается', 'checked_at': None}))
        if item.get('checked_at'):
            try:
                stamp = datetime.fromisoformat(item['checked_at'].replace('Z', '+00:00'))
                if not 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 180: raise ValueError('stale')
            except (ValueError, TypeError):
                item.update(status='unknown', reason='Актуальная автоматическая проверка недоступна')
        result.append({**item, 'id': identity, 'name': image.get('name', identity)})
    return result



def inspect(api, identity, image, *, read_only=False):
    checks = []
    def check(name, ok, reason):
        level = 'process' if name.endswith('service') or name == 'service' else 'local_api' if name == 'manager_api' else 'configuration' if 'configuration' in name else 'local_runtime'
        checks.append({'name': name, 'ok': ok, 'state': 'passed' if ok else 'failed', 'level': level, 'reason': reason})
    observed = api.observe_service(image['service'])
    check('service', observed.get('runtime', {}).get('state') == 'running', observed.get('runtime', {}).get('reason', 'Нет наблюдения службы'))
    if identity == 'nodera':
        settings = control.read(api)
        for component in control.MODULES:
            root = control.runtime_directory(component)
            entry = root / ('runner.py' if component == 'ai-agent' else 'bot.py')
            try:
                ast.parse(entry.read_text(encoding='utf-8'))
                python = root / 'venv' / 'bin' / 'python'
                output = api.run(str(python), str(entry), '--self-check', timeout=8) if python.is_file() else ''
                valid = ('AI-agent runtime ready' if component == 'ai-agent' else 'NoderaBot runtime ready') in output
            except (OSError, SyntaxError): valid = False
            check(component + '_runtime', valid, 'Локальная среда и синтаксис runtime')
        if settings['modules']['ai-agent']:
            check('ai_configuration', control.ready(settings, 'ai-agent'), 'Настроены API и модель; платный запрос не выполнялся')
        if settings['modules']['nodera-bot']:
            bot = api.observe_service(control.BOT_UNIT)
            check('bot_service', bot.get('runtime', {}).get('state') == 'running', 'Процесс Telegram-бота')
            check('bot_configuration', control.ready(settings, 'nodera-bot'), 'Настроен токен Telegram-бота')
        if not settings['modules']['ai-agent']:
            # The idle oneshot is allowed to be stopped when AI is disabled.
            checks = [item for item in checks if item['name'] != 'service']
        import assistant_memory, assistant_personalization
        if not read_only:
            with operations.locked(api.DATA_DIR):
                assistant_memory._prune(api, settings)
        check('cache_budget', assistant_personalization.usage(api)['used_bytes'] <= assistant_memory.MAX_BYTES, 'Общий кеш персонализации не превышает 5 ГБ')
    else:
        import assistant_mihomo
        status = assistant_mihomo.request('GET', '/status', timeout=5)
        check('manager_api', isinstance(status, dict) and status.get('installed') is True, 'Локальный API Mihomo отвечает')
        version = api.mihomo_core_installed_version()
        check('core', bool(version), 'Версия установленного бинарника Mihomo определена')
        check('version_consistency', bool(version) and status.get('core_version') == version, 'Версия менеджера совпадает с установленным ядром')
        check('profiles', not status.get('recovery_required', False), 'Нет незавершённого восстановления профилей')
    failed = [item for item in checks if not item['ok']]
    return {'status': 'critical' if failed else 'healthy', 'checked_at': operations.timestamp(), 'checks': checks,
            'coverage': {'scope': 'local_checks_only', 'dependency': 'not_checked', 'functional_operation': 'not_checked',
                         'settings_mode': 'no_configuration_changes', 'cache_maintenance': 'personalization_pruning_may_run'},
            'reason': '; '.join(item['reason'] for item in failed) if failed else 'Локальные проверки пройдены; проверка из сети пользователя выполняется отдельно'}


def refresh(api):
    images = api.protocol_image_manifests(include_versions=False)
    items = {}
    for identity in ('nodera', 'mihomo'):
        image = images.get(identity, {})
        if not image.get('installed') or image.get('management', {}).get('retained'): continue
        try: result = inspect(api, identity, image)
        except Exception:
            result = {'status': 'warning', 'checked_at': operations.timestamp(), 'checks': [], 'reason': 'Автоматическая проверка недоступна; проверьте журнал приложения'}
            api.logger.warning('Component health check unavailable: %s', identity)
        items[identity] = {'id': identity, 'name': image.get('name', identity), **result}
    with operations.locked(api.DATA_DIR): operations.atomic_json(api.DATA_DIR / 'component-health.json', {'items': items})
    return items


async def monitor(api):
    while True:
        try: await asyncio.to_thread(refresh, api)
        except Exception: api.logger.warning('Automatic component health refresh unavailable')
        try:
            import diagnostic_history
            await asyncio.to_thread(diagnostic_history.observe, api)
        except Exception: api.logger.warning('Diagnostic baseline/alert sample unavailable')
        await asyncio.sleep(60)
