"""Small, bounded probes against existing configuration; no arbitrary URLs."""
from collections import OrderedDict
import ipaddress
import socket
import ssl
import subprocess
import threading
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import application_operation as operations
import assistant_mihomo
import network_routes
import network_probe
from diagnostic_tools import report, observation, unknown

_active = threading.Lock()
_guard = threading.Lock()
_recent = OrderedDict()


class ProbeQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['targets', 'dns', 'tls', 'mihomo_api', 'transport', 'service']
    provider_id: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')
    binding_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    consumer_id: str | None = Field(default=None, max_length=300)
    service: str | None = Field(default=None, pattern='^[a-zA-Z0-9_-]{1,80}$')


def resolve_public(host):
    try:
        direct = ipaddress.ip_address(host)
        return [str(direct)] if direct.is_global else []
    except ValueError: pass
    # OS resolver in a killable subprocess, so a wedged resolver cannot leak threads.
    value = subprocess.run(['getent', 'ahosts', host], capture_output=True, text=True, timeout=4, check=False)
    addresses = []
    for line in value.stdout.splitlines()[:64]:
        try: address = ipaddress.ip_address(line.split()[0])
        except (ValueError, IndexError): continue
        if address.is_global and str(address) not in addresses: addresses.append(str(address))
    return addresses[:4]


def tls_exchange(host, addresses, *, port=443, context=None):
    """Connect to the resolved IP, verify the configured host, never re-resolve."""
    context = context or ssl.create_default_context()
    started = time.monotonic()
    certificate_error = False
    for address in addresses[:4]:
        remaining = 5 - (time.monotonic() - started)
        if remaining <= 0: break
        try:
            with socket.create_connection((address, port), timeout=min(2, remaining)) as stream:
                stream.settimeout(max(0.05, 5 - (time.monotonic() - started)))
                with context.wrap_socket(stream, server_hostname=host) as secure:
                    return {'state': 'passed', 'method': 'verified_tls_handshake', 'tls_version': secure.version(),
                            'latency_ms': round((time.monotonic() - started) * 1000, 2), 'certificate_verified': True}
        except ssl.SSLCertVerificationError: certificate_error = True
        except (OSError, ssl.SSLError): pass
    return {'state': 'failed' if addresses else 'unknown', 'method': 'verified_tls_handshake',
            'error_code': 'tls_certificate_invalid' if certificate_error else 'tls_connection_failed' if addresses else 'no_public_address',
            'certificate_verified': False}


def binding(api, identity):
    if not identity: raise HTTPException(422, 'Укажите binding_id существующего маршрута из vps_network')
    value = next((row for row in api.read_route_registry()['bindings'] if row['id'] == identity and row.get('state') == 'active'), None)
    if not value: raise HTTPException(404, 'Активный маршрут не найден')
    return value


