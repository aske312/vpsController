"""PRO-managed, expiring client test kits and sanitized result collection."""
import base64
from contextlib import ExitStack
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import zipfile
import ipaddress
import ssl
from relay_client import AgentAPIError

import network_probe
import relay_measurement
import relay_test_peer
from relay_test_cleanup import atomic, cleanup

TTL = 600
ACTIVE = {'preparing', 'awaiting_client', 'processing'}


@contextmanager
def provisioning_lock(api):
    import application_operation
    deadline = time.monotonic() + 20
    while True:
        lock = application_operation.mutation_lock(api.DATA_DIR)
        try:
            lock.__enter__(); break
        except application_operation.OperationConflict:
            if time.monotonic() >= deadline: raise
            time.sleep(0.1)
    try: yield
    finally: lock.__exit__(None, None, None)


def directory(api, identity):
    if not re.fullmatch(r'[0-9a-f]{32}', identity): raise ValueError('Invalid test id')
    return api.DATA_DIR/'relay-tests'/identity


def read_public(api, identity):
    value = json.loads((directory(api, identity)/'public.json').read_text())
    if value.get('id') != identity: raise ValueError('Invalid test state')
    if value.get('cleanup_state') in ('failed', 'pending'):
        journal = directory(api, identity)/'cleanup.json'
        record = json.loads(journal.read_text()) if journal.exists() else {}
        if record.get('cleaned'):
            value = {**value, 'cleanup_state': 'pending' if record.get('measurement') and not record.get('remote_cleaned') else 'cleaned'}
    if value['state'] in ACTIVE and time.time() > value['expires_at']:
        value = {**value, 'state': 'expired', 'reason': 'test_expired'}
    return value


def attach(api, result):
    latest = {}
    for path in (api.DATA_DIR/'relay-tests').glob('*/public.json'):
        try:
            value = read_public(api, path.parent.name)
            key = (value['host_id'], value['route_id'])
            if key not in latest or value['created_at'] > latest[key]['created_at']: latest[key] = value
        except (OSError, ValueError, KeyError, TypeError): continue
    for host in result['hosts']:
        for route in host['routes']:
            value = latest.get((host['id'], route['id']))
            if not value: continue
            if value['state'] in ('ready', 'warning', 'failed'):
                try:
                    private = api.relay_control().host(api.relay_control().read(), host['id'])
                    plan = api.prepare_relay_media_probe(route, private, value['consumer_id'])
                    current = network_probe.plan_seal(plan, network_probe.evidence_key(api.DATA_DIR))
                    if host['state'] != 'connected' or route['state'] != 'listening' or current != value.get('plan_seal') or time.time() - value.get('finished_at', value['created_at']) > 3600:
                        value = {**value, 'state': 'unknown', 'reason': 'configuration_changed_or_stale'}
                except Exception: value = {**value, 'state': 'unknown', 'reason': 'configuration_changed_or_stale'}
            route['device_test'] = {key: item for key, item in value.items() if key != 'plan_seal'}


