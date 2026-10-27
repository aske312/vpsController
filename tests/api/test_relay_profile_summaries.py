import unittest

from tests.api.support import api


class RelayProfileSummaryTests(unittest.TestCase):
    def test_channels_keep_profile_totals_and_share_socket_references(self):
        connections = [
            {'id': 'a', 'name': 'Private', 'component': 'transport-shadowsocks',
             'credential': {'port': 31000, 'password': 'secret'}, 'settings': {'relay_host_id': 'host'}},
            {'id': 'b', 'component': 'transport-wg', 'credential': {'port': 31000}, 'settings': {}},
            {'id': 'c', 'component': 'transport-wg', 'credential': {'port': 31000}, 'settings': {'relay_host_id': 'host'}},
        ]
        hosts = [{'id': 'host', 'routes': [
            {'id': 'tcp', 'transport': 'tcp', 'target_port': 31000, 'target_ip': 'origin'},
            {'id': 'udp', 'transport': 'udp', 'target_port': 31000, 'target_ip': 'origin'},
            {'id': 'foreign', 'transport': 'udp', 'target_port': 31000, 'target_ip': 'other'},
        ]}]
        result = api.relay_connections.profile_summaries([{'id': 'p', 'name': 'Phone', 'connections': connections}], hosts, 'origin')
        channels = result[0]['channels']
        self.assertEqual(len(channels), 4)
        self.assertEqual(sum(row['transport'] == 'udp' for row in channels), 3)
        self.assertEqual(sum(row['transport'] == 'udp' and row['host_id'] == 'host' for row in channels), 2)
        self.assertEqual(channels[-1]['route_ids'], ['udp'])
        self.assertNotIn('secret', str(result))
        self.assertNotIn('credential', str(result))

    def test_cdn_route_does_not_borrow_same_port_from_origin_or_another_domain(self):
        connection = {'id': 'cdn', 'component': 'transport-reality',
                      'credential': {'cdn_enabled': True}, 'settings': {'relay_host_id': 'host',
                      'route_mode': 'cdn', 'relay_cdn': True, 'relay_cdn_mode': 'before_cdn', 'cdn_domain': 'cdn.example'}}
        routes = [{'id': domain or 'origin', 'transport': 'tcp', 'target_port': 443, 'target_ip': 'origin',
                   **({'cdn_domain': domain} if domain else {})} for domain in ('cdn.example', 'other.example', '')]
        result = api.relay_connections.profile_summaries([{'id': 'p', 'connections': [connection]}], [{'id': 'host', 'routes': routes}], 'origin')
        self.assertEqual(result[0]['channels'][0]['route_ids'], ['cdn.example'])


if __name__ == '__main__':
    unittest.main()
