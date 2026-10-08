import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import yaml
from tests.api_portability_test import api
from connection_tuning import tuning_catalog, xhttp_extra
from fastapi.testclient import TestClient


class ConnectionTuningTests(unittest.TestCase):
    def test_presets_reset_owned_knobs_and_do_not_change_identity_or_local_access(self):
        forbidden = {'xray_sni', 'fingerprint', 'local_password', 'local_username', 'proxy_bind',
                     'local_auth_enabled', 'local_socks_port', 'local_http_port', 'awg_port'}
        for protocol, presets in tuning_catalog().items():
            self.assertGreaterEqual(len(presets), 5)
            self.assertEqual(len({p['id'] for p in presets}), len(presets))
            keys = set(presets[0]['settings'])
            for preset in presets:
                with self.subTest(protocol=protocol, preset=preset['id']):
                    self.assertEqual(set(preset['settings']), keys)
                    self.assertFalse(keys & forbidden)
                    settings = api.ClientSettings(**preset['settings'])
                    for key, value in preset['settings'].items():
                        self.assertEqual(getattr(settings, key), value)

    def test_all_presets_survive_personal_export_and_preserve_existing_accounts(self):
        valid = type('Result', (), {'returncode': 0, 'stderr': ''})()
        for protocol, presets in tuning_catalog().items():
            for preset in presets:
                with self.subTest(protocol=protocol, preset=preset['id']), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    config_path, settings_path = root/'config.json', root/'settings.json'
                    existing = {'name': 'existing', 'uuid': 'old', 'password': 'old'}
                    if protocol == 'tuic':
                        server = {'inbounds': [{'type': 'tuic', 'users': [existing]}]}
                    else:
                        existing = {'id': 'old', 'email': 'existing'}
                        server = {'inbounds': [{'protocol': 'vless', 'settings': {'clients': [existing]},
                                               'streamSettings': {'realitySettings': {'serverNames': ['example.com']}}}]}
                    config_path.write_text(json.dumps(server), encoding='utf-8')
                    settings_path.write_text(json.dumps({'port': 8443, 'domain': 'example.com', 'server_name': 'example.com',
                                                        'path': '/xhttp', 'password': 'public', 'short_id': 'ab'}), encoding='utf-8')
                    users_path = root/'users.json'
                    users_path.write_text('{"existing":"old"}', encoding='utf-8')
                    (root/'server.crt').write_text('certificate', encoding='utf-8')
                    def command(*args, **kwargs):
                        return 'enabled' if args[:2] == ('systemctl', 'is-enabled') else 'SHA256 Fingerprint=AB'
                    with patch.multiple(api, PUBLIC_IP='192.0.2.1', DATA_DIR=root, CLIENTS_FILE=root/'clients.json',
                                        HYSTERIA2_DIR=root, HYSTERIA2_SETTINGS=settings_path, HYSTERIA2_USERS=users_path,
                                        TUIC_DIR=root, TUIC_CONFIG=config_path, TUIC_SETTINGS=settings_path,
                                        XRAY_DIR=root, XRAY_CONFIG=config_path, XRAY_SETTINGS=settings_path), \
                         patch.object(api, 'run', side_effect=command), patch.object(api.subprocess, 'run', return_value=valid), \
                         patch.object(api.Path, 'exists', return_value=True), \
                         patch.object(api, 'certificate_server_name', return_value='example.com'):
                        result = api.create_client(api.ClientCreate(name='Preset QA', protocol=protocol,
                                                                    settings=api.ClientSettings(**preset['settings'])))
                    exported = yaml.safe_load(result['config']) if protocol == 'hysteria2' else json.loads(result['config'])
                    if protocol == 'hysteria2':
                        self.assertEqual(exported['quic']['keepAlivePeriod'], str(preset['settings']['hysteria_keepalive'])+'s')
                        self.assertEqual(exported['quic']['disablePathMTUDiscovery'], preset['settings']['disable_path_mtu_discovery'])
                        self.assertEqual(json.loads(users_path.read_text())['existing'], 'old')
                        self.assertNotIn('bandwidth', exported)
                    elif protocol == 'tuic':
                        outbound = exported['outbounds'][0]
                        self.assertEqual(outbound['congestion_control'], preset['settings']['congestion_control'])
                        self.assertEqual(outbound['udp_relay_mode'], preset['settings']['udp_relay_mode'])
                        self.assertFalse(outbound['zero_rtt_handshake'])
                        if preset['id'] == 'balanced': self.assertNotIn('disable_path_mtu_discovery', outbound)
                        self.assertEqual(json.loads(config_path.read_text())['inbounds'][0]['users'][0], existing)
                    else:
                        outbound = exported['outbounds'][0]
                        xhttp = outbound['streamSettings']['xhttpSettings']
                        self.assertEqual(xhttp['mode'], preset['settings']['xray_xhttp_mode'])
                        uri = result['profile']['delivery']['link']['uri']
                        self.assertEqual(json.loads(parse_qs(urlsplit(uri).query)['extra'][0]), xhttp['extra'])
                        self.assertEqual(outbound['mux']['concurrency'], -1)
                        self.assertEqual(json.loads(config_path.read_text())['inbounds'][0]['settings']['clients'][0], existing)

    def test_xmux_defaults_immutable_and_api_boundaries(self):
        self.assertNotIn('xmux', xhttp_extra(api.ClientSettings()))
        extra = xhttp_extra(api.ClientSettings(xray_xmux_profile='mobile'))
        extra['xmux']['maxConnections'] = 100
        self.assertEqual(xhttp_extra(api.ClientSettings(xray_xmux_profile='mobile'))['xmux']['maxConnections'], 0)
        client = TestClient(api.app)
        self.assertEqual(client.get('/api/clients/options').status_code, 401)
        with patch.object(api, 'ADMIN_USER', 'test'), patch.object(api, 'ADMIN_PASSWORD', 'secret'):
            for settings in ({'hysteria_keepalive': 0}, {'hysteria_keepalive': 31}, {'xray_xmux_profile': 'invalid'}):
                result = client.post('/api/clients', auth=('test', 'secret'), json={'name': 'Preset QA', 'protocol': 'hysteria2', 'settings': settings})
                self.assertEqual(result.status_code, 422)


if __name__ == '__main__':
    unittest.main()
