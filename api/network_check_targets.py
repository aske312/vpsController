"""Enumerate installed transports without exporting credentials or picking a consumer."""


def targets(images, modules, consumers, clients, profiles, kind, domain):
    direct = {str(row.get('id')): row for row in clients}
    connections = {f"mihomo:{profile.get('id')}:{row.get('id')}": row
                   for profile in profiles for row in profile.get('connections', [])}
    result = []
    installed = [('direct', key, row.get('name') or key) for key, row in images.items() if key != 'mihomo' and row.get('installed')]
    installed += [('mihomo', key, key.removeprefix('transport-').upper()) for key, enabled in modules.items() if enabled is True and key.startswith('transport-')]
    for owner, component, name in installed:
        matching = {}
        for consumer in consumers:
            if consumer.get('owner') != owner or consumer.get('kind') != kind or consumer.get('address') != domain:
                continue
            actual = direct.get(consumer['id'].removeprefix('direct:'), {}).get('protocol') if owner == 'direct' else connections.get(consumer['id'], {}).get('component')
            if actual == component:
                matching[consumer['id']] = consumer
        for consumer in list(matching.values()) or [None]:
            result.append({'id': f"{owner}:{component}:{consumer['id'] if consumer else '-'}", 'owner': owner,
                           'component': str(component), 'label': str(name), 'consumer_label': str(consumer.get('label') or '') if consumer else '',
                           'consumer_id': consumer['id'] if consumer else None,
                           'state': 'pending' if consumer else 'unavailable',
                           'message': '' if consumer else 'Нет настроенного подключения этого компонента к данному адресу'})
    return result