def probe(api, query, actor='panel'):
    # Validate all identifiers against current inventory before any network activity.
    if query.kind == 'targets':
        result = report('vps_probe')
        import re
        consumers = api.network_consumers()
        bindings = []
        registry = api.read_route_registry()
        active = [row for row in registry['bindings'] if row.get('state') == 'active']
        for row in active[:64]:
            identities = [item['id'] for item in consumers if item.get('kind') == row['kind'] and item.get('address') == row['address']
                          and isinstance(item.get('id'), str) and re.fullmatch(r'[a-zA-Z0-9:_-]{1,300}', item['id'])]
            bindings.append({'binding_id': row['id'], 'kind': row['kind'], 'consumer_ids': identities[:8],
                             'consumers_truncated': len(identities) > 8, 'transport_support': 'requires_compatible_live_consumer'})
        providers = [{'provider_id': row['id']} for row in api.dns_provider_list()[:32]
                     if isinstance(row.get('id'), str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', row['id'])]
        observation(result, 'targets', 'configured_inventory', {'dns_providers': providers, 'bindings': bindings,
                    'services': list(api.managed_services())[:64]})
        result['coverage'] = {'bindings_truncated': len(active) > 64, 'scope': 'inventory_only_no_active_measurement'}
        result['next_steps'] = [{'tool': 'vps_probe', 'arguments': {'kind': 'mihomo_api'}, 'reason': 'test_manager_reachability'}]
        return result
    target = binding(api, query.binding_id) if query.kind in ('tls', 'transport') else None
    provider = None
    if query.kind == 'dns':
        provider = next((row for row in api.dns_provider_list() if row['id'] == query.provider_id), None)
        if provider is None: raise HTTPException(422, 'Выберите provider_id из настроенных DNS-профилей')
    definitions = api.managed_services() if query.kind == 'service' else {}
    if query.kind == 'service' and query.service not in definitions: raise HTTPException(422, 'Выберите service из vps_services')
    if query.kind == 'tls' and target['kind'] == 'udp_relay': raise HTTPException(422, 'UDP-маршрут не является TLS endpoint')
    if query.kind == 'transport' and not query.consumer_id: raise HTTPException(422, 'Для транспортной проверки укажите конкретный consumer_id из vps_network')
    if not _active.acquire(blocking=False): raise HTTPException(429, 'Другая диагностическая проверка ещё выполняется')
    try:
        with _guard:
            key = (actor, query.kind)
            now = time.monotonic()
            if now - _recent.get(key, -100) < 3: raise HTTPException(429, 'Повторите проверку через три секунды')
            _recent[key] = now
            _recent.move_to_end(key)
            while len(_recent) > 256: _recent.popitem(last=False)
        result = report('vps_probe')
        result['coverage'] = {'kind': query.kind, 'vantage_point': 'server', 'client_network': 'not_measured'}
        try:
            if query.kind == 'dns':
                raw = api.check_dns_provider(provider)
                data = {key: raw.get(key) for key in ('udp_ok', 'tcp_ok', 'doh_ok', 'udp_ms', 'tcp_ms', 'doh_ms')}
                data.update(provider_id=query.provider_id, test_domain='example.com', method='validated_DNS_answer')
                state = 'passed' if any(data.get(key) is True for key in ('udp_ok', 'tcp_ok', 'doh_ok')) else 'failed'
            elif query.kind == 'tls':
                port = target.get('listen_port') or 443
                if type(port) is not int or not 1 <= port <= 65535: raise ValueError('Invalid configured port')
                data = tls_exchange(target['address'], resolve_public(target['address']), port=port)
                state = data.pop('state')
                data['binding_id'] = query.binding_id
                data['scope'] = 'TLS_only_not_transport_or_outbound'
            elif query.kind == 'mihomo_api':
                value = assistant_mihomo.request('GET', '/status', timeout=5)
                if not isinstance(value, dict) or not value: raise ValueError('Invalid manager response')
                data, state = {'method': 'authenticated_loopback_HTTP', 'scope': 'manager_API_only'}, 'passed'
            elif query.kind == 'service':
                raw = api.observe_service(definitions[query.service]['unit'])
                runtime = raw.get('runtime', {}).get('state', 'unknown')
                execution = raw.get('execution', {}).get('state', runtime)
                data = {'service': query.service, 'runtime_state': runtime, 'execution': raw.get('execution'),
                        'service_type': raw.get('service_type', 'unknown'), 'scope': 'systemd_runtime_or_latest_oneshot_execution_only'}
                state = 'failed' if execution in ('error', 'stopped') else 'passed' if execution in ('running', 'applied', 'succeeded', 'listening', 'waiting') else 'unknown'
            else:
                # Reuse the application's nonce round-trip, without saving readiness
                # or changing live routing. Mutation admission keeps credentials stable.
                with operations.short_mutation(api.DATA_DIR):
                    plan, sources = api.prepare_network_probe(target, query.consumer_id)
                    if plan.consumer_id != query.consumer_id: raise network_probe.ProbeUnavailable('Consumer mismatch')
                    paths = api.network_probe_sources(plan.producer, target['kind'], plan.consumer_id)
                    before = network_probe.source_revision(paths)
                    raw = api.execute_network_probe(plan)
                    after = network_probe.source_revision(paths)
                    current = api.read_route_registry()
                    fresh = next((row for row in current['bindings'] if row['id'] == target['id']), None)
                    unchanged = fresh == target and before == after and before == sources
                    expected = f'{plan.producer}_nonce_v1'
                    state = 'passed' if unchanged and raw.get('state') == 'verified' and raw.get('method') == expected else 'failed' if raw.get('state') == 'failed' and unchanged else 'unknown'
                    data = {'binding_id': target['id'], 'consumer_id': query.consumer_id, 'revision': target['desired_revision'],
                            'method': expected, 'configuration_unchanged': unchanged, 'scope': 'configured_transport_nonce_roundtrip',
                            'client_network': 'not_measured'}
            observation(result, 'probe', 'active_' + query.kind, data, state)
            if state in ('passed', 'failed'):
                result['confirmed'].append({'claim': query.kind + '_probe_' + state, 'evidence': ['probe'], 'scope': data.get('scope', 'selected_DNS_provider_from_VPS')})
            else: result['missing_data'].append({'source': query.kind, 'reason': 'measurement_unavailable_or_changed_configuration'})
        except (OSError, ValueError, subprocess.SubprocessError, HTTPException, operations.OperationConflict) as exc:
            if isinstance(exc, HTTPException) and exc.status_code in (403, 409, 422, 429): raise
            unknown(result, query.kind, 'probe_unavailable')
        result['next_steps'] = [{'tool': 'vps_incident', 'arguments': {}, 'reason': 'compare_server_measurement_with_incident_evidence'}]
        return result
    finally: _active.release()
