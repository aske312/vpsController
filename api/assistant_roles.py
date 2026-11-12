"""One permission model for the panel, Telegram commands and model tools."""
from fastapi import HTTPException

ROLES = ('owner', 'administrator', 'moderator', 'user', 'guest')
PERMISSIONS = ('chat', 'model_select', 'web', 'memory', 'telegram_read', 'server_read', 'server_manage', 'diagnostics', 'logs_read', 'network_read', 'security_read', 'terminal_read',
               'subscription_read', 'subscription_manage', 'subscription_name', 'subscription_connections', 'subscription_devices', 'subscription_routing',
               'subscription_quota', 'subscription_speed', 'subscription_expiry', 'subscriptions_admin', 'subscriptions_create', 'subscriptions_delete',
               'modules_manage', 'routing_manage', 'dns_manage', 'recovery', 'roles_manage')
OWN_EDIT = ('subscription_name', 'subscription_connections', 'subscription_devices', 'subscription_routing')
USER_ALLOWED = {'chat', 'model_select', 'web', 'memory', 'telegram_read', 'subscription_read', 'subscription_manage', *OWN_EDIT}
FIXED = {'owner', 'administrator', 'guest'}
MIHOMO = {key for key in PERMISSIONS if key.startswith('subscription')} | {'modules_manage', 'routing_manage', 'dns_manage', 'recovery'}
VPS_PERMISSIONS = set(PERMISSIONS) - {'chat', 'model_select', 'web', 'memory', 'telegram_read'}
SERVER_PERMISSIONS = VPS_PERMISSIONS - MIHOMO
DEFAULTS = {
    'owner': list(PERMISSIONS),
    'administrator': [item for item in PERMISSIONS if item != 'roles_manage'],
    'moderator': [item for item in PERMISSIONS if item in USER_ALLOWED | {'server_read'}],
    'user': [item for item in PERMISSIONS if item in USER_ALLOWED], 'guest': [],
}
EXPANSIONS = {
    'diagnostics': ['logs_read', 'network_read', 'security_read'],
    'subscription_manage': list(OWN_EDIT),
    'subscriptions_admin': ['subscription_quota', 'subscription_speed', 'subscription_expiry', 'subscriptions_create', 'subscriptions_delete', 'modules_manage', 'routing_manage', 'dns_manage', 'recovery'],
}


def migrate_settings(settings):
    # Fixed roles saved before terminal_read was introduced remain valid.
    # Only the exact previous fixed policy is upgraded; custom roles and
    # malformed/incomplete policies must still go through strict validation.
    policies = settings.get('role_permissions', {})
    if isinstance(policies, dict):
        for name in ('owner', 'administrator'):
            grants = policies.get(name)
            if (isinstance(grants, list) and all(isinstance(key, str) for key in grants)
                    and len(grants) == len(set(grants))
                    and set(grants) == set(DEFAULTS[name]) - {'terminal_read'}):
                policies[name] = list(DEFAULTS[name])
    if settings.get('role_policy_version', 1) >= 2: return
    policies = settings.get('role_permissions', {})
    accounts = settings.get('accounts', [])
    if not isinstance(policies, dict) or not isinstance(accounts, list) or any(not isinstance(row, dict) for row in accounts):
        settings['role_policy_version'] = 2
        return
    for name, grants in list(policies.items()):
        if not isinstance(grants, list) or any(not isinstance(key, str) for key in grants): continue
        expanded = set(grants)
        for key, children in EXPANSIONS.items():
            if key in expanded: expanded.update(children)
        if name in FIXED: expanded = set(DEFAULTS[name])
        if name == 'user': expanded &= USER_ALLOWED
        policies[name] = [key for key in PERMISSIONS if key in expanded]
    for account in settings.get('accounts', []):
        if not isinstance(account.get('permission_overrides', {}), dict): continue
        overrides = dict(account.get('permission_overrides', {}))
        for key, children in EXPANSIONS.items():
            if key in overrides:
                for child in children: overrides.setdefault(child, overrides[key])
        if account.get('role') in FIXED:
            overrides = {}
            account['products'] = [] if account['role'] == 'guest' else ['assistant', 'mihomo']
        if account.get('role') == 'user': overrides = {key: enabled for key, enabled in overrides.items() if key in USER_ALLOWED}
        account['permission_overrides'] = overrides
        if account.get('role') == 'guest':
            account.update(products=[], bot_access=False, bot_chat_access=False, telegram_access=False, vps_access=False)
    settings['role_policy_version'] = 2


def products(account):
    if account.get('role') in ('owner', 'administrator') or not account.get('role') and account.get('bot_access'): return {'assistant', 'mihomo'}
    if account.get('role') == 'guest': return set()
    if account.get('products') is not None: return set(account['products'])
    result = set()
    if any(account.get(key) for key in ('bot_access', 'bot_chat_access', 'telegram_access', 'vps_access')): result.add('assistant')
    if account.get('mihomo_profile_id'): result.add('mihomo')
    if not result and account.get('role') in ('moderator', 'user'): result.add('assistant')
    return result


def role(account):
    return account.get('role') or ('administrator' if account.get('bot_access') else 'moderator' if account.get('vps_access') else 'user' if account.get('bot_chat_access') or account.get('mihomo_profile_id') or account.get('telegram_access') else 'guest')


def legacy(account):
    grants = set()
    if account.get('bot_access') or account.get('bot_chat_access'): grants.update(('chat', 'model_select'))
    if account.get('bot_access') or account.get('vps_access'): grants.update(('server_read', 'server_manage'))
    if account.get('bot_access'): grants.update(set(PERMISSIONS) - {'roles_manage', 'telegram_read'})
    if account.get('telegram_access'): grants.add('telegram_read')
    if account.get('mihomo_profile_id'): grants.update(('subscription_read', 'subscription_manage'))
    if any(account.get(key) for key in ('bot_access', 'bot_chat_access', 'vps_access', 'mihomo_profile_id')): grants.update(('web', 'memory'))
    for key, children in EXPANSIONS.items():
        if key in grants: grants.update(children)
    return grants


def account_grants(settings, account):
    name = account.get('role')
    if name in FIXED: return set(DEFAULTS[name])
    if not name: grants = legacy(account)
    else:
        grants = set(settings.get('role_permissions', {}).get(name, DEFAULTS[name]))
        for key, enabled in account.get('permission_overrides', {}).items():
            if enabled: grants.add(key)
            else: grants.discard(key)
        if name == 'user': grants &= USER_ALLOWED
        grants.discard('roles_manage')
    enabled = products(account)
    if 'mihomo' not in enabled: grants -= MIHOMO
    if 'assistant' not in enabled: grants &= MIHOMO
    return grants


def effective(settings, actor):
    if str(actor) == 'panel': return set(PERMISSIONS)
    account = next((row for row in settings.get('accounts', []) if str(row['telegram_id']) == str(actor)), None)
    return account_grants(settings, account) if account else set()


def allows(settings, actor, permission):
    return permission in effective(settings, actor)


def require(settings, actor, permission):
    if not allows(settings, actor, permission): raise HTTPException(403, 'Роль не разрешает это действие: ' + permission)


def migrated(settings, account):
    if account.get('role'): return dict(account)
    name = role(account)
    current = legacy(account)
    baseline = set(settings.get('role_permissions', {}).get(name, DEFAULTS[name]))
    overrides = {key: key in current for key in PERMISSIONS if (key in current) != (key in baseline) and key != 'roles_manage'}
    if name in FIXED: overrides = {}
    return {**account, 'role': name, 'products': sorted(products(account)), 'permission_overrides': overrides}