def start(api, payload, identity):
    control = api.relay_control(); registry = control.checked(payload['expected_revision'])
    host = control.host(registry, payload['host_id'])
    route = next(item for item in registry['routes'] if item['id'] == payload['route_id'] and item['host_id'] == host['id'])
    root = directory(api, identity)
    if (root/'public.json').exists(): return
    for path in (api.DATA_DIR/'relay-tests').glob('*/public.json'):
        previous = read_public(api, path.parent.name)
        if previous['host_id'] == host['id'] and previous['route_id'] == route['id'] and previous['state'] in ACTIVE:
            raise api.HTTPException(status_code=409, detail='Для перенаправления уже запущен тест с устройства. Завершите или отмените его в ROUTES.')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    relay_test_peer.schedule_cleanup(api, root, TTL + 60)
    public = {'id': identity, 'host_id': host['id'], 'route_id': route['id'], 'consumer_id': payload['consumer_id'],
              'state': 'preparing', 'reason': 'preparing', 'created_at': time.time(), 'expires_at': time.time() + TTL,
              'vantage_point': 'client', 'verification': 'client_report_with_udp_witness'}
    atomic(root/'public.json', public); atomic(root/'request.json', payload)
    durable = api.DATA_DIR/'recovery'/'relay-tests'/'cleanup.py'
    try:
        subprocess.run(['systemd-run', '--quiet', '--collect', '--service-type=exec', '--unit=vps-relay-device-test-' + identity,
                        '--property=EnvironmentFile=' + str(api.ENV_FILE), '--property=RuntimeMaxSec=' + str(TTL + 30),
                        '--property=UMask=0077', '--property=ExecStopPost=' + sys.executable + ' ' + str(durable) + ' ' + str(root/'cleanup.json'),
                        sys.executable, str(Path(__file__)), identity], capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        # The command outcome can be unknown. Do not replay it or remove another live worker's lease.
        raise api.HTTPException(status_code=503, detail='Запуск теста устройства не подтверждён. Проверьте его состояние в ROUTES перед повтором.') from None


def kit(api, host_id, route_id, identity):
    public = read_public(api, identity)
    if public['host_id'] != host_id or public['route_id'] != route_id or public['state'] != 'awaiting_client':
        raise ValueError('Test kit unavailable')
    raw = (directory(api, identity)/'kit.zip').read_bytes()
    return {'filename': 'relay-test-' + identity[:8] + '.zip', 'content_base64': base64.b64encode(raw).decode()}


def accept(api, payload):
    root = directory(api, payload['test_id']); public = read_public(api, payload['test_id'])
    if public['host_id'] != payload['host_id'] or public['route_id'] != payload['route_id'] or public['state'] != 'awaiting_client':
        raise ValueError('Test is not accepting results')
    report = payload['report']
    private = json.loads((root/'private.json').read_text())
    if report['id'] != public['id'] or not hmac.compare_digest(report['token'], private['token']):
        raise ValueError('Wrong test report token')
    if (root/'report.json').exists():
        if json.loads((root/'report.json').read_text()) != report: raise ValueError('Test report already submitted')
        return
    public.update(state='processing', reason='processing_report')
    atomic(root/'public.json', public)
    atomic(root/'report.json', report)


def cancel(api, payload):
    root = directory(api, payload['test_id']); public = read_public(api, payload['test_id'])
    if public['host_id'] != payload['host_id'] or public['route_id'] != payload['route_id']: raise ValueError('Wrong test')
    atomic(root/'cancel.json', {'cancelled': True})


def build_kit(root, value, origin):
    # No admin credentials, Agent API token, origin address or callback URL enters a kit.
    origins = [origin] if isinstance(origin, str) else origin
    encoded = json.dumps(value).encode()
    for address in origins:
        if address.encode() in encoded: raise ValueError('Origin address in client kit')
    def inspect(item):
        if isinstance(item, dict):
            for child in item.values(): inspect(child)
        elif isinstance(item, list):
            for child in item: inspect(child)
        elif isinstance(item, str) and '-----BEGIN CERTIFICATE-----' in item:
            for pem in re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', item, re.S):
                der = ssl.PEM_cert_to_DER_cert(pem)
                if any(ipaddress.ip_address(address).packed in der or address.encode() in der for address in origins):
                    raise ValueError('Origin address in client certificate')
    inspect(value)
    buffer = io.BytesIO()
    instructions = ('1. Extract this archive.\n2. Run: python runner.py --core PATH_TO_' + value['engine'].upper() + '\n'
                    '3. Upload result.json in PRO / ROUTES before expiry.\n'
                    'This does not change system VPN/TUN/routes. Keep test.json private like a connection profile.\n'
                    'UDP measurements use a separate diagnostic port over the same Relay and VPS, not the encrypted protocol port.\n')
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('runner.py', Path(__file__).with_name('relay_test_runner.py').read_bytes())
        archive.writestr('test.json', json.dumps(value))
        archive.writestr('README.txt', instructions)
    path = root/'kit.zip'; path.write_bytes(buffer.getvalue()); os.chmod(path, 0o600)


def serve(api, identity):
    root = directory(api, identity); public = read_public(api, identity)
    stage = 'export_unavailable'
    measurement = None
    try:
        with ExitStack() as stack:
            with provisioning_lock(api):
                payload = json.loads((root/'request.json').read_text())
                control = api.relay_control(); registry = control.checked(payload['expected_revision'])
                host = control.host(registry, payload['host_id'])
                route = next(item for item in registry['routes'] if item['id'] == payload['route_id'] and item['host_id'] == host['id'])
                observed = control.client(host).request('GET', '/v1/routes')
                if not any(item['id'] == route['id'] and item['state'] == 'listening' and api.relay_manager.same_route(item, route) for item in observed['items']):
                    raise ValueError('Relay listener unavailable')
                base = api.prepare_relay_media_probe(route, host, payload['consumer_id'])
                if hasattr(control, 'validate_client_address'):
                    control.validate_client_address(host['id'], base.address)
                public['plan_seal'] = network_probe.plan_seal(base, network_probe.evidence_key(api.DATA_DIR, create=True))
                stage = 'temporary_peer_unavailable'
                plan = stack.enter_context(relay_test_peer.disposable_plan(api, route, host, payload['consumer_id'], root, scheduled=True))
                stage = 'measurement_unavailable'
                measurement = relay_measurement.Measurement(api, route, host, root, TTL)
                stack.enter_context(measurement)
                private = {'id': identity, 'token': secrets.token_hex(32), 'expires_at': public['expires_at'],
                           'engine': plan.engine, 'client': plan.client, 'measurement': measurement.settings()}
                stage = 'kit_unavailable'
                origin_ips = {api.PUBLIC_IPV4}
                for address in (getattr(api, 'PUBLIC_IPV6', ''), getattr(api, 'PUBLIC_IP', '')):
                    try: origin_ips.add(str(ipaddress.ip_address(address)))
                    except ValueError: pass
                if hasattr(api, 'server_addresses'):
                    origin_ips.update(item['address'] for item in api.server_addresses.inventory(api.DATA_DIR, tuple(origin_ips))['items'])
                atomic(root/'private.json', private); build_kit(root, private, origin_ips)
                public.update(state='awaiting_client', reason='awaiting_client')
                atomic(root/'public.json', public)
            while time.time() < public['expires_at']:
                if (root/'cancel.json').exists():
                    public.update(state='cancelled', reason='test_cancelled'); break
                if (root/'report.json').exists():
                    stage = 'report_invalid'
                    report = api.RelayDeviceReport.model_validate(json.loads((root/'report.json').read_text())).model_dump()
                    if not hmac.compare_digest(report['token'], private['token']) or report['id'] != identity: raise ValueError('Wrong report')
                    loss = measurement.result(report['measurement'])
                    media = report['media']
                    if media['state'] == 'ready' and (media.get('reason') != 'media_verified' or media.get('verified_bytes') != 4372373 or media.get('range_checks') != 3):
                        raise ValueError('Invalid media success report')
                    after = api.prepare_relay_media_probe(route, host, payload['consumer_id'])
                    remote = control.client(host).request('GET', '/v1/routes')
                    valid = public['plan_seal'] == network_probe.plan_seal(after, network_probe.evidence_key(api.DATA_DIR)) and any(item['id'] == route['id'] and item['state'] == 'listening' and api.relay_manager.same_route(item, route) for item in remote['items'])
                    state = 'ready' if media['state'] == 'ready' and loss['state'] == 'ready' else 'warning' if media['state'] == 'ready' else media['state']
                    public.update(state=state if valid else 'unknown', reason=media['reason'] if valid else 'configuration_changed', media=media, measurement=loss)
                    break
                time.sleep(0.25)
            else: public.update(state='expired', reason='test_expired')
    except Exception:
        public.update(state='unknown', reason=stage)
    finally:
        ok = cleanup(root/'cleanup.json')
        (root/'report.json').unlink(missing_ok=True)
        public['cleanup_state'] = 'failed' if not ok else 'pending' if measurement is not None and measurement.cleanup_pending else 'cleaned'
        public['finished_at'] = time.time()
        atomic(root/'public.json', public)


def recover(api):
    import application_operation
    try:
        with application_operation.mutation_lock(api.DATA_DIR):
            _recover(api)
    except application_operation.OperationConflict: pass


def _recover(api):
    """Recover leases after expiry/reboot; live client workers survive an API restart."""
    remote_budget = 2
    for path in sorted((api.DATA_DIR/'relay-tests').glob('*/cleanup.json'), key=lambda item: item.stat().st_mtime_ns):
        try:
            record = json.loads(path.read_text())
            if record.get('measurement') and record.get('cleaned') and not record.get('remote_cleaned') and remote_budget:
                remote_budget -= 1
                record['remote_cleaned'] = remote_cleanup_confirmed(api, record['measurement'])
                record['remote_checked_at'] = time.time()
                atomic(path, record)
            public_path = path.parent/'public.json'
            expired = record.get('expires_at', 0) <= time.time()
            if public_path.exists() and not expired:
                public = json.loads(public_path.read_text())
                if public.get('state') in ACTIVE:
                    if public.get('state') == 'preparing' and time.time() - public['created_at'] < 30: continue
                    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'vps-relay-device-test-' + path.parent.name], capture_output=True, timeout=5)
                    expired = active.returncode != 0
            if expired:
                if not record.get('cleaned'): cleanup(path)
                if public_path.exists():
                    public = json.loads(public_path.read_text())
                    original = dict(public)
                    cleaned = json.loads(path.read_text()).get('cleaned')
                    public['cleanup_state'] = 'failed' if not cleaned else 'pending' if record.get('measurement') and not record.get('remote_cleaned') else 'cleaned'
                    if public.get('state') in ACTIVE:
                        public.update(state='expired', reason='test_expired', finished_at=time.time())
                    if public != original: atomic(public_path, public)
        except (OSError, ValueError, subprocess.SubprocessError): continue


