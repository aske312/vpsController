"""Bounded non-secret connection observations, bound to saved configuration revision."""
import re

import application_operation as operations


def clean_quota(value):
    if not isinstance(value, dict): return None
    result = {}
    for key in ('unlimited', 'model_limits_enabled', 'allowed_models_truncated'):
        if type(value.get(key)) is bool: result[key] = value[key]
    for key in ('remaining', 'account_remaining', 'used', 'granted', 'expires_at', 'allowed_models_count'):
        item = value.get(key)
        if type(item) is int and abs(item) <= 10**15 and (key in ('remaining', 'account_remaining') or item >= 0): result[key] = item
    models = value.get('allowed_models')
    if isinstance(models, list): result['allowed_models'] = [item for item in models[:100] if isinstance(item, str) and re.fullmatch(r'[^\s]{1,128}', item)]
    return result or None


def clean_info(value):
    if not isinstance(value, dict): return None
    info = {name: item for name, item in value.items() if name in ('checked_at', 'catalog_latency_ms', 'models_count', 'catalog_http_status') and type(item) in (int, float) and 0 <= item <= 10**15}
    if not 0 < info.get('checked_at', 0) <= 253402300799 or info.get('catalog_http_status') != 200 or type(info.get('models_count')) is not int or not 0 <= info['models_count'] <= 10000: return None
    return info


def read(api, settings):
    path = api.DATA_DIR / 'assistant/ai-agent/provider-observations.json'
    try:
        if path.is_symlink() or path.exists() and path.stat().st_size > 256 * 1024: return {}
        stored = operations.read(path)
    except (OSError, ValueError): return {}
    ids = {row['id'] for row in settings.get('providers', [])}
    if not ids:
        import assistant_control
        ids = {row['id'] for row in assistant_control.providers(settings)}
    result = {}
    for key, value in stored.items():
        if key not in ids or not isinstance(value, dict) or value.get('revision') != settings.get('revision') or not isinstance(value.get('provider_info'), dict): continue
        info = clean_info(value['provider_info'])
        if not info: continue
        row = {'provider_info': info, 'revision': value['revision']}
        if 'quota' in value: row['quota'] = clean_quota(value['quota'])
        if type(value.get('quota_checked_at')) in (int, float) and 0 < value['quota_checked_at'] <= 253402300799: row['quota_checked_at'] = value['quota_checked_at']
        result[key] = row
    return result


def save(api, settings, identity, result, *, quota_requested):
    import assistant_control
    if not re.fullmatch('[0-9a-f]{32}', identity): return
    path = api.DATA_DIR / 'assistant/ai-agent/provider-observations.json'
    try:
        with operations.locked(api.DATA_DIR):
            current = assistant_control._read(api)
            if current.get('revision') != settings.get('revision'): return
            rows = read(api, current)
            prior = rows.get(identity, {})
            info = clean_info(result.get('provider_info'))
            if not info: return
            row = {'revision': current['revision'], 'provider_info': info}
            if quota_requested:
                row.update(quota=clean_quota(result.get('quota')), quota_checked_at=result.get('quota_checked_at', info['checked_at']))
            elif 'quota' in prior:
                row.update(quota=prior['quota'], quota_checked_at=prior.get('quota_checked_at'))
            rows[identity] = row
            operations.atomic_json(path, rows)
    except (OSError, ValueError): pass
