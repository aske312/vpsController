"""Snell delivery through an existing, device-local Xray TLS/CDN connection."""
import hashlib
import yaml


def validate(connections):
    by_id = {item['id']: item for item in connections if item.get('id')}
    for item in connections:
        settings = item.get('settings', {})
        carrier_id = settings.get('carrier_connection_id', '')
        if not isinstance(carrier_id, str):
            raise ValueError('Некорректный внешний канал Snell')
        if item['component'] == 'transport-snell':
            if (settings.get('route_mode', 'direct') != 'direct' or settings.get('cdn_enabled')
                    or settings.get('cdn_domain') or settings.get('tls_domain') or settings.get('relay_cdn')):
                raise ValueError('Snell v6 не поддерживает HTTP-CDN напрямую. Выберите внешний канал Xray TLS/CDN')
        if not carrier_id:
            continue
        carrier = by_id.get(carrier_id)
        if (item['component'] != 'transport-snell' or not carrier or carrier['component'] != 'transport-reality'
                or carrier.get('device_id') != item.get('device_id')
                or carrier.get('settings', {}).get('route_mode') not in {'cdn', 'tls'}):
            raise ValueError('Для Snell выберите существующее Xray TLS/CDN-подключение того же устройства')
        if settings.get('relay_host_id'):
            raise ValueError('Настройте Relay на внешнем Xray-канале или выберите прямую доставку Snell через Relay')


def remap(connections, identities):
    for item in connections:
        settings = item.get('settings', {})
        carrier = settings.get('carrier_connection_id')
        if carrier:
            if carrier not in identities:
                raise ValueError('Внешний канал Snell отсутствует в шаблоне устройства')
            settings['carrier_connection_id'] = identities[carrier]


def transport_names(rendered):
    carriers = {item.get('settings', {}).get('carrier_connection_id') for item, *_ in rendered} - {None, ''}
    if not carriers:
        return rendered
    validate([item for item, *_ in rendered])
    existing = {name for _, *names in rendered for name in names if name}
    result = []
    for item, direct, cdn, tls in rendered:
        if item.get('id') in carriers:
            alias = '__312_transport_snell_' + hashlib.sha256(item['id'].encode()).hexdigest()[:24]
            if alias in existing:
                raise ValueError('Имя внешнего транспортного канала занято')
            direct, cdn, tls = (alias if name else None for name in (direct, cdn, tls))
        result.append((item, direct, cdn, tls))
    return result


def apply(text, rendered):
    if not any(item.get('settings', {}).get('carrier_connection_id') for item, *_ in rendered):
        return text
    validate([item for item, *_ in rendered])
    names = {item['id']: (direct, cdn, tls) for item, direct, cdn, tls in rendered if item.get('id')}
    config = yaml.safe_load(text)
    nodes = {node['name']: node for node in config['proxies']}
    for item, direct, _, _ in rendered:
        carrier_id = item.get('settings', {}).get('carrier_connection_id')
        if carrier_id:
            _, cdn, tls = names[carrier_id]
            carrier = cdn or tls
            if not direct or carrier not in nodes:
                raise ValueError('Внешний канал Snell недоступен в этой подписке')
            nodes[direct]['dialer-proxy'] = carrier
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
