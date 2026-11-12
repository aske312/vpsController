"""Layered read-only checks using fixed local endpoints and configured targets."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import diagnostic_tools as reports


class CheckQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    component: str = Field(pattern='^[a-zA-Z0-9_-]{1,80}$')
    level: Literal['runtime', 'local_api', 'dependency', 'functional'] = 'local_api'
    integration_id: str | None = Field(default=None, pattern='^(ai|dns):[a-zA-Z0-9_-]{1,80}$')
    binding_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    consumer_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9:_-]{1,300}$')
    model_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^\S+$')


from diagnostic_runtime import local_http, read_budget


def gateway_contract(api, functional):
    import http.client
    import socket
    import ssl
    import diagnostic_probes
    host = getattr(api, 'PUBLIC_DOMAIN', '')
    if not host: return {'state': 'not_checked', 'error_code': 'public_panel_domain_not_configured'}
    addresses = diagnostic_probes.resolve_public(host)
    if not addresses: return {'state': 'failed', 'error_code': 'dns_resolution_failed'}
    if not functional: return diagnostic_probes.tls_exchange(host, addresses)
    connection = None
    stream = None
    try:
        stream = socket.create_connection((addresses[0], 443), timeout=3)
        stream.settimeout(3)
        secure = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
        connection = http.client.HTTPSConnection(host, timeout=3)
        connection.sock = secure
        connection.request('GET', '/', headers={'Accept': 'text/html'})
        with read_budget(secure):
            response = connection.getresponse()
            body = response.read(65536)
        return {'state': 'passed' if response.status == 200 and b'<html' in body.lower() else 'failed',
                'status': response.status, 'scope': 'configured_public_panel_HTTPS_HTML_from_VPS'}
    except (OSError, ValueError, http.client.HTTPException): return {'state': 'failed', 'error_code': 'public_panel_HTTP_contract_failed'}
    finally:
        if connection: connection.close()
        elif stream: stream.close()


def check(api, query, actor='panel'):
    definitions = api.managed_services()
    if query.component not in definitions: raise HTTPException(422, 'Выберите компонент из vps_services')
    result = reports.report('component_check')
    result['coverage'] = {'component': query.component, 'level': query.level, 'mode': 'read_only_no_settings_changes',
                          'vantage_point': 'vps', 'client_network': 'not_measured'}
    service = api.observe_service(definitions[query.component]['unit'])
    state = (service.get('execution') or {}).get('state') or service.get('runtime', {}).get('state', 'unknown')
    reports.observation(result, 'runtime', 'systemd', {key: service.get(key) for key in ('state', 'reason', 'service_type', 'runtime', 'execution')},
                        'unknown' if state == 'unknown' else 'observed')
    if query.level == 'runtime': return result
    if query.level in ('dependency', 'functional') and query.integration_id:
        import application_diagnostics as diagnostics
        if query.component == 'nodera' and query.level == 'functional' and query.integration_id.startswith('ai:'):
            import diagnostic_execution
            return diagnostic_execution.prepare(api, diagnostics.IntegrationQuery(integration_id=query.integration_id, mode='functional', model_id=query.model_id), actor, application_runtime=True)
        mode = 'dns' if query.integration_id.startswith('dns:') else 'functional' if query.level == 'functional' else 'tls'
        evidence = diagnostics.integration_probe(api, diagnostics.IntegrationQuery(integration_id=query.integration_id, mode=mode, model_id=query.model_id), actor)
        if evidence.get('confirmation_required'): return evidence
        reports.observation(result, 'integration', 'configured_dependency', evidence)
        result['coverage']['functional_scope'] = 'minimal_chat_completion' if mode == 'functional' else 'dependency_only'
        return result
    if query.level == 'functional' and query.binding_id and query.consumer_id:
        import diagnostic_probes
        evidence = diagnostic_probes.probe(api, diagnostic_probes.ProbeQuery(kind='transport', binding_id=query.binding_id, consumer_id=query.consumer_id), actor)
        reports.observation(result, 'transport_operation', 'configured_nonce_roundtrip', evidence)
        result['coverage']['functional_scope'] = 'selected_configured_transport'
        return result
    if query.component == 'gateway':
        data = gateway_contract(api, query.level == 'functional')
        reports.observation(result, 'gateway_contract', 'configured_panel_endpoint', data, data['state'])
    elif query.component in ('api', 'web'):
        data = local_http(8000 if query.component == 'api' else 3000, '/api/health' if query.component == 'api' else '/', json_response=query.component == 'api')
        reports.observation(result, 'local_contract', 'loopback_HTTP', data, data['state'])
        result['confirmed'].append({'claim': query.component + '_HTTP_contract_' + data['state'], 'evidence': ['local_contract']})
    elif query.component == 'mihomo':
        import assistant_mihomo
        try:
            data = assistant_mihomo.request('GET', '/status', timeout=5)
            valid = isinstance(data, dict) and data.get('installed') is True
            reports.observation(result, 'manager_api', 'authenticated_loopback_API', {'state': 'passed' if valid else 'failed', 'installed': valid})
        except (HTTPException, OSError, ValueError): reports.unknown(result, 'manager_api', 'local_API_unavailable')
        if query.level == 'functional':
            reports.unknown(result, 'transport_operation', 'select_binding_id_and_consumer_id_from_vps_probe_targets')
    elif query.component == 'nodera':
        import component_checks
        images = api.protocol_image_manifests(include_versions=False)
        if 'nodera' in images:
            data = component_checks.inspect(api, 'nodera', images['nodera'], read_only=True)
            reports.observation(result, 'runtime_contract', 'installed_runtime_self_checks', data)
        if query.level in ('functional', 'dependency'):
            reports.unknown(result, 'configured_dependency', 'select_integration_id_from_application_diagnostics; catalog_does_not_prove_chat')
    else:
        reports.unknown(result, 'selected_operation', 'select_configured_integration_or_transport_target_for_active_measurement')
    result['next_steps'] = [{'tool': 'vps_probe', 'arguments': {'kind': 'targets'}, 'reason': 'discover_configured_functional_targets'}]
    return result


def operation_health(api, action):
    """Record postconditions without undoing a committed action or replaying it."""
    if not hasattr(api, 'managed_services') or not hasattr(api, 'observe_service'):
        return {'state': 'unknown', 'reason': 'service_inventory_unavailable', 'checks': []}
    parts = action.split(':')
    selected = [parts[1]] if parts[0] == 'service-action' and len(parts) == 3 else [key for key in ('api', 'web') if key in api.managed_services()]
    checks = []
    for component in selected:
        definitions = api.managed_services()
        if component not in definitions: continue
        observed = api.observe_service(definitions[component]['unit'])
        state = (observed.get('execution') or {}).get('state') or observed.get('runtime', {}).get('state', 'unknown')
        expected_stop = parts[0] == 'service-action' and parts[-1] == 'stop'
        passed = state == 'stopped' if expected_stop else state in ('running', 'applied', 'succeeded', 'listening', 'waiting')
        checks.append({'component': component, 'level': 'runtime', 'state': 'unknown' if state == 'unknown' else 'passed' if passed else 'failed', 'observed': state})
        if component in ('api', 'web') and not expected_stop:
            checks.append({'component': component, 'level': 'local_api', **local_http(8000 if component == 'api' else 3000,
                '/api/health' if component == 'api' else '/', json_response=component == 'api')})
    return {'state': 'failed' if any(row['state'] == 'failed' for row in checks) else 'passed' if checks and all(row['state'] == 'passed' for row in checks) else 'unknown',
            'scope': 'post_operation_runtime_and_local_HTTP; external_function_requires_selected_target', 'checks': checks}
