"""Additional protocols in the existing Direct Xray component.

VLESS keeps its legacy shared listeners. New TLS/CDN connections own their
loopback listener, path and credentials, in the same Xray service/configuration.
"""
import copy
import json
import os
import secrets
import uuid
from datetime import datetime, timezone

import xray_protocols


def apply(api, config, clients, token):
    paths = (api.VLESS_CONFIG, api.CLIENTS_FILE, api.VLESS_CDN_SNIPPET)
    original = {path: path.read_bytes() if path.exists() else None for path in paths}
    candidate = api.VLESS_CONFIG_DIR / f'.xray-{token}.tmp.json'
    replaced = False
    try:
        candidate.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        os.chmod(candidate, 0o640)
        os.chown(candidate, 0, 65534)
        checked = api.subprocess.run([api.XRAY_BIN, 'run', '-test', '-config', str(candidate)],
                                     capture_output=True, text=True, timeout=15, check=False)
        if checked.returncode:
            raise RuntimeError('Xray rejected candidate configuration')
        candidate.replace(api.VLESS_CONFIG)
        replaced = True
        api.restart_vless_service()
        api.write_clients(clients)
        values = api.cdn_security.read_env(api.VLESS_ENV)
        api.write_vless_cdn_snippet(values.get('CDN_ENABLED') == 'yes', values.get('CDN_DOMAIN', ''),
                                   values.get('CDN_PATH', values.get('WS_PATH', '/')), values.get('CDN_TRANSPORT', 'websocket'))
        api.run('caddy', 'validate', '--config', '/etc/caddy/Caddyfile', check=True)
        api.run('systemctl', 'reload', 'caddy.service', check=True)
    except Exception:
        for path, data in original.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                backup = path.with_name(path.name + '.xray-rollback')
                backup.write_bytes(data)
                os.chmod(backup, 0o644 if path == api.VLESS_CDN_SNIPPET else 0o640 if path == api.VLESS_CONFIG else 0o600)
                if path == api.VLESS_CONFIG:
                    os.chown(backup, 0, 65534)
                backup.replace(path)
        if replaced:
            api.restart_vless_service()
            api.run('systemctl', 'reload', 'caddy.service', check=True)
        raise
    finally:
        candidate.unlink(missing_ok=True)


