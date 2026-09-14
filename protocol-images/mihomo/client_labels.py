"""Public connection names shared by all subscription exporters."""


def connection_label(connection, index, variant="direct", direct_settings=None):
    component = connection["component"]
    protocols = {"transport-wg": "WireGuard · UDP", "transport-awg": "AmneziaWG · UDP",
                 "transport-shadowsocks": "Shadowsocks · TCP/UDP", "transport-hysteria2": "Hysteria2 · QUIC",
                 "transport-tuic": "TUIC · QUIC"}
    if component != "transport-reality":
        return f"{index} · {protocols.get(component, component)}"
    credential = connection.get("credential", {})
    if variant == "direct":
        effective = dict(credential)
        if not credential.get("direct_tag") and direct_settings:
            effective.update(direct_settings())
        transport = effective.get("transport", "xhttp")
        security = "REALITY"
    else:
        transport = credential.get(f"{variant}_transport", "websocket" if variant == "cdn" else "xhttp")
        security = "TLS · CDN" if variant == "cdn" else "TLS"
    transport = {"tcp": "TCP", "raw": "TCP", "websocket": "WebSocket", "grpc": "gRPC", "xhttp": "XHTTP", "httpupgrade": "HTTPUpgrade"}.get(transport, transport)
    return f"{index} · VLESS · {transport} · {security}"
