"""CI release discovery and durable, single-send Agent update requests."""
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.request import Request, urlopen

from application_operation import atomic_json
from relay_manager import status_snapshot
from relay_client import AgentAPIError

RELEASE = 'https://api.github.com/repos/aske312/vpsController/releases/tags/agent-latest'
PREFIX = 'https://github.com/aske312/vpsController/releases/download/agent-latest/'
_cache = (0, None)
_lock = threading.Lock()


def fetch(url, limit):
    with urlopen(Request(url, headers={'User-Agent': 'vps-control-pro', 'Accept': 'application/vnd.github+json'}), timeout=5) as response:
        data = response.read(limit+1)
    if len(data) > limit: raise ValueError('Release metadata too large')
    return data


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', value): raise ValueError('Invalid version')
    return tuple(map(int, value.split('.')))


def latest(force=False):
    global _cache
    with _lock:
        if not force and time.monotonic() < _cache[0]: return _cache[1]
        result = None
        try:
            release = json.loads(fetch(RELEASE, 1024*1024))
            if release.get('draft') or release.get('tag_name') != 'agent-latest': raise ValueError('Invalid release')
            def asset(name):
                items = [item for item in release['assets'] if item.get('name') == name]
                if len(items) != 1: raise ValueError('Missing release asset')
                item = items[0]
                if item.get('browser_download_url') != PREFIX+name or not re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('digest', '')): raise ValueError('Invalid asset')
                return item
            archive = asset('relay-agent.tar.gz'); metadata = asset('relay-agent-version.json')
            data = fetch(metadata['browser_download_url'], 4096)
            if 'sha256:'+hashlib.sha256(data).hexdigest() != metadata['digest']: raise ValueError('Metadata checksum mismatch')
            value = json.loads(data); version(value['version'])
            if value['digest'] != archive['digest']: raise ValueError('Release changed during discovery')
            result = {'version': value['version'], 'digest': value['digest']}
        except Exception: pass  # No URLs, remote errors or credentials in public state.
        _cache = (time.monotonic()+(300 if result else 30), result)
        return result


def path(api, host_id):
    return api.DATA_DIR/'relay'/'updates'/(hashlib.sha256(host_id.encode()).hexdigest()+'.json')


def missing_request(api, host_id, identity):
    try:
        control = api.relay_control(); host = control.host(control.read(), host_id)
        client = control.client(host); client.timeout = 2
        client.request('GET', '/v1/updates/'+identity)
    except AgentAPIError as exc: return exc.status == 404
    except Exception: pass
    return False


def annotate(api, hosts):
    release = latest() if hosts else None
    for host in hosts:
        available = {'state': 'unknown'}
        if release and host.get('state') == 'connected':
            try:
                installed, published = version(host['version']), version(release['version'])
                available = {**release, 'state': 'outdated' if installed < published else 'current' if installed == published else 'newer'}
            except (ValueError, KeyError): pass
        host['agent_release'] = available
        saved = path(api, host['id'])
        if not saved.exists(): continue
        try:
            intent = json.loads(saved.read_text())
            state = intent.get('state', 'unknown')
            if state not in ('updated', 'failed'):
                manual = host.get('updates', {}).get('manual', {})
                if host.get('state') == 'connected' and version(host['version']) >= version(intent['version']): state = 'updated'
                elif manual.get('id') == intent['id'] and manual.get('version') == intent['version'] and manual.get('digest') == intent['digest']:
                    state = manual['state']
                    if state == 'updated': state = 'unknown'  # Wait for the running version, not just the installer report.
                elif state == 'sending': state = 'unknown'
                if state == 'unknown' and host.get('state') == 'connected' and time.time()-intent['requested_at'] > 15 and missing_request(api, host['id'], intent['id']):
                    state = 'failed'  # Read-only proof that no such request exists; any retry remains explicit.
                if state in ('queued', 'running') and time.time()-intent['requested_at'] > 900: state = 'unknown'
            host['agent_update'] = {**{key: intent[key] for key in ('id', 'version', 'requested_at')}, 'state': state,
                                    'polling': state in ('sending', 'queued', 'running', 'unknown') and time.time()-intent['requested_at'] < 900}
        except (OSError, ValueError, KeyError, TypeError): host['agent_update'] = {'state': 'unknown'}


def perform(api, payload, identity):
    control = api.relay_control(); registry = control.checked(payload['expected_revision'])
    host = control.host(registry, payload['host_id']); saved = path(api, host['id'])
    command_id = uuid.UUID(identity).hex
    if saved.exists():
        previous = json.loads(saved.read_text())
        if previous['id'] == command_id: return  # Never repeat an uncertain PUT.
        observed = {'id': host['id'], 'state': 'unknown'}
        client = control.client(host)
        status = status_snapshot(client.request('GET', '/v1/status'))
        observed.update(state='connected', version=status['version'], updates=status.get('updates', {}))
        annotate(api, [observed])
        if observed.get('agent_update', {}).get('state') not in ('updated', 'failed'):
            raise ValueError('Сначала дождитесь подтверждения предыдущего обновления Agent')
    client = control.client(host)
    status = status_snapshot(client.request('GET', '/v1/status'))
    if 'manual-update-v1' not in status.get('capabilities', []):
        raise ValueError('Для команды обновления нужен Agent 1.4 или новее; сначала обновите Agent штатным способом')
    release = latest(force=True)
    if not release or any(payload[key] != release[key] for key in ('version', 'digest')):
        raise ValueError('Доступный релиз изменился или не подтверждён. Обновите сведения')
    if version(status['version']) >= version(release['version']): raise ValueError('Agent уже не устарел')
    for test in (api.DATA_DIR/'relay-tests').glob('*/public.json'):
        try:
            value = json.loads(test.read_text())
            if value.get('host_id') == host['id'] and value.get('state') in ('preparing', 'awaiting_client', 'processing') and value.get('expires_at', 0) > time.time():
                raise ValueError('Завершите клиентский тест перед обновлением Agent')
        except OSError: continue
    intent = {**release, 'id': command_id, 'state': 'sending', 'requested_at': time.time()}
    atomic_json(saved, intent)
    try:
        response = client.request('PUT', '/v1/updates/'+command_id, release)
        if response.get('id') != command_id or any(response.get(key) != release[key] for key in release) or response.get('state') not in ('queued', 'running', 'updated', 'failed', 'unknown'):
            raise ValueError('Unexpected update response')
        intent['state'] = response['state']
    except Exception:
        intent['state'] = 'unknown'
        atomic_json(saved, intent)
        raise ValueError('Ответ Agent на команду обновления не подтверждён. PRO проверит состояние без повторной отправки') from None
    atomic_json(saved, intent)
