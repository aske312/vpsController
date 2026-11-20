from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

import yaml

from tests.api.support import manager
import snell_delivery


class SnellDeliveryTests(unittest.TestCase):
    def test_carrier_has_a_stable_transport_name_and_is_absent_from_wildcard_selection(self):
        import routing_policy
        rows = self.connections()
        rendered = [(rows[0], 'Snell', None, None), (rows[1], None, 'User CDN label', None)]
        renamed = snell_delivery.transport_names(rendered)
        alias = renamed[1][2]
        self.assertTrue(alias.startswith('__312_transport_snell_'))
        reordered = snell_delivery.transport_names([(rows[1], None, 'Renamed CDN label', None), rendered[0]])
        self.assertEqual(reordered[0][2], alias)
        groups = routing_policy.groups(json.dumps({'groups': [{'name': 'Automatic', 'members': ['*']}], 'rules': []}), renamed, {})
        self.assertEqual(groups[0]['proxies'], ['Snell'])

    def connections(self):
        return [
            {'id': 'snell', 'component': 'transport-snell', 'device_id': 'phone',
             'settings': {'carrier_connection_id': 'carrier'}, 'credential': {'port': 33000}},
            {'id': 'carrier', 'component': 'transport-reality', 'device_id': 'phone',
             'settings': {'route_mode': 'cdn'}},
        ]

    def test_carrier_is_local_to_device_and_rejects_missing_invalid_or_conflicting_routes(self):
        for mode in ('cdn', 'tls'):
            items = self.connections(); items[1]['settings']['route_mode'] = mode
            snell_delivery.validate(items)
        cases = [lambda rows: rows.pop(), lambda rows: rows[1].update(device_id='other'),
                 lambda rows: rows[1].update(component='transport-snell'),
                 lambda rows: rows[1]['settings'].update(route_mode='direct'),
                 lambda rows: rows[0]['settings'].update(relay_host_id='a' * 32),
                 lambda rows: rows[0]['settings'].update(carrier_connection_id=True),
                 lambda rows: rows[0]['settings'].update(cdn_enabled=True)]
        for change in cases:
            rows = self.connections(); change(rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                snell_delivery.validate(rows)

    def test_export_uses_rendered_name_and_keeps_snell_endpoint_key_and_udp(self):
        items = self.connections()
        config = {'proxies': [{'name': 'Snell phone', 'type': 'snell', 'server': '8.8.8.8',
                              'port': 33000, 'psk': 'private-key', 'udp': True},
                             {'name': 'Actual CDN name', 'type': 'vless', 'server': 'cdn.example', 'port': 443}],
                  'rules': ['MATCH,Snell phone']}
        original = deepcopy(config)
        for mode in ('cdn', 'tls'):
            items[1]['settings']['route_mode'] = mode
            rendered = [(items[0], 'Snell phone', None, None),
                        (items[1], None, 'Actual CDN name' if mode == 'cdn' else None,
                         'Actual CDN name' if mode == 'tls' else None)]
            output = yaml.safe_load(snell_delivery.apply(yaml.safe_dump(config), rendered))
            self.assertEqual(output['proxies'][0], {**original['proxies'][0], 'dialer-proxy': 'Actual CDN name'})
            self.assertEqual(output['rules'], config['rules'])
        self.assertEqual(config, original)
        with self.assertRaises(ValueError):
            snell_delivery.apply(yaml.safe_dump(config), rendered[:1])

    def test_profile_admission_rejects_dangling_carrier_before_any_provisioning(self):
        rows = self.connections()[:1]
        values = [manager.ProfileConnectionInput(**{**row, 'name': row['id']}) for row in rows]
        with patch.object(manager, 'validate_channels'), patch.object(manager, 'manifest', return_value={'connection_settings': []}), \
                patch.object(manager, 'validate_connection', side_effect=lambda _, settings, **kw: settings), \
                patch.object(manager, 'provision') as provision:
            with self.assertRaises(manager.HTTPException) as error:
                manager.validate_connection_inputs(values)
            self.assertEqual(error.exception.status_code, 422)
            provision.assert_not_called()
        self.assertIn('carrier_connection_id', manager.client_connection_keys('transport-snell'))

    def test_relay_uses_only_tcp_including_udp_over_snell(self):
        item = self.connections()[0]
        item['settings'] = {'relay_host_id': 'a' * 32}
        control = SimpleNamespace(ensure=Mock(), validate_client_address=Mock(),
                                  endpoint=Mock(return_value=('relay.example', 20010)))
        self.assertEqual(manager.relay_connections.connection_groups(item), [[('tcp', 33000)]])
        manager.relay_connections.prepare_mihomo(control, [item], 'b' * 32)
        control.ensure.assert_called_once_with('a' * 32, [[('tcp', 33000)]], 'b' * 32)
        lines = ['  - name: Snell', '    server: "8.8.8.8"', '    port: 33000', '    udp: true']
        output = manager.relay_connections.redirect_mihomo_lines(control, 'transport-snell', item['settings'], lines)
        self.assertIn('    server: "relay.example"', output)
        self.assertIn('    port: 20010', output)
        self.assertIn('    udp: true', output)
        summary = manager.relay_connections.profile_summaries([{'id': 'p', 'connections': [item]}], [], '8.8.8.8')
        self.assertEqual([row['transport'] for row in summary[0]['channels']], ['tcp'])
