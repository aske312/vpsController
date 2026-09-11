"""Sing-box client profile export.

The server remains Xray/Caddy/Mihomo; this module only serializes client-side
outbounds. Unknown or unsupported connection types fail the export explicitly.
"""
from copy import deepcopy


class UnsupportedClientConfig(ValueError):
    pass


def _tls(server, reality=None, ech=False, fragment=False):
    value = {"enabled": True, "server_name": server,
             "utls": {"enabled": True, "fingerprint": "chrome"}}
    if reality:
        value["reality"] = {"enabled": True, "public_key": reality[0], "short_id": reality[1]}
    if ech:
        value["ech"] = {"enabled": True, "query_server_name": server}
    if fragment:
        # sing-box intentionally chooses safe record/packet boundaries itself;
        # unlike Xray it does not accept a length range in this field.
        value["fragment"] = True
        value["record_fragment"] = True
    return value


def _transport(kind, path, mode="auto", host=None):
    if kind in {"raw", "tcp"}:
        return None
    if kind == "xhttp":
        # XHTTP is represented by sing-box's HTTP V2Ray transport. The server
        # path and mode are retained; clients that do not support XHTTP reject
        # validation instead of silently connecting incorrectly.
        return {"type": "http", "path": path}
    if kind == "websocket":
        return {"type": "ws", "path": path, "headers": {"Host": host} if host else {}}
    if kind == "httpupgrade":
        return {"type": "httpupgrade", "path": path, "host": [host] if host else []}
    if kind == "grpc":
        return {"type": "grpc", "service_name": str(path).lstrip("/")}
    raise UnsupportedClientConfig(f"Транспорт {kind} не поддерживается sing-box")


def _vless(tag, credential, endpoint, variant, routing, fragment):
    if variant == "direct":
        effective = dict(credential)
        if not credential.get("direct_tag"):
            effective.update(endpoint[1]())
        server, port = endpoint[0], int(effective["port"])
        transport = str(effective.get("transport", "xhttp"))
        path = effective.get("path", "/")
        tls = _tls(effective["servername"], (effective["public_key"], effective["short_id"]), False, fragment)
    else:
        server, port = credential[f"{variant}_domain"], 443
        transport = str(credential.get(f"{variant}_transport", "xhttp"))
        path = credential.get(f"{variant}_path", "/")
        tls = _tls(server, None, bool(routing.get("tunnel_ech")) and variant == "cdn", fragment)
    value = {"type": "vless", "tag": tag, "server": server, "server_port": port,
             "uuid": credential["uuid"], "network": "tcp", "tls": tls}
    transport_value = _transport(transport, path, credential.get(f"{variant}_xhttp_mode", "auto"), server)
    if transport_value:
        value["transport"] = transport_value
    return value


def build_singbox_config(connections, routing, dns, rules, endpoint, direct_settings, fragment):
    outbounds = []
    for index, connection in enumerate(connections, 1):
        module, credential = connection["component"], connection.get("credential", {})
        base = f"connection-{index}"
        if module == "transport-reality":
            mode = str(connection.get("settings", {}).get("route_mode") or credential.get("route_mode") or "both")
            variants = ["tls"] if mode == "tls" else ["direct"]
            if credential.get("cdn_enabled") and mode in {"cdn", "both"}:
                variants = ["cdn"] if mode == "cdn" else ["direct", "cdn"]
            for variant in variants:
                outbounds.append(_vless(f"{base}-{variant}", credential, (endpoint, direct_settings), variant, routing, fragment))
        elif module == "transport-shadowsocks":
            outbounds.append({"type": "shadowsocks", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "method": credential["method"],
                              "password": credential["password"]})
        elif module in {"transport-wg", "transport-awg"}:
            awg = credential.get("amnezia")
            if module == "transport-awg" and awg:
                # Keep the data for future sing-box/AmneziaWG support explicit;
                # current sing-box schema has no amnezia-wg-option field.
                raise UnsupportedClientConfig("AmneziaWG пока не имеет проверенного sing-box-экспорта")
            outbounds.append({"type": "wireguard", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "private_key": credential["private_key"],
                              "local_address": [credential["ip"]], "peer_public_key": credential["server_public_key"],
                              "mtu": int(credential["mtu"])})
        elif module == "transport-hysteria2":
            outbounds.append({"type": "hysteria2", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "password": credential["password"],
                              "up_mbps": int(credential["up_mbps"]), "down_mbps": int(credential["down_mbps"]),
                              "tls": {"enabled": True, "server_name": credential.get("sni", "gate.312"), "insecure": True}})
        elif module == "transport-tuic":
            outbounds.append({"type": "tuic", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "uuid": credential["uuid"],
                              "password": credential["password"], "congestion_control": credential.get("congestion_control", "bbr"),
                              "tls": {"enabled": True, "server_name": credential.get("sni", "gate.312"), "insecure": True}})
        else:
            raise UnsupportedClientConfig(f"Модуль {module} не поддерживается sing-box")
    if not outbounds:
        raise UnsupportedClientConfig("У устройства нет подключений для экспорта")
    tags = [item["tag"] for item in outbounds]
    outbounds += [{"type": "direct", "tag": "direct"}, {"type": "block", "tag": "block"}]
    if len(tags) == 1:
        proxy_tag = tags[0]
    else:
        proxy_tag = "GATE.312"
        outbounds.append({"type": "urltest", "tag": proxy_tag, "outbounds": tags,
                          "url": routing.get("test_url", "https://www.gstatic.com/generate_204"),
                          "interval": f"{int(routing.get('interval', 180))}s"})
    route_rules = []
    for rule in rules:
        parts = [part.strip() for part in rule.split(",")]
        if parts and parts[-1] == "no-resolve":
            parts.pop()
        if len(parts) != 3:
            raise UnsupportedClientConfig(f"Правило {parts[0] if parts else rule} не поддерживается sing-box")
        kind, value, target = parts
        item = {"outbound": proxy_tag if target == "GATE.312" else ("direct" if target == "DIRECT" else "block")}
        if kind in {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}:
            item["domain"] = ({"DOMAIN": [value], "DOMAIN-SUFFIX": [value], "DOMAIN-KEYWORD": [value]}[kind])
        elif kind in {"IP-CIDR", "IP-CIDR6", "GEOIP"}:
            item["ip_cidr"] = [value if kind != "GEOIP" else f"geoip:{value}"]
        elif kind == "NETWORK":
            item["network"] = [value.lower()]
        else:
            raise UnsupportedClientConfig(f"Правило {kind} не поддерживается sing-box")
        route_rules.append(item)
    route_rules.append({"action": "sniff"})
    route_rules.append({"inbound": ["mixed-in"], "outbound": proxy_tag})
    return {"log": {"level": "warn"},
            "dns": {"servers": [{"tag": "remote", "address": dns["nameserver"]}, {"tag": "fallback", "address": dns["fallback"]}], "final": "remote"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 7890}],
            "outbounds": outbounds,
            "route": {"auto_detect_interface": True, "rules": route_rules, "final": proxy_tag}}
