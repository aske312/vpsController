"""Subscription import page and VLESS share links for URI-only clients."""
from html import escape
from urllib.parse import quote, urlencode
import base64
import json


CLIENT_HINTS = {"happ": "Happ", "singbox": "sing-box", "v2rage": "v2RAGE"}


def import_page(url, verified_clients=()):
    apps = "".join(f'<a href="{escape(record["app_url"], quote=True)}" rel="noreferrer">{escape(record["client_name"])} {escape(record["client_version"])} · {escape(record["os"])} {escape(record["os_version"])} · {escape(record["core_name"])} {escape(record["core_version"])}</a>' for record in verified_clients)
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение профиля</title><style>body{font:17px system-ui;background:#0c1520;color:#eef3fa;max-width:520px;margin:8vh auto;padding:24px}a{display:block;background:#473c86;color:white;padding:18px;margin:16px 0;border-radius:12px;text-decoration:none}input{box-sizing:border-box;width:100%;padding:14px;font:14px system-ui}p{line-height:1.6}</style>
<h1>Подключить профиль</h1>''' + \
        ('<p>Приложения, проверенные для этих подключений:</p>' + apps if apps else '<p>Для этих подключений пока нет проверенных приложений.</p>') + \
        '<p>Добавьте общую ссылку как подписку. Непроверенный клиент получит стандартный Mihomo YAML; это не подтверждает совместимость приложения. После изменения настроек обновите подписку.</p>' + \
        f'<input readonly aria-label="Общая подписка" value="{escape(url, quote=True)}"></html>'


def vless_subscription(configs):
    links = []
    for config in configs:
        node = config["outbounds"][0]
        kind = node['protocol']
        if kind not in {'vless', 'vmess', 'trojan'}:
            continue
        server = node["settings"]['servers' if kind == 'trojan' else 'vnext'][0]
        user = server if kind == 'trojan' else server["users"][0]
        stream = node.get("streamSettings", {})
        params = {"encryption": user.get("encryption", "none"), "type": stream.get("network", "tcp"), "security": stream.get("security", "none")}
        tls = stream.get("realitySettings", stream.get("tlsSettings", {}))
        for source, target in [("serverName", "sni"), ("fingerprint", "fp"), ("publicKey", "pbk"), ("shortId", "sid")]:
            if source in tls:
                params[target] = tls[source]
        if "wsSettings" in stream:
            params.update(path=stream["wsSettings"].get("path", "/"), host=stream["wsSettings"].get("headers", {}).get("Host", server["address"]))
        if "grpcSettings" in stream:
            params["serviceName"] = stream["grpcSettings"].get("serviceName", "")
        if "httpupgradeSettings" in stream:
            params.update(stream["httpupgradeSettings"])
        if kind == 'vmess':
            data = {'v':'2', 'ps':config['remarks'], 'add':server['address'], 'port':str(server['port']),
                    'id':user['id'], 'aid':'0', 'scy':'auto', 'net':params['type'], 'type':'none',
                    'host':params.get('host', ''), 'path':params.get('serviceName', params.get('path', '')),
                    'tls':'tls' if params['security'] == 'tls' else '', 'sni':params.get('sni', ''), 'fp':params.get('fp', '')}
            links.append('vmess://' + base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode())
            continue
        if kind == 'trojan':
            params.pop('encryption', None)
        address = server["address"]
        if ":" in address:
            address = f"[{address}]"
        identity = user['password'] if kind == 'trojan' else user['id']
        links.append(f'{kind}://{quote(identity, safe="")}@{address}:{server["port"]}?{urlencode(params, quote_via=quote)}#{quote(config["remarks"], safe="")}')
    return "\n".join(links) + "\n"