def create(api, payload, client_id, safe_name, on_mutation=None):
    kind = payload.settings.xray_protocol
    if kind not in {'vmess', 'trojan'}:
        raise api.HTTPException(status_code=422, detail='Неизвестный протокол Xray')
    if not api.VLESS_CONFIG.exists() or not api.VLESS_ENV.exists():
        raise api.HTTPException(status_code=409, detail='Сначала установите компонент Xray')
    config = json.loads(api.VLESS_CONFIG.read_text(encoding='utf-8'))
    values = api.cdn_security.read_env(api.VLESS_ENV)
    requested = list(dict.fromkeys(payload.vless_routes or []))
    if not requested or any(route not in {'tls', 'cdn'} for route in requested):
        raise api.HTTPException(status_code=422, detail='Для VMess и Trojan выберите TLS или CDN')
    routes = []
    for route in requested:
        domain = str(payload.settings.cdn_domain or values.get('CDN_DOMAIN', '')) if route == 'cdn' else values.get('TLS_DOMAIN', '')
        if values.get(route.upper()+'_ENABLED') != 'yes' or not domain:
            raise api.HTTPException(status_code=409, detail='Маршрут Xray TLS/CDN не настроен')
        available = api.read_network_endpoint_settings().get('cdn_domains' if route == 'cdn' else 'tls_relay_domains', [])
        if domain not in {*available, values.get(route.upper()+'_DOMAIN', '')} or not api.network_endpoint_check('cdn' if route == 'cdn' else 'tls_relay', domain).get('selectable'):
            raise api.HTTPException(status_code=409, detail='Адрес Xray отсутствует в активных маршрутах Сети')
        # DNS delivery belongs to a hostname, regardless of its Xray protocol.
        if any(item.get('domain') == domain and (item.get('relay_origins') or item.get('cloudflare', True) != (route == 'cdn')) for item in api.cdn_security.read_routes()):
            raise api.HTTPException(status_code=409, detail='CDN-домен уже использует Relay; для Direct нужен отдельный домен')
        routes.append({'kind':route, 'domain':domain, 'transport':getattr(payload.settings, route+'_transport'),
                       'mode':getattr(payload.settings, route+'_xhttp_mode')})
    identity = str(uuid.uuid4())
    credential = {'xray_protocol':kind, 'uuid':identity}
    if kind == 'trojan': credential['password'] = secrets.token_urlsafe(32)
    profiles, descriptors = [], []
    owner = f'panel:xray:{client_id}:'
    try:
        if on_mutation: on_mutation()
        used = {int(row.get('port', 0)) for row in config.get('inbounds', []) if isinstance(row.get('port'), int)}
        for route in routes:
            port = api.port_allocation.allocate(owner+route['kind'], 12000, {'tcp'}, used=used, count=10000)
            used.add(port)
            path = '/' + secrets.token_hex(16)
            tag = f"xray-{route['kind']}-{client_id}"
            stream = api.cdn_stream(route['transport'], path, route['mode'])
            config.setdefault('inbounds', []).append({'tag':tag, 'listen':'127.0.0.1', 'port':port, 'protocol':kind,
                'settings':xray_protocols.server_settings(kind, xray_protocols.client_identity(credential), client_id+'@312.net'),
                'streamSettings':stream})
            descriptors.append({'tag':tag, 'domain':route['domain'], 'path':path, 'port':port,
                                'transport':route['transport'], 'cloudflare':route['kind'] == 'cdn'})
            client_stream = copy.deepcopy(stream)
            client_stream.update(security='tls', tlsSettings={'serverName':route['domain'], 'fingerprint':payload.settings.fingerprint})
            outbound = {'tag':'proxy', 'protocol':kind, 'settings':xray_protocols.outbound_settings(credential, route['domain'], 443), 'streamSettings':client_stream}
            client = {'remarks':payload.name+' '+route['kind'].upper(), 'log':{'loglevel':'warning'},
                      'inbounds':[{'listen':'127.0.0.1', 'port':10808, 'protocol':'socks', 'settings':{'udp':True}}],
                      'outbounds':[outbound]}
            profiles.append({'id':route['kind'], 'name':kind.title()+' '+route['kind'].upper(),
                             'filename':f"{safe_name}-{route['kind']}.json", 'config':json.dumps(client, ensure_ascii=False, indent=2)})
        clients = api.read_clients()
        clients.append({'id':client_id, 'name':payload.name, 'protocol':'vless-reality-xhttp', 'xray_protocol':kind,
                        'public_key':identity, 'vless_routes':requested, 'xray_routes':descriptors,
                        'route_endpoints':{'cdn':next((r['domain'] for r in routes if r['kind']=='cdn'), ''),
                                           'tls_relay':next((r['domain'] for r in routes if r['kind']=='tls'), '')},
                        'settings':payload.settings.model_dump(exclude_none=True), 'created_at':datetime.now(timezone.utc).isoformat()})
        apply(api, config, clients, client_id)
        return {'id':client_id, 'filename':safe_name+'.json', 'config':json.dumps([json.loads(item['config']) for item in profiles], ensure_ascii=False, indent=2) if len(profiles)>1 else profiles[0]['config'], 'profiles':profiles}
    finally:
        api.port_allocation.release_prefix(owner)


def delete(api, item, clients):
    config = json.loads(api.VLESS_CONFIG.read_text(encoding='utf-8'))
    tags = {f"xray-{route}-{item['id']}" for route in ('tls', 'cdn')}
    config['inbounds'] = [row for row in config.get('inbounds', []) if row.get('tag') not in tags]
    apply(api, config, [row for row in clients if row['id'] != item['id']], item['id'])
    return {'deleted':item['id']}
