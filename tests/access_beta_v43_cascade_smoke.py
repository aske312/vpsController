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
fail_delete_ids = set()


def detector():
    return {
        'awg': {'name': 'AmneziaWG', 'installed': True, 'active': True},
        'wg': {'name': 'WireGuard', 'installed': True, 'active': True},
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
    if cid in fail_delete_ids:
        raise HTTPException(status_code=500, detail='simulated delete failure')
    if cid not in backend:
        raise HTTPException(status_code=404, detail='Client not found')
    del backend[cid]
    return {'deleted': cid}


def direct_list():
    return list(backend.values())


mods = [
    {'id': 'transport-awg', 'name': 'AWG', 'installed': True, 'active': True},
    {'id': 'transport-wg', 'name': 'WG', 'installed': True, 'active': True},
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
        manager_profiles[pid] = {'id': pid, **(payload or {})}
        return manager_profiles[pid]
    if method == 'DELETE' and path.startswith('/api/mihomo/profiles/'):
        pid = path.rsplit('/', 1)[1]
        if pid not in manager_profiles:
            raise HTTPException(status_code=404, detail='Profile not found')
        del manager_profiles[pid]
        return {'removed': pid}
    if method == 'PATCH' and path.startswith('/api/mihomo/profiles/'):
        pid = path.rsplit('/', 1)[1]
        manager_profiles[pid].update(payload or {})
        return manager_profiles[pid]
    if method == 'GET' and path.endswith('/config'):
        return 'mixed-port: 7890\n'
    raise RuntimeError((method, path, payload))


def create_device(c, uid, name, transports):
    r = c.post(f'/api/access-beta/users/{uid}/devices', json={
        'name': name,
        'platform': 'windows',
        'transports': transports,
    })
    assert r.status_code == 200, r.text
    return r.json()['device']['id']


def provision(c, uid, did, transports, smart=False):
    r = c.post(
        f'/api/access-beta/users/{uid}/devices/{did}/connections/provision-selected',
        json={'transports': transports},
    )
    assert r.status_code == 200, r.text
    if smart:
        r = c.post(f'/api/access-beta/users/{uid}/devices/{did}/smart', json={'transports': transports})
        assert r.status_code == 200, r.text


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

    # Device cascade: direct + Smart disappear before the device metadata.
    r = c.post('/api/access-beta/users', json={'name': 'Cascade', 'note': ''})
    uid = r.json()['user']['id']
    d1 = create_device(c, uid, 'Phone', ['awg', 'vless-reality-xhttp'])
    d2 = create_device(c, uid, 'Laptop', ['wg'])
    provision(c, uid, d1, ['awg', 'vless-reality-xhttp'], smart=True)
    provision(c, uid, d2, ['wg'])
    assert len(backend) == 3 and len(manager_profiles) == 1

    r = c.delete(f'/api/access-beta/users/{uid}/devices/{d1}')
    assert r.status_code == 200, r.text
    assert r.json()['connections_deleted'] == 2
    assert r.json()['smart_deleted'] is True
    assert len(backend) == 1 and not manager_profiles
    users = c.get('/api/access-beta/users').json()['users']
    user = next(item for item in users if item['id'] == uid)
    assert [d['id'] for d in user['devices']] == [d2]

    # User cascade: remaining devices and their resources disappear automatically.
    r = c.delete(f'/api/access-beta/users/{uid}')
    assert r.status_code == 200, r.text
    assert r.json()['devices_deleted'] == 1
    assert not backend and not manager_profiles
    assert not c.get('/api/access-beta/users').json()['users']

    # Partial failure: deleted resources are removed from metadata, failed resources stay visible.
    r = c.post('/api/access-beta/users', json={'name': 'Failure', 'note': ''})
    uid = r.json()['user']['id']
    d1 = create_device(c, uid, 'Good', ['awg'])
    d2 = create_device(c, uid, 'Bad', ['wg'])
    provision(c, uid, d1, ['awg'])
    provision(c, uid, d2, ['wg'])
    bad_backend_id = next(item['id'] for item in backend.values() if item['protocol'] == 'wg')
    fail_delete_ids.add(bad_backend_id)

    r = c.delete(f'/api/access-beta/users/{uid}')
    assert r.status_code == 500, r.text
    users = c.get('/api/access-beta/users').json()['users']
    assert len(users) == 1
    assert len(users[0]['devices']) == 1
    assert users[0]['devices'][0]['name'] == 'Bad'
    assert len(users[0]['devices'][0]['connections']) == 1
    assert list(backend) == [bad_backend_id]

    fail_delete_ids.clear()
    r = c.delete(f'/api/access-beta/users/{uid}')
    assert r.status_code == 200, r.text
    assert not backend
    assert not c.get('/api/access-beta/users').json()['users']

print('OK access beta v4.3 cascade delete flow')
