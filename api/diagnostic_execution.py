"""Human-approved billable diagnostic; an uncertain result is never replayed."""
import secrets
import time
import uuid

from fastapi import HTTPException

import application_operation as operations
import assistant_control as control
import assistant_roles as roles


def prepare(api, query, actor, *, application_runtime=False):
    import assistant_commands
    settings = control.read(api)
    for permission in ('diagnostics', 'network_read', 'server_manage'): roles.require(settings, actor, permission)
    provider = next((row for row in control.providers(settings) if query.integration_id == 'ai:' + row['id']), None)
    if not provider: raise HTTPException(404, 'Настроенная интеграция не найдена')
    if not provider.get('api_key') or not provider.get('provider_url'): raise HTTPException(422, 'Интеграция не настроена')
    if not query.model_id and not provider.get('model'): raise HTTPException(422, 'Выберите model_id из каталога провайдера')
    record = {'kind': 'diagnostic_probe', 'service': 'diagnostic_probe', 'action': 'integration-functional-probe',
        'body': query.model_dump(), 'code': secrets.token_hex(4), 'operation_id': uuid.uuid4().hex,
        'expires': time.time() + 120, 'revision': settings['revision'], 'submitted': False, 'application_runtime': application_runtime}
    with operations.locked(api.DATA_DIR):
        path = assistant_commands.pending_path(api, actor)
        previous = operations.read(path)
        if previous.get('submitted') and not previous.get('completed_text'): raise HTTPException(409, 'Предыдущее действие ещё выполняется')
        operations.atomic_json(path, record)
    return {'confirmation_required': True, 'operation_id': record['operation_id'],
        'message': 'Планируется один тестовый запрос генерации с лимитом 1 выходной токен. Провайдер может списать квоту. Подтвердите в панели или /confirm.',
        'scope': 'configured_provider_minimal_chat_completion; no_tools_or_user_content'}


def confirm(api, actor, record):
    import application_diagnostics as diagnostics
    import assistant_commands
    import operation_log
    settings = control.read(api)
    for permission in ('diagnostics', 'network_read', 'server_manage'): roles.require(settings, actor, permission)
    if settings['revision'] != record['revision'] or not settings.get('mcp', {}).get('vps-controller-mcp'):
        raise HTTPException(409, 'Права или настройки изменились; подготовьте проверку заново')
    path = api.DATA_DIR / 'operations' / (record['operation_id'] + '.json')
    with operations.locked(api.DATA_DIR):
        prior = operations.read(path)
        if prior: return 'Проверка уже отправлялась. Повтор не выполняется: /operation ' + record['operation_id']
        value = {'id': record['operation_id'], 'action': 'integration-functional-probe', 'state': 'running',
                 'started_at': operations.timestamp(), 'diagnostics_version': 2, 'configuration_revision': settings['revision'],
                 'functional_scope': 'installed_runtime_provider_adapter' if record.get('application_runtime') else 'upstream_chat_completion'}
        operations.atomic_json(path, value)
    try:
        if record.get('application_runtime'):
            query = diagnostics.IntegrationQuery.model_validate(record['body'])
            output = control.runtime_request(api, '--functional-check', {'provider_id': query.integration_id.split(':')[1],
                'model': query.model_id, 'actor': actor, 'settings_revision': record['revision']})
            state = 'passed' if output.get('ok') and output.get('result', {}).get('provider_response') else 'failed'
            report = {'scope': 'installed_runtime_provider_adapter_without_tools', 'state': state, 'max_completion_tokens': 1}
        else:
            report = diagnostics.integration_probe(api, diagnostics.IntegrationQuery.model_validate(record['body']), actor, confirmed=True)
            state = report['observations'][0]['data'].get('generation', {}).get('state', 'unknown')
        value.update(state='succeeded' if state == 'passed' else 'failed' if state == 'failed' else 'unknown',
                     diagnostic_report=report, health_check={'state': state, 'checks': [{'component': 'nodera' if record.get('application_runtime') else 'ai_provider', 'level': 'functional', 'state': state}]})
    except Exception:
        value.update(state='unknown', health_check={'state': 'unknown', 'checks': []})
    value.update(updated_at=operations.timestamp(), finished_at=operations.timestamp())
    text = 'Функциональная проверка: ' + value['state'] + '. Отчёт: /operation ' + value['id']
    with operations.locked(api.DATA_DIR):
        operations.atomic_json(path, value)
        operation_log.append(api.DATA_DIR, {**value, 'stage': 'functional_check', 'component': 'nodera'})
        pending = assistant_commands.pending_path(api, actor)
        current = operations.read(pending)
        if current.get('operation_id') == value['id']: operations.atomic_json(pending, {**current, 'completed_text': text})
    return text