def remote_cleanup_confirmed(api, measurement):
    try:
        control = api.relay_control(); host = control.host(control.read(), measurement['host_id'])
        client = control.client(host); client.timeout = 2
        value = client.request('GET', '/v1/measurements/' + measurement['id'])
        return value.get('id') == measurement['id'] and value.get('route_id') == measurement['route_id'] and value.get('state') == 'expired'
    except AgentAPIError as exc: return exc.status == 404
    except Exception: return False


def execute_server(api, route, host, consumer_id):
    import uuid
    from relay_test_runner import udp_measurement
    root = api.DATA_DIR/'relay-tests'/uuid.uuid4().hex
    result = {'state': 'unknown', 'reason': 'client_unavailable'}
    measurement = None
    relay_test_peer.schedule_cleanup(api, root)
    try:
        with relay_test_peer.disposable_plan(api, route, host, consumer_id, root, scheduled=True) as plan:
            result = api.execute_relay_media_probe(plan, directory=root)
            try:
                measurement = relay_measurement.Measurement(api, route, host, root)
                with measurement:
                    result['measurement'] = measurement.result(udp_measurement(measurement.settings()))
            except (OSError, ValueError, api.relay_manager.AgentAPIError, subprocess.SubprocessError):
                result['measurement'] = {'state': 'unknown', 'reason': 'measurement_unavailable'}
    except (OSError, ValueError, api.HTTPException, subprocess.SubprocessError):
        result = {'state': 'unknown', 'reason': 'client_unavailable'}
    finally:
        ok = cleanup(root/'cleanup.json')
        result['cleanup_state'] = 'failed' if not ok else 'pending' if measurement is not None and measurement.cleanup_pending else 'cleaned'
        result['measurement_id'] = root.name
    return result


if __name__ == '__main__':
    import main as api
    if os.geteuid() != 0: raise SystemExit('Test worker requires root')
    serve(api, sys.argv[1])
