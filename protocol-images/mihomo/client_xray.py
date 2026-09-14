"""Happ accepts an array of complete Xray configurations, one per selectable node."""
from copy import deepcopy
from client_singbox import UnsupportedClientConfig
from client_labels import connection_label


def xray_rules(rules):
    result = []
    for rule in rules:
        parts = rule.split(",")
        if parts[-1] == "no-resolve":
            parts.pop()
        if len(parts) != 3:
            raise UnsupportedClientConfig(f"Правило {rule} не поддерживается экспортом Happ")
        kind, value, target = parts
        if target not in {"DIRECT", "REJECT", "GATE.312"}:
            raise UnsupportedClientConfig(f"Цель правила {target} не поддерживается Happ")
        item = {"type": "field", "outboundTag": {"DIRECT": "direct", "REJECT": "block", "GATE.312": "proxy"}[target]}
        if kind in {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}:
            item["domain"] = [{"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:"}[kind] + value]
        elif kind in {"IP-CIDR", "IP-CIDR6"}:
            item["ip"] = [value]
        elif kind == "NETWORK":
            item["network"] = value.lower()
        else:
            raise UnsupportedClientConfig(f"Правило {kind} не поддерживается экспортом Happ")
        result.append(item)
    return result


def build_xray_configs(connections, routing, dns, rules, endpoint, direct_settings):
    nodes = []
    names = []
    for index, connection in enumerate(connections, 1):
        credential = connection.get("credential", {})
        if connection["component"] == "transport-shadowsocks":
            names.append(connection_label(connection, index))
            nodes.append({"protocol": "shadowsocks", "settings": {"servers": [{"address": endpoint, "port": int(credential["port"]), "method": credential["method"], "password": credential["password"]}]}})
            continue
        if connection["component"] != "transport-reality":
            raise UnsupportedClientConfig(f"Модуль {connection['component']} не поддерживается экспортом Happ")
        mode = connection.get("settings", {}).get("route_mode") or credential.get("route_mode") or "both"
        variants = ["tls"] if mode == "tls" else ["direct"]
        if credential.get("cdn_enabled") and mode in {"cdn", "both"}:
            variants = ["cdn"] if mode == "cdn" else ["direct", "cdn"]
        for variant in variants:
            names.append(connection_label(connection, index, variant, direct_settings))
            if variant == "direct":
                effective = dict(credential)
                if not credential.get("direct_tag"):
                    effective.update(direct_settings())
                address, port = endpoint, int(effective["port"])
                kind, path = effective.get("transport", "xhttp"), effective.get("path", "/")
                transport_mode = effective.get("xhttp_mode", "auto")
                stream = {"security": "reality", "realitySettings": {"serverName": effective["servername"], "fingerprint": "chrome", "publicKey": effective["public_key"], "shortId": effective["short_id"]}}
            else:
                address, port = credential[f"{variant}_domain"], 443
                kind, path = credential.get(f"{variant}_transport", "websocket" if variant == "cdn" else "xhttp"), credential.get(f"{variant}_path", "/")
                transport_mode = credential.get(f"{variant}_xhttp_mode", "auto")
                stream = {"security": "tls", "tlsSettings": {"serverName": address, "fingerprint": "chrome"}}
            stream["network"] = {"raw": "tcp", "websocket": "ws"}.get(kind, kind)
            if kind == "xhttp":
                stream["xhttpSettings"] = {"path": path, "mode": transport_mode}
            elif kind == "grpc":
                stream["grpcSettings"] = {"serviceName": str(path).lstrip("/")}
            elif kind == "websocket":
                stream["wsSettings"] = {"path": path, "headers": {"Host": address}}
            elif kind == "httpupgrade":
                stream["httpupgradeSettings"] = {"path": path, "host": address}
            elif kind not in {"raw", "tcp"}:
                raise UnsupportedClientConfig(f"Транспорт {kind} не поддерживается экспортом Happ")
            nodes.append({"protocol": "vless", "settings": {"vnext": [{"address": address, "port": port, "users": [{"id": credential["uuid"], "encryption": credential.get("encryption") or "none"}]}]}, "streamSettings": stream})
    if not nodes:
        raise UnsupportedClientConfig("В профиле нет совместимых подключений для Happ")
    configs = []
    for index, node in enumerate(nodes, 1):
        node["tag"] = "proxy"
        configs.append({"remarks": names[index - 1], "log": {"loglevel": "warning"},
                        "dns": {"servers": [dns["nameserver"], dns["fallback"]]},
                        "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"auth": "noauth", "udp": True}, "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}}],
                        "outbounds": [node, {"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
                        "routing": {"domainStrategy": "AsIs", "rules": deepcopy(xray_rules(rules))}})
    return configs
