import json
import tempfile
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from access_beta import create_access_beta_router

backend = {}
manager_profiles = {}
seq = {'direct': 0, 'smart': 0}

def detector():
    return {
        'awg': {'name': 'AmneziaWG', 'installed': True, 'active': True},
        'wg': {'name': 'WireGuard', 'installed': False, 'active': False},
        'vless-reality-xhttp': {'name': 'VLESS Reality', 'installed': True, 'active': True},
        'shadowsocks': {'name': 'Shadowsocks', 'installed': False, 'active': False},
        'mihomo': {'name': 'Mihomo', 'installed': True, 'active': True},
    }

def auth(): return None

def direct_create(name, protocol):
    seq['direct'] += 1
    cid = f'c{seq["direct"]}'
    backend[cid] = {'id': cid, 'name': name, 'protocol': protocol}
    return {'id': cid, 'filename': f'{name}.conf', 'config': f'CONFIG-{cid}'}

def direct_delete(cid):
    if cid not in backend:
        raise HTTPException(status_code=404, detail='missing')
    del backend[cid]
    return {'deleted': cid}

def direct_list(): return list(backend.values())

mods = [
    {'id': 'transport-awg', 'name': 'AWG', 'installed': True, 'active': True},
    {'id': 'transport-reality', 'name': 'VRX', 'installed': True, 'active': True},
]

def mh(method, path, payload=None):
    if method == 'GET' and path == '/api/mihomo/modules': return {'items': mods}
    if method == 'GET' and path == '/api/mihomo/profiles': return {'items': list(manager_profiles.values())}
    if method == 'POST' and path == '/api/mihomo/profiles':
        seq['smart'] += 1
        pid = f'm{seq["smart"]}'
        item = {'id': pid, **(payload or {}), 'created_at': '2026-08-19T00:00:00Z'}
        manager_profiles[pid] = item
        return item
    if method == 'PATCH' and path.startswith('/api/mihomo/profiles/'):
        pid = path.rsplit('/', 1)[1]; manager_profiles[pid].update(payload or {}); return manager_profiles[pid]
    if method == 'DELETE' and path.startswith('/api/mihomo/profiles/'):
        return {'removed': manager_profiles.pop(path.rsplit('/', 1)[1])['id']}
    if method == 'GET' and path.endswith('/config'): return 'mixed-port: 7890\n'
    raise RuntimeError((method, path, payload))

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    app = FastAPI()
    app.include_router(create_access_beta_router(
        data_dir=root, protocol_detector=detector, auth_dependency=auth,
        direct_create=direct_create, direct_delete=direct_delete,
        direct_list=direct_list, mihomo_request=mh,
    ))
    c = TestClient(app)
    u = c.post('/api/access-beta/users', json={'name':'Тестировщик','note':''}).json()['user']
    d = c.post(f'/api/access-beta/users/{u["id"]}/devices', json={'name':'iPhone-15PRO','platform':'ios','transports':['awg','vless-reality-xhttp']}).json()['device']
    assert c.post(f'/api/access-beta/users/{u["id"]}/devices/{d["id"]}/connections/provision-selected', json={'transports':['awg','vless-reality-xhttp']}).status_code == 200
    assert c.post(f'/api/access-beta/users/{u["id"]}/devices/{d["id"]}/smart', json={'transports':['awg','vless-reality-xhttp']}).status_code == 200
    assert (root/'access-beta.json.bak').exists()

    # Simulate a valid-but-empty registry being written externally while real resources remain.
    (root/'access-beta.json').write_text(json.dumps({'version':4,'users':[]}), encoding='utf-8')
    before = c.get('/api/access-beta/users').json()
    assert before['users'] == []
    assert before['recovery']['available'] is True
    assert before['recovery']['smart_candidates'] == 1

    rec = c.post('/api/access-beta/recover')
    assert rec.status_code == 200, rec.text
    payload = rec.json()['recovered']
    assert payload['users'] == 1 and payload['devices'] == 1 and payload['smart_profiles'] == 1
    after = c.get('/api/access-beta/users').json()
    assert len(after['users']) == 1
    device = after['users'][0]['devices'][0]
    assert device['smart_profile']['status'] == 'ready'
    assert len(device['connections']) == 2
    assert all(item['status'] == 'ready' for item in device['connections'])
    assert all(item['has_export'] is False for item in device['connections'])
    print('OK access beta v4.1 recovery flow')
