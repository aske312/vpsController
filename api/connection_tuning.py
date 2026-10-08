"""Client-local tuning: no listener, certificate or existing identity changes.

Sources: Hysteria Full-Client-Config, sing-box outbound/tuic and shared/quic,
XTLS/Xray-core discussion 4113 (XHTTP/XMUX). These are alternatives, not
measured speed rankings or guarantees against filtering.
"""
from copy import deepcopy


XMUX = {
    "default": {},  # Preserve the core's randomized, version-specific defaults.
    "mobile": {"maxConcurrency": "4-8", "maxConnections": 0, "cMaxReuseTimes": 0,
               "hMaxRequestTimes": "100-200", "hMaxReusableSecs": "180-300", "hKeepAlivePeriod": 10},
    "parallel": {"maxConcurrency": 1, "maxConnections": 0, "cMaxReuseTimes": 0,
                 "hMaxRequestTimes": 1, "hMaxReusableSecs": "1800-3000", "hKeepAlivePeriod": 0},
    "rotate": {"maxConcurrency": "8-16", "maxConnections": 0, "cMaxReuseTimes": 0,
               "hMaxRequestTimes": "100-200", "hMaxReusableSecs": "60-120", "hKeepAlivePeriod": 10},
}


def xhttp_extra(settings) -> dict:
    extra = {"xPaddingBytes": settings.xray_padding}
    xmux = ({"maxConcurrency": settings.xmux_concurrency, "maxConnections": settings.xmux_connections,
             "cMaxReuseTimes": settings.xmux_reuse, "hMaxRequestTimes": settings.xmux_requests,
             "hMaxReusableSecs": settings.xmux_seconds, "hKeepAlivePeriod": settings.xmux_keepalive}
            if settings.xray_xmux_profile == "custom" else XMUX[settings.xray_xmux_profile])
    if xmux:
        extra["xmux"] = deepcopy(xmux)
    return extra


def number_range(value: str, minimum: int, maximum: int) -> tuple[int, int]:
    import re
    if not re.fullmatch(r"\d+(?:-\d+)?", value):
        raise ValueError("Expected an integer or min-max range")
    parts = list(map(int, value.split('-')))
    lo, hi = parts[0], parts[-1]
    if not minimum <= lo <= hi <= maximum:
        raise ValueError(f"Range must be within {minimum}..{maximum}")
    return lo, hi


def quic_options(settings) -> dict:
    options = {}
    for field, target in (("quic_idle", "idle_timeout"), ("quic_keepalive", "keep_alive_period")):
        if getattr(settings, field): options[target] = f"{getattr(settings, field)}s"
    for field, target in (("quic_stream_window", "stream_receive_window"), ("quic_conn_window", "connection_receive_window")):
        if getattr(settings, field): options[target] = f"{getattr(settings, field) * 1048576} B"
    if settings.quic_streams: options["max_concurrent_streams"] = settings.quic_streams
    return options


def singbox_client(outbound: dict, inbound: dict, settings, endpoint: str) -> dict:
    client = {"log": {"level": "warn"}, "inbounds": [inbound], "outbounds": [outbound], "route": {"final": "connection-out"}}
    if settings.client_mode == "vpn":
        import ipaddress
        server = ipaddress.ip_address(endpoint)
        tun = {"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30"],
               "mtu": settings.mtu or 1280, "auto_route": True,
               "route_exclude_address": [f"{server}/{server.max_prefixlen}"]}
        if settings.route_mode == "all": tun["address"].append("fdfe:dcba:9876::1/126")
        if settings.route_mode == "custom": tun["route_address"] = [n.strip() for n in settings.allowed_ips.split(',')]
        client["inbounds"] = [tun]
        client["dns"] = {"servers": [{"type": "udp", "tag": "tunnel-dns", "server": settings.dns.split(',')[0].strip(), "detour": "connection-out"}], "final": "tunnel-dns", "strategy": "ipv4_only" if settings.route_mode == "ipv4" else "prefer_ipv4"}
        client["route"].update({"auto_detect_interface": True, "rules": [{"port": 53, "action": "hijack-dns"}]})
    return client


