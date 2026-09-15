"""Sing-box client profile export.

The server remains Xray/Caddy/Mihomo; this module only serializes client-side
outbounds. Unknown or unsupported connection types fail the export explicitly.
"""
from urllib.parse import urlsplit
import re
from client_labels import connection_label


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


def _transport(kind, path, mode="auto", host=None, capabilities=None):
    if kind in {"raw", "tcp"}:
        return None
    if kind == "xhttp":
        if capabilities and "xhttp" in capabilities["transports"]:
            return {"type": "xhttp", "path": path, "mode": mode, "host": host or ""}
        raise UnsupportedClientConfig("XHTTP не поддерживается стандартным экспортом sing-box")
    if kind == "websocket":
        return {"type": "ws", "path": path, "headers": {"Host": host} if host else {}}
    if kind == "httpupgrade":
        return {"type": "httpupgrade", "path": path, "host": host or ""}
    if kind == "grpc":
        return {"type": "grpc", "service_name": str(path).lstrip("/")}
    raise UnsupportedClientConfig(f"Транспорт {kind} не поддерживается sing-box")


def _vless(tag, credential, endpoint, variant, routing, fragment, capabilities=None):
    if credential.get("encryption") and not (capabilities and "tunnel_privacy" in capabilities["features"]):
        raise UnsupportedClientConfig("VLESS Encryption не поддерживается экспортом sing-box. Сохраните устройство с выключенным шифрованием до VPS и обновите подписку.")
    if variant == "direct":
        effective = dict(credential)
        if not credential.get("direct_tag"):
            effective.update(endpoint[1]())
        server, port = endpoint[0], int(effective["port"])
        transport = str(effective.get("transport", "xhttp"))
        path = effective.get("path", "/")
        mode = effective.get("xhttp_mode", "auto")
        tls = _tls(effective["servername"], (effective["public_key"], effective["short_id"]), False, fragment)
    else:
        server, port = credential[f"{variant}_domain"], 443
        transport = str(credential.get(f"{variant}_transport", "xhttp"))
        path = credential.get(f"{variant}_path", "/")
        mode = credential.get(f"{variant}_xhttp_mode", "auto")
        tls = _tls(server, None, bool(routing.get("tunnel_ech")) and variant == "cdn", fragment)
    value = {"type": "vless", "tag": tag, "server": server, "server_port": port,
             "uuid": credential["uuid"], "tls": tls}
    if credential.get("encryption"):
        value["encryption"] = credential["encryption"]
    if tls.get("ech"):
        value["domain_resolver"] = "remote"
    transport_value = _transport(transport, path, mode, server, capabilities)
    if transport_value:
        value["transport"] = transport_value
    return value


def build_singbox_config(connections, routing, dns, rules, endpoint, direct_settings, fragment, capabilities=None):
    outbounds = []
    endpoints = []
    tags = []
    for index, connection in enumerate(connections, 1):
        previous_outbounds, previous_endpoints = len(outbounds), len(endpoints)
        module, credential = connection["component"], connection.get("credential", {})
        base = connection_label(connection, index) if module != "transport-reality" else ""
        if module == "transport-reality":
            mode = str(connection.get("settings", {}).get("route_mode") or credential.get("route_mode") or "both")
            variants = ["tls"] if mode == "tls" else ["direct"]
            if credential.get("cdn_enabled") and mode in {"cdn", "both"}:
                variants = ["cdn"] if mode == "cdn" else ["direct", "cdn"]
            for variant in variants:
                outbounds.append(_vless(connection_label(connection, index, variant, direct_settings), credential, (endpoint, direct_settings), variant, routing, fragment, capabilities))
        elif module in {"transport-wg", "transport-awg"}:
            if module == "transport-awg" and not (capabilities and module in capabilities["components"]):
                raise UnsupportedClientConfig("AmneziaWG требует клиент с ядром sing-box-lx")
            peer = {"address": endpoint, "port": int(credential["port"]), "public_key": credential["server_public_key"],
                    "allowed_ips": ["0.0.0.0/0", "::/0"], "persistent_keepalive_interval": 25}
            if credential.get("preshared_key"):
                peer["pre_shared_key"] = credential["preshared_key"]
            value = {"type": "wireguard", "tag": base, "system": False, "address": [credential["ip"]],
                     "private_key": credential["private_key"], "mtu": int(credential.get("mtu", 1420)), "peers": [peer]}
            if module == "transport-awg":
                value.update(credential.get("amnezia", {}))
            endpoints.append(value)
        elif module == "transport-shadowsocks":
            outbounds.append({"type": "shadowsocks", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "method": credential["method"],
                              "password": credential["password"]})
        elif module == "transport-hysteria2":
            outbounds.append({"type": "hysteria2", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "password": credential["password"],
                              "up_mbps": int(credential["up_mbps"]), "down_mbps": int(credential["down_mbps"]),
                              "tls": {"enabled": True, "server_name": credential.get("sni", "gate.312"), "insecure": True}})
            if credential.get("obfs"):
                outbounds[-1]["obfs"] = {"type": "salamander", "password": credential["obfs_password"]}
        elif module == "transport-tuic":
            outbounds.append({"type": "tuic", "tag": base, "server": endpoint,
                              "server_port": int(credential["port"]), "uuid": credential["uuid"],
                              "password": credential["password"], "congestion_control": credential.get("congestion_control", "bbr"),
                              "tls": {"enabled": True, "server_name": credential.get("sni", "gate.312"), "insecure": True}})
        else:
            raise UnsupportedClientConfig(f"Модуль {module} не поддерживается sing-box")
        tags.extend(item["tag"] for item in outbounds[previous_outbounds:] + endpoints[previous_endpoints:])
    if not outbounds and not endpoints:
        raise UnsupportedClientConfig("У устройства нет подключений для экспорта")
    outbounds += [{"type": "direct", "tag": "direct"}, {"type": "block", "tag": "block"}]
    if len(tags) == 1:
        proxy_tag = tags[0]
    else:
        proxy_tag = "GATE.312"
        strategy = routing.get("strategy") or "url-test"
        if strategy == "select":
            outbounds.append({"type": "selector", "tag": proxy_tag, "outbounds": tags, "default": tags[0]})
        elif strategy == "url-test":
            outbounds.append({"type": "urltest", "tag": proxy_tag, "outbounds": tags,
                              "url": routing.get("test_url", "https://www.gstatic.com/generate_204"),
                              "interval": f"{int(routing.get('interval', 30))}s"})
        else:
            raise UnsupportedClientConfig(f"Стратегия {strategy} не поддерживается стандартным sing-box")
    route_rules, rule_sets = singbox_rules(rules, proxy_tag)
    config = {"log": {"level": "warn"},
              "dns": {"servers": [{"type": "local", "tag": "bootstrap"}, dns_server(dns["nameserver"], "remote"), dns_server(dns["fallback"], "fallback")], "final": "remote"},
              "inbounds": [{"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"], "auto_route": True},
                           {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 7890}],
              "outbounds": outbounds,
              "route": {"auto_detect_interface": True, "default_domain_resolver": "bootstrap", "rules": [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}, *route_rules], "final": proxy_tag}}
    if endpoints:
        config["endpoints"] = endpoints
    if rule_sets:
        config["route"]["rule_set"] = rule_sets
        config.setdefault("experimental", {})["cache_file"] = {"enabled": True}
    if routing.get("strategy") == "select":
        config.setdefault("experimental", {})["clash_api"] = {"external_controller": "127.0.0.1:9090"}
    return config


