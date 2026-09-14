"""Subscription import page and VLESS share links for URI-only clients."""
from html import escape
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


CLIENT_HINTS = {"happ": "Happ", "singbox": "sing-box", "v2rage": "v2RAGE"}


def import_page(url):
    def hinted(client):
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode({"client": client}), ""))
    happ = "happ://add/" + hinted("happ")
    singbox = "sing-box://import-remote-profile?url=" + quote(hinted("singbox"), safe="") + "#VPN"
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение профиля</title><style>body{font:17px system-ui;background:#0c1520;color:#eef3fa;max-width:520px;margin:8vh auto;padding:24px}a{display:block;background:#473c86;color:white;padding:18px;margin:16px 0;border-radius:12px;text-decoration:none}input{box-sizing:border-box;width:100%;padding:14px;font:14px system-ui}p{line-height:1.6}</style>
<h1>Подключить профиль</h1><p>Выберите установленное приложение.</p>''' + \
        f'<a href="{escape(happ, quote=True)}">Открыть в Happ</a><a href="{escape(singbox, quote=True)}">Открыть в sing-box</a>' + \
        f'<p>Для v2RAGE скопируйте ссылку ниже и добавьте её как подписку.</p><input readonly aria-label="Подписка v2RAGE" value="{escape(hinted("v2rage"), quote=True)}">' + \
        f'<p>Общая ссылка для остальных клиентов:</p><input readonly aria-label="Общая подписка" value="{escape(url, quote=True)}"></html>'


def vless_subscription(configs):
    links = []
    for config in configs:
        node = config["outbounds"][0]
        if node["protocol"] != "vless":
            continue
        server = node["settings"]["vnext"][0]
        user = server["users"][0]
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
        address = server["address"]
        if ":" in address:
            address = f"[{address}]"
        links.append(f'vless://{quote(user["id"], safe="")}@{address}:{server["port"]}?{urlencode(params, quote_via=quote)}#{quote(config["remarks"], safe="")}')
    return "\n".join(links) + "\n"