def tuning_catalog() -> dict:
    # Each preset resets all knobs it owns: switching does not accumulate tuning.
    reset_quic = {"quic_idle": 0, "quic_keepalive": 0, "quic_stream_window": 0, "quic_conn_window": 0, "quic_streams": 0}
    h = {**reset_quic, "hysteria_chrome_parrot": True, "hysteria_congestion": "bbr", "bbr_profile": "standard", "up_mbps": 0,
         "down_mbps": 0, "disable_loss_compensation": False, "fast_open": False,
         "lazy": False, "hysteria_keepalive": 10, "disable_path_mtu_discovery": False}
    t = {**reset_quic, "tuic_udp_over_stream": False, "congestion_control": "bbr", "heartbeat": "10s", "udp_relay_mode": "native",
         "network": "all", "initial_packet_size": 0, "disable_path_mtu_discovery": False}
    x = {"xray_xhttp_mode": "stream-one", "xray_xmux_profile": "default", "mux_enabled": False, "xray_padding": "100-1000"}

    def row(base, ident, label, description, **values):
        return {"id": ident, "label": label, "description": description, "settings": {**base, **values}}

    catalog = {
        "hysteria2": [
            row(h, "balanced", "Сбалансированный", "BBR; автоматическая оценка скорости, keepalive 10 с."),
            row(h, "mobile", "Мобильная сеть / проблемный MTU", "Консервативный BBR, keepalive 5 с, без увеличения QUIC-пакетов.", bbr_profile="conservative", hysteria_keepalive=5, disable_path_mtu_discovery=True),
            row(h, "reno", "Альтернативный контроль: Reno", "New Reno вместо BBR; для сравнения в сети с потерями.", hysteria_congestion="reno"),
            row(h, "responsive", "Быстрый старт запросов", "Fast Open сокращает ожидание ответа SOCKS; BBR остаётся стандартным.", fast_open=True),
            row(h, "on-demand", "Подключение по требованию", "Lazy connect: не подключаться до первого запроса; keepalive 20 с.", lazy=True, hysteria_keepalive=20),
        ],
        "tuic": [
            row(t, "balanced", "Сбалансированный", "BBR, нативный UDP, heartbeat 10 с."),
            row(t, "mobile", "Мобильная сеть / проблемный MTU", "QUIC-пакет 1200 B, heartbeat 5 с, без Path MTU discovery.", heartbeat="5s", initial_packet_size=1200, disable_path_mtu_discovery=True),
            row(t, "cubic", "Альтернативный контроль: CUBIC", "CUBIC вместо BBR; нативный UDP без дополнительной очереди.", congestion_control="cubic"),
            row(t, "reno", "Альтернативный контроль: Reno", "New Reno вместо BBR; остальные параметры стандартные.", congestion_control="new_reno"),
            row(t, "reliable-udp", "UDP с повторной доставкой", "UDP через надёжный QUIC stream; потери компенсируются ценой задержки.", udp_relay_mode="quic"),
        ],
        "xray": [
            row(x, "balanced", "Сбалансированный", "Stream one + штатный случайный XMUX; без обычного Mux."),
            row(x, "mobile", "Мобильная сеть / keepalive", "Stream one, XMUX 4–8 запросов, H2 keepalive 10 с.", xray_xmux_profile="mobile"),
            row(x, "packet-up", "Разделённые запросы: Packet up", "Отдельные POST-запросы на отправку; штатный XMUX.", xray_xhttp_mode="packet-up"),
            row(x, "stream-up", "Раздельные потоки: Stream up", "Отдельные потоки отправки и получения; штатный XMUX.", xray_xhttp_mode="stream-up"),
            row(x, "parallel", "Параллельные соединения", "XMUX: один запрос на H2-соединение; больше TCP/TLS-соединений.", xray_xmux_profile="parallel"),
            row(x, "rotate", "Короткое переиспользование", "XMUX 8–16 запросов; новые запросы меняют H2-соединение через 60–120 с.", xray_xmux_profile="rotate"),
        ],
    }
    # Mirror the effective XMUX preset in editable fields, not only in export.
    keys = {'xmux_concurrency': 'maxConcurrency', 'xmux_connections': 'maxConnections',
            'xmux_reuse': 'cMaxReuseTimes', 'xmux_requests': 'hMaxRequestTimes',
            'xmux_seconds': 'hMaxReusableSecs', 'xmux_keepalive': 'hKeepAlivePeriod'}
    defaults = {'maxConcurrency': '1', 'maxConnections': '0', 'cMaxReuseTimes': '0',
                'hMaxRequestTimes': '600-900', 'hMaxReusableSecs': '1800-3000', 'hKeepAlivePeriod': 0}
    for preset in catalog['xray']:
        values = XMUX[preset['settings']['xray_xmux_profile']] or defaults
        preset['settings'].update({field: values[key] if field == 'xmux_keepalive' else str(values[key]) for field, key in keys.items()})
    return catalog
