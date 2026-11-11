"""Bounded administrator diagnostics. Read-only tools never execute model commands."""
import re
from fastapi import HTTPException
import assistant_control as control


def sanitize(api, value):
    settings = control.read(api)
    secrets = [settings.get('control_token'), settings.get('api_key'), settings.get('bot_token'),
               settings.get('telegram_api_hash'), getattr(api, 'ADMIN_PASSWORD', '')]
    secrets += [row.get('api_key') for row in settings.get('providers', [])]
    secrets += [row.get('session') for row in settings.get('accounts', [])]
    remaining, nodes = 9000, 150
    safe_fields = {"admin_password_strong", "password_authentication", "secrets_protected", "secrets_mode"}
    def scrub(item, depth=0):
        nonlocal remaining, nodes
        nodes -= 1
        if nodes < 0: return "[bounded]"
        if depth > 8: return '[bounded]'
        if isinstance(item, dict):
            return {str(key): scrub(entry, depth + 1) for key, entry in list(item.items())[:40]
                    if str(key) in safe_fields or not re.search(r'(?i)(?:password|token|secret|private.?key|api.?key|session|credential|authorization)', str(key))}
        if isinstance(item, list): return [scrub(entry, depth + 1) for entry in item[:80]]
        if isinstance(item, str):
            for secret in secrets:
                if secret: item = item.replace(secret, '[redacted]')
            item = re.sub(r'(?i)(Bearer\s+|(?:password|token|secret|api[_-]?key)[\s=:]+)[^\s,;]+', r'\1[redacted]', item)
            item = re.sub(r'bot[0-9]{5,}:[A-Za-z0-9_-]{20,}', 'bot[redacted]', item)
            text = item[:min(1200, max(0, remaining))]
            remaining -= len(text)
            return text
        return item
    return scrub(value)


def call(api, name, arguments, actor):
    settings = control.read(api)
    user = next((row for row in settings['accounts'] if str(row['telegram_id']) == actor), {})
    if actor != 'panel' and not user.get('bot_access'):
        raise HTTPException(403, 'Журналы, сеть и аудит доступны администратору')
    if name == 'vps_network':
        return sanitize(api, api.get_network(None))
    if name == 'vps_security':
        return sanitize(api, api.security(None))
    if name == 'vps_logs':
        service = arguments.get('service') or 'application'
        lines = max(20, min(int(arguments.get('lines') or 60), 120))
        if service == 'application': return sanitize(api, api.application_logs(lines, None))
        if service in ('ssh', 'firewall', 'system'): return sanitize(api, api.security_logs(service, lines, None))
        definition = api.managed_services().get(service)
        if not definition: raise HTTPException(422, 'Выберите службу из vps_services или application, ssh, firewall, system')
        output = api.run('journalctl', '-r', '-u', definition['unit'], '-n', str(lines), '--no-pager', '-o', 'short-iso', timeout=12)
        return sanitize(api, {'service': service, 'lines': output.splitlines()})
    if name == 'vps_diagnostics':
        security = api.security(None)
        findings = []
        def check(identity, observed, risk, advice):
            findings.append({'id': identity, 'state': 'unknown' if observed is None else 'passed' if observed else 'risk', 'risk': risk, 'recommendation': advice})
        app = security.get('application_security', {})
        for key, risk in [('admin_password_strong', 'Слабый пароль панели'), ('cors_restricted', 'Избыточный CORS'), ('secrets_protected', 'Небезопасные права файлов с секретами')]:
            check(key, app.get(key), risk, 'Проверьте настройки в разделе Безопасность')
        firewall = security.get('firewall', {})
        check('panel_access', firewall.get('panel_access', {}).get('consistent'), 'Доступ к панели не соответствует настройкам', 'Проверьте правила firewall и режим доступа')
        ssh = security.get('ssh', {})
        check('ssh_password', None if ssh.get('password_authentication') not in ('yes', 'no') else ssh['password_authentication'] == 'no', 'SSH допускает вход по паролю', 'Используйте ключи SSH и настройку доступа в панели')
        updates = security.get('updates', {})
        check('security_updates', None if updates.get('security') is None else updates['security'] == 0, 'Ожидаются обновления безопасности ОС', 'Проверьте обновления ОС и отчёт безопасного обновления')
        components = control.control_tool(api, 'vps_components', actor=actor)
        return sanitize(api, {'scope': 'Пассивная диагностика приложения и конфигурации; не полный аудит CVE и не проверка эксплуатации',
                             'findings': findings, **components, 'dependencies': api.application_dependency_versions(),
                             'logs': api.application_logs(40, None), 'checked_at': control.operations.timestamp()})
    raise HTTPException(404, 'Диагностика не найдена')
