import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('relay_light_api', Path(__file__).resolve().parents[1] / 'api/main.py')
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class RelayLightAPITests(unittest.TestCase):
    def test_relay_reservations_block_new_direct_aliases_even_without_a_listener(self):
        import awg_ports
        import protocol_ports
        with patch.object(awg_ports.platform, 'system', return_value='Linux'), patch.object(awg_ports, 'relay_reserved_port', return_value=True), \
             patch.object(protocol_ports, 'relay_reserved_port', return_value=True):
            awg = api.awg_port_manager()
            direct = api.protocol_port_manager('xray')
            self.assertEqual(awg.status(20001)['status'], 'occupied')
            self.assertEqual(direct.status(20001)['status'], 'occupied')
            with self.assertRaises(awg_ports.PortError):
                with awg.reserve(20001): pass
            with self.assertRaises(awg_ports.PortError):
                with direct.reserve(20001): pass

    def test_light_endpoints_require_panel_auth_and_do_not_reuse_relay_token(self):
        client = TestClient(api.app)
        for method, path in [('GET', '/api/relay-agent'), ('POST', '/api/relay-agent/credentials'), ('POST', '/api/relay-agent/token/rotate')]:
            for headers in ({}, {'Authorization': 'Bearer relay-token'}):
                self.assertEqual(client.request(method, path, headers=headers).status_code, 401)

    def test_status_excludes_secret_but_explicit_reveal_is_uncached(self):
        client = TestClient(api.app)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'config.json').write_text('{}')
            def connection(reveal=False):
                value = {'agent_url': 'https://8.8.8.8:9443', 'certificate_sha256': 'a'*64}
                if reveal: value['token'] = 'relay-only-secret'
                return value
            with patch.object(api, 'ADMIN_PASSWORD', 'panel-password'), patch.object(api, 'ADMIN_USER', 'admin'), \
                 patch.object(api.relay_agent, 'CONFIG_DIR', root), patch.object(api.relay_agent, 'connection', side_effect=connection), \
                 patch.object(api.relay_agent, 'local_request', return_value={'items': []}):
                result = client.get('/api/relay-agent', auth=('admin','panel-password'))
                self.assertEqual(result.status_code, 200)
                self.assertNotIn('token', result.json())
                result = client.post('/api/relay-agent/credentials', auth=('admin','panel-password'))
                self.assertEqual(result.json()['token'], 'relay-only-secret')
                self.assertEqual(result.headers['cache-control'], 'no-store')


if __name__ == '__main__': unittest.main()
