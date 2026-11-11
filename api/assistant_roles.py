"""One permission model for the panel, Telegram commands and model tools."""
from fastapi import HTTPException

ROLES = ('owner', 'administrator', 'moderator', 'user', 'guest')
PERMISSIONS = ('chat', 'model_select', 'web', 'memory', 'telegram_read', 'server_read', 'server_manage', 'diagnostics', 'subscription_read', 'subscription_manage', 'subscriptions_admin', 'roles_manage')
DEFAULTS = {
    'owner': list(PERMISSIONS),
    'administrator': [item for item in PERMISSIONS if item != 'roles_manage'],
    'moderator': ['chat', 'model_select', 'web', 'memory', 'telegram_read', 'server_read', 'subscription_read', 'subscription_manage'],
    'user': ['chat', 'model_select', 'web', 'memory', 'telegram_read', 'subscription_read', 'subscription_manage'],
    'guest': ['chat', 'web'],
}


def role(account):
    return account.get('role') or ('administrator' if account.get('bot_access') else 'moderator' if account.get('vps_access') else 'user' if account.get('bot_chat_access') or account.get('mihomo_profile_id') or account.get('telegram_access') else 'guest')


def legacy(account):
    grants = set()
    if account.get('bot_access') or account.get('bot_chat_access'): grants.update(('chat', 'model_select'))
    if account.get('bot_access') or account.get('vps_access'): grants.update(('server_read', 'server_manage'))
    if account.get('bot_access'): grants.update(('diagnostics', 'subscriptions_admin', 'subscription_read', 'subscription_manage'))
    if account.get('telegram_access'): grants.add('telegram_read')
    if account.get('mihomo_profile_id'): grants.update(('subscription_read', 'subscription_manage'))
    if any(account.get(key) for key in ('bot_access', 'bot_chat_access', 'vps_access', 'mihomo_profile_id')): grants.update(('web', 'memory'))
    return grants


def account_grants(settings, account):
    if not account.get('role'): return legacy(account)
    if account['role'] == 'owner': return set(PERMISSIONS)
    grants = set(settings.get('role_permissions', {}).get(account['role'], DEFAULTS[account['role']]))
    for key, enabled in account.get('permission_overrides', {}).items():
        if enabled: grants.add(key)
        else: grants.discard(key)
    grants.discard('roles_manage')
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
    return {**account, 'role': name, 'permission_overrides': overrides}
