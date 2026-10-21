import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests.api.support import api


class NetworkCheckTargetsTests(unittest.TestCase):
    def test_every_installed_transport_is_reported_and_all_exact_consumers_are_kept(self):
        consumers = [
            {'id': 'direct:a', 'owner': 'direct', 'kind': 'udp_relay', 'address': 'node.example', 'label': 'Phone'},
            {'id': 'direct:b', 'owner': 'direct', 'kind': 'udp_relay', 'address': 'node.example', 'label': 'Laptop'},
            {'id': 'mihomo:p:c', 'owner': 'mihomo', 'kind': 'udp_relay', 'address': 'node.example', 'label': 'Profile'},
            {'id': 'direct:other', 'owner': 'direct', 'kind': 'udp_relay', 'address': 'other.example', 'label': 'Other'},
        ]
        images = {'tuic': {'installed': True, 'name': 'TUIC'}, 'wg': {'installed': True, 'name': 'WireGuard'}, 'awg': {'installed': False}, 'mihomo': {'installed': True}}
        rows = api.network_check_targets.targets(images, {'transport-tuic': True, 'transport-wg': True, 'dns-private': True, 'transport-awg': False}, consumers + [consumers[0]],
            [{'id': 'a', 'protocol': 'tuic', 'token': 'must-not-export'}, {'id': 'b', 'protocol': 'tuic'}, {'id': 'other', 'protocol': 'tuic'}],
            [{'id': 'p', 'connections': [{'id': 'c', 'component': 'transport-tuic', 'credential': {'secret': 'must-not-export'}}]}], 'udp_relay', 'node.example')
        self.assertEqual(len(rows), 5)
        self.assertEqual({row['consumer_id'] for row in rows if row['state'] == 'pending'}, {'direct:a', 'direct:b', 'mihomo:p:c'})
        self.assertEqual(sum(row['state'] == 'unavailable' for row in rows), 2)
        self.assertNotIn('must-not-export', json.dumps(rows))
        self.assertNotIn('other.example', json.dumps(rows))
        self.assertEqual(len({row['id'] for row in rows}), len(rows))

    def test_missing_or_retiring_connections_are_not_chosen_as_other_consumers(self):
        rows = api.network_check_targets.targets({}, {'transport-reality': True},
            [{'id': 'mihomo:p:old', 'owner': 'mihomo', 'kind': 'cdn', 'address': 'node.example'}], [],
            [{'id': 'p', 'connections': [], 'retiring_connections': [{'id': 'old', 'component': 'transport-reality'}]}], 'cdn', 'node.example')
        self.assertEqual(rows[0]['state'], 'unavailable')
        self.assertIsNone(rows[0]['consumer_id'])

    def test_target_inventory_requires_auth_and_does_not_start_a_probe(self):
        with patch.object(api, 'execute_network_probe') as execute:
            self.assertEqual(TestClient(api.app).get('/api/network/endpoints/check-targets?kind=cdn&domain=node.example').status_code, 401)
            execute.assert_not_called()
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = root / 'state.json'; state.write_text('{"modules":{"transport-tuic":true}}')
        for name, value in [('DATA_DIR', root), ('MIHOMO_STATE_FILE', state)]: self.enterContext(patch.object(api, name, value))
        self.enterContext(patch.object(api, 'protocol_image_manifests', return_value={'wg': {'installed': True, 'name': 'WG'}}))
        self.enterContext(patch.object(api, 'network_consumers', return_value=[]))
        self.enterContext(patch.object(api, 'read_clients', return_value=[]))
        self.enterContext(patch.object(api, 'network_endpoint_check', return_value={'status': 'unchecked', 'ready': False}))
        result = api.get_network_check_targets('udp_relay', 'NODE.example', None)
        self.assertEqual(result['domain'], 'node.example')
        self.assertEqual(len(result['items']), 2)
        state.write_text('not json')
        with self.assertRaises(api.HTTPException) as error: api.get_network_check_targets('udp_relay', 'node.example', None)
        self.assertEqual(error.exception.status_code, 503)
