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
        'shadowsocks': {'name': 'Shadowsocks', 'installed': True, 'active': True},
        'mihomo': {'name': 'Mihomo', 'installed': True, 'active': True},
    }

def auth():
    return None

def direct_create(name, protocol):
    seq['direct'] += 1
    cid = f'core{seq["direct"]}'
    backend[cid] = {'id': cid, 'name': name, 'protocol': protocol}
    return {'id': cid, 'filename': f'{name}-{protocol}.txt', 'config': f'CONFIG:{protocol}:{cid}'}

def direct_delete(cid):
    if cid not in backend:
        raise HTTPException(status_code=404, detail='Client not found')
    del backend[cid]
    return {'deleted': cid}

def direct_list():
    return list(backend.values())

mods = [
    {'id': 'transport-awg', 'name': 'AWG', 'installed': True, 'active': True},
    {'id': 'transport-wg', 'name': 'WG', 'installed': False, 'active': False},
    {'id': 'transport-reality', 'name': 'VRX', 'installed': True, 'active': True},
    {'id': 'transport-shadowsocks', 'name': 'SS', 'installed': True, 'active': True},
]

def mh(method, path, payload=None):
    if method == 'GET' and path == '/api/mihomo/modules':
        return {'items': mods}
    if method == 'GET' and path == '/api/mihomo/profiles':
        return {'items': list(manager_profiles.values())}
    if method == 'POST' and path == '/api/mihomo/profiles':
        seq['smart'] += 1
        pid = f'mh{seq["smart"]}'
        manager_profiles[pid] = {'id': pid, **payload}
        return manager_profiles[pid]
    if method == 'PATCH' and path.startswith('/api/mihomo/profiles/'):
        pid = path.rsplit('/', 1)[1]
        if pid not in manager_profiles:
            raise HTTPException(status_code=404, detail='Profile not found')
        manager_profiles[pid].update(payload or {})
        return manager_profiles[pid]
    if method == 'DELETE' and path.startswith('/api/mihomo/profiles/'):
        pid = path.rsplit('/', 1)[1]
        if pid not in manager_profiles:
            raise HTTPException(status_code=404, detail='Profile not found')
        del manager_profiles[pid]
        return {'removed': pid}
    if method == 'GET' and path.endswith('/config'):
        pid = path.split('/')[-2]
        if pid not in manager_profiles:
            raise HTTPException(status_code=404, detail='Profile not found')
        return 'mixed-port: 7890\n'
    raise RuntimeError((method, path, payload))

with tempfile.TemporaryDirectory() as td:
    app = FastAPI()
    app.include_router(create_access_beta_router(
        data_dir=Path(td),
        protocol_detector=detector,
        auth_dependency=auth,
        direct_create=direct_create,
        direct_delete=direct_delete,
        direct_list=direct_list,
        mihomo_request=mh,
    ))
    c = TestClient(app)

    r = c.post('/api/access-beta/users', json={'name': 'Ivan', 'note': 'test'})
    assert r.status_code == 200, r.text
    uid = r.json()['user']['id']

    r = c.post(f'/api/access-beta/users/{uid}/devices', json={
        'name': 'iPhone',
        'platform': 'ios',
        'transports': ['awg', 'vless-reality-xhttp'],
    })
    assert r.status_code == 200, r.text
    did = r.json()['device']['id']
    assert r.json()['device']['routing']['strategy'] == 'fallback'

    routing = {
        'mode': 'rule',
        'strategy': 'url-test',
        'test_url': 'https://example.com/generate_204',
        'interval': 90,
        'rules': 'DOMAIN-SUFFIX,example.org,DIRECT',
    }
    r = c.patch(f'/api/access-beta/users/{uid}/devices/{did}', json={'routing': routing})
    assert r.status_code == 200, r.text
    assert r.json()['device']['routing'] == routing

    r = c.post(f'/api/access-beta/users/{uid}/devices/{did}/connections/provision-selected', json={'transports': ['awg', 'vless-reality-xhttp']})
    assert r.status_code == 200, r.text
    assert len(r.json()['connections']) == 2

    r = c.post(f'/api/access-beta/users/{uid}/devices/{did}/smart', json={
        'transports': ['awg', 'vless-reality-xhttp'],
        'strategy': 'url-test',
        'routing': routing,
    })
    assert r.status_code == 200, r.text
    smart = r.json()['smart_profile']
    assert smart['status'] == 'ready'
    assert smart['routing']['strategy'] == 'url-test'

    updated_routing = {**routing, 'strategy': 'fallback', 'interval': 120}
    r = c.patch(f'/api/access-beta/users/{uid}/devices/{did}', json={'routing': updated_routing})
    assert r.status_code == 200, r.text
    assert r.json()['device']['smart_profile']['strategy'] == 'fallback'
    backend_smart = next(iter(manager_profiles.values()))
    assert backend_smart['routing']['strategy'] == 'fallback'
    assert backend_smart['routing']['interval'] == 120

    r = c.get(f'/api/access-beta/users/{uid}/devices/{did}/smart/export')
    assert r.status_code == 200 and 'mixed-port' in r.text

    r = c.post(f'/api/access-beta/users/{uid}/devices/{did}/cleanup')
    assert r.status_code == 200, r.text
    assert not backend and not manager_profiles

    r = c.delete(f'/api/access-beta/users/{uid}/devices/{did}')
    assert r.status_code == 200, r.text
    r = c.delete(f'/api/access-beta/users/{uid}')
    assert r.status_code == 200, r.text
    print('OK access beta v3 compact routing flow')
