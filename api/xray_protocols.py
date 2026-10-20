"""Protocol identities shared by Xray listeners and client exporters.

The absent discriminator is the persisted VLESS format, not a migration.
"""
PROTOCOLS = {'vless', 'vmess', 'trojan'}


def protocol(credential):
    value = credential.get('xray_protocol', 'vless')
    if value not in PROTOCOLS:
        raise ValueError('Unsupported Xray protocol')
    return value


def client_identity(credential):
    return credential['password'] if protocol(credential) == 'trojan' else credential['uuid']


def server_settings(kind, identity, email, decryption='none'):
    if kind not in PROTOCOLS:
        raise ValueError('Unsupported Xray protocol')
    client = {'password' if kind == 'trojan' else 'id': identity, 'email': email}
    if kind == 'vless':
        client['flow'] = ''
    value = {'clients': [client]}
    if kind == 'vless':
        value['decryption'] = decryption
    return value


def outbound_settings(credential, address, port):
    kind = protocol(credential)
    if kind == 'trojan':
        return {'servers': [{'address': address, 'port': port, 'password': credential['password']}]}
    user = {'id': credential['uuid']}
    if kind == 'vless':
        user['encryption'] = credential.get('encryption') or 'none'
    else:
        user.update(security='auto', alterId=0)
    return {'vnext': [{'address': address, 'port': port, 'users': [user]}]}


def mihomo_auth(credential):
    kind = protocol(credential)
    if kind == 'trojan':
        return {'password': credential['password']}
    result = {'uuid': credential['uuid']}
    if kind == 'vmess':
        result.update(alterId=0, cipher='auto')
    elif credential.get('encryption'):
        result['encryption'] = credential['encryption']
    return result