def singbox_rules(rules, proxy_tag="GATE.312"):
    route_rules, rule_sets = [], {}
    for rule in rules:
        parts = [part.strip() for part in rule.split(",")]
        if parts and parts[-1] == "no-resolve":
            parts.pop()
        if len(parts) != 3:
            raise UnsupportedClientConfig(f"Правило {parts[0] if parts else rule} не поддерживается sing-box")
        kind, value, target = parts
        if target not in {"GATE.312", "DIRECT", "REJECT"}:
            raise UnsupportedClientConfig(f"Цель правила {target} не поддерживается sing-box")
        item = {"outbound": proxy_tag if target == "GATE.312" else ("direct" if target == "DIRECT" else "block")}
        if kind in {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}:
            item[{"DOMAIN": "domain", "DOMAIN-SUFFIX": "domain_suffix", "DOMAIN-KEYWORD": "domain_keyword"}[kind]] = [value]
        elif kind in {"IP-CIDR", "IP-CIDR6"}:
            item["ip_cidr"] = [value]
        elif kind == "NETWORK":
            item["network"] = [value.lower()]
        elif kind == "DST-PORT":
            if not value.isdigit() or not 1 <= int(value) <= 65535:
                raise UnsupportedClientConfig(f"Некорректный порт в правиле: {value}")
            item["port"] = [int(value)]
        elif kind == "PROCESS-NAME":
            item["process_name"] = [value]
        elif kind == "PROCESS-NAME-WILDCARD":
            # Match the entire process name; RE2 syntax, unlike Python fnmatch's \Z.
            item["process_path_regex"] = [r"(?:^|[/\\])" + re.escape(value).replace(r"\*", ".*").replace(r"\?", ".") + "$"]
        elif kind in {"GEOSITE", "GEOIP"}:
            name = value.lower()
            if not re.fullmatch(r"[a-z0-9_-]+", name):
                raise UnsupportedClientConfig(f"Неподдерживаемая база {kind}: {value}")
            tag = f"{kind.lower()}-{name}"
            item["rule_set"] = [tag]
            rule_sets[tag] = {"type": "remote", "tag": tag, "format": "binary",
                              "url": f"https://raw.githubusercontent.com/SagerNet/sing-{kind.lower()}/rule-set/{tag}.srs",
                              "download_detour": proxy_tag, "update_interval": "1d"}
        else:
            raise UnsupportedClientConfig(f"Правило {kind} не поддерживается sing-box")
        route_rules.append(item)
    return route_rules, list(rule_sets.values())


def dns_server(address, tag):
    parsed = urlsplit(address if "://" in address else "udp://" + address)
    if parsed.scheme not in {"udp", "tcp", "tls", "https", "quic"} or not parsed.hostname:
        raise UnsupportedClientConfig(f"DNS {address} не поддерживается sing-box")
    result = {"type": parsed.scheme, "tag": tag, "server": parsed.hostname, "domain_resolver": "bootstrap"}
    if parsed.port:
        result["server_port"] = parsed.port
    if parsed.scheme == "https":
        result["path"] = parsed.path or "/dns-query"
    return result
