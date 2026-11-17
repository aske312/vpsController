from copy import deepcopy
import json
import http.server
import os
from pathlib import Path
import subprocess
import tempfile
import socket
import threading
import time
import unittest
from unittest.mock import patch

import yaml
from fastapi import HTTPException
from tests.api.support import manager, ROOT, free_port, wait_port, wait_http_proxy, mihomo_readiness
from tests.api.test_mihomo_rules_runtime import profile
import routing_policy as policy


class StructuredRoutingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='127.0.0.1'))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))

    def raw(self, groups=None, rules=None): return json.dumps({'groups': groups or [], 'rules': rules or []})

    def test_library_references_are_strict_unique_and_do_not_store_copied_conditions(self):
        for source in ('preset:missing', 'personal:../../private', 'domain:example.com'):
            with self.subTest(source=source), self.assertRaises(HTTPException):
                policy.parse(self.raw(rules=[{'kind': 'library', 'value': source}]))
        rule = {'kind': 'library', 'value': 'preset:direct_ru_sites', 'target': 'DIRECT'}
        with self.assertRaises(HTTPException): policy.parse(self.raw(rules=[rule, rule]))
        self.assertEqual(policy.rules(self.raw(rules=[rule])), [])
        self.assertEqual(policy.route_library(['AND,((NETWORK,UDP),(DOMAIN,site.test)),REJECT',
                                               'IP-CIDR,192.0.2.0/24,DIRECT,no-resolve'], 'policy:Work'),
                         ['AND,((NETWORK,UDP),(DOMAIN,site.test)),policy:Work',
                          'IP-CIDR,192.0.2.0/24,policy:Work,no-resolve'])

    def test_library_routes_follow_live_content_opt_in_and_original_priority(self):
        raw = self.raw([{'id': 'work', 'name': 'Work'}], [
            {'kind': 'library', 'value': 'preset:direct_ru_sites', 'target': 'Work'}])
        routing = {'structured_policy': raw, 'direct_ru_sites': True,
                   'direct_ru_sites_rules': 'DOMAIN-SUFFIX,work.test,DIRECT\nIP-CIDR,192.0.2.0/24,DIRECT,no-resolve',
                   '_personal_rule_lines': ['PROCESS-NAME,browser.exe,GATE.312']}
        lines = manager.profile_rules(routing)
        self.assertEqual(lines.count('DOMAIN-SUFFIX,work.test,policy:Work'), 1)
        self.assertNotIn('DOMAIN-SUFFIX,work.test,DIRECT', lines)
        self.assertLess(lines.index('DOMAIN-SUFFIX,work.test,policy:Work'), lines.index('PROCESS-NAME,browser.exe,GATE.312'))
        self.assertIn('IP-CIDR,192.0.2.0/24,policy:Work,no-resolve', lines)
        routing['direct_ru_sites_rules'] = 'DOMAIN,new-work.test,DIRECT'
        self.assertIn('DOMAIN,new-work.test,policy:Work', manager.profile_rules(routing))
        self.assertNotIn('DOMAIN-SUFFIX,work.test,policy:Work', manager.profile_rules(routing))
        routing[policy.SELECTION_KEY] = '{"work":false}'
        self.assertIn('DOMAIN,new-work.test,DIRECT', manager.profile_rules(routing))
        routing['direct_ru_sites'] = False
        self.assertNotIn('new-work.test', '\n'.join(manager.profile_rules(routing)))

    def test_personal_library_route_follows_device_selection_and_live_edits(self):
        rule = {'id': 'personal-123456abcdef', 'kind': 'domain', 'entries': ['work.test'], 'target': 'DIRECT'}
        item = profile()
        item['routing'] = {'structured_policy': self.raw([{'name': 'Work'}], [
            {'kind': 'library', 'value': 'personal:' + rule['id'], 'target': 'Work'}])}
        item['devices'][0]['personal_rule_ids'] = [rule['id']]
        item['devices'].append({'id': 'phone', 'os': 'android', 'routing': {}})
        item['connections'].append({**deepcopy(item['connections'][0]), 'id': 'phone-c', 'device_id': 'phone'})
        with patch.object(manager, 'personal_rules', return_value=[rule]):
            common = yaml.safe_load(manager.render_profile(item, 'common'))
            self.assertIn('DOMAIN-SUFFIX,work.test,policy:Work', common['rules'])
            self.assertNotIn('DOMAIN-SUFFIX,work.test,DIRECT', common['rules'])
            rule['entries'] = ['updated.test']
            common = yaml.safe_load(manager.render_profile(item, 'common'))
            self.assertIn('DOMAIN-SUFFIX,updated.test,policy:Work', common['rules'])
            self.assertNotIn('updated.test', '\n'.join(yaml.safe_load(manager.render_profile(item, 'phone'))['rules']))
            self.assertEqual(rule['target'], 'DIRECT')

    def test_library_udp_route_preserves_tunnel_exclusions_and_profile_gate(self):
        raw = self.raw([{'name': 'Work'}], [{'kind': 'library', 'value': 'preset:direct_games_udp_enabled', 'target': 'Work'}])
        routing = {'structured_policy': raw, 'direct_games_udp_enabled': True, 'udp_tunnel_exclusions': 'telegram'}
        lines = manager.profile_rules(routing)
        self.assertIn('NETWORK,UDP,policy:Work', lines)
        self.assertLess(lines.index('DOMAIN-SUFFIX,telegram.org,GATE.312'), lines.index('NETWORK,UDP,policy:Work'))
        self.assertNotIn('NETWORK,UDP,DIRECT', lines)
        routing['direct_games_udp_enabled'] = False
        self.assertNotIn('NETWORK,UDP,policy:Work', manager.profile_rules(routing))

    def test_cycles_unknown_references_and_injection_are_rejected(self):
        for groups in ([{'name': 'A', 'members': ['group:B']}, {'name': 'B', 'members': ['group:A']}],
                       [{'name': 'A', 'members': ['group:deleted']}], [{'name': 'A', 'members': ['DIRECT']}],
                       [{'name': 'DIRECT'}]):
            with self.assertRaises(HTTPException): policy.parse(self.raw(groups))
        for rule in ({'kind': 'domain', 'value': 'example.com,DIRECT'}, {'kind': 'geoip', 'value': 'http://evil'},
                     {'kind': 'domain', 'value': 'example.com', 'target': 'deleted'}):
            with self.assertRaises(HTTPException): policy.parse(self.raw(rules=[rule]))

    def test_order_named_groups_and_load_balance_algorithms_reach_export(self):
        item = profile()
        item['connections'].append({**deepcopy(item['connections'][0]), 'id': 'second', 'name': 'second'})
        raw = self.raw([{'name': 'Media', 'type': 'fallback', 'members': ['connection:second', 'connection:c']}], [
            {'kind': 'domain-suffix', 'value': 'example.com', 'target': 'Media'},
            {'kind': 'ip-cidr', 'value': '2001:db8::1/64', 'target': 'REJECT'}])
        item['routing'] = {'structured_policy': raw, 'rules': 'DOMAIN,legacy.test,DIRECT'}
        config = yaml.safe_load(manager.render_profile(item))
        group = config['proxy-groups'][1]
        self.assertEqual(group['name'], 'policy:Media')
        self.assertEqual(group['proxies'], [config['proxies'][1]['name'], config['proxies'][0]['name']])
        rules = config['rules']
        self.assertLess(rules.index('DOMAIN-SUFFIX,example.com,policy:Media'), rules.index('DOMAIN,legacy.test,DIRECT'))
        self.assertIn('IP-CIDR6,2001:db8::/64,REJECT,no-resolve', rules)
        self.assertEqual(rules[-1], 'MATCH,' + manager.MIHOMO_PROXY_GROUP)
        for algorithm in ('consistent-hashing', 'round-robin', 'sticky-sessions'):
            item['routing'].update(strategy='load-balance', load_balance_strategy=algorithm)
            self.assertEqual(yaml.safe_load(manager.render_profile(item))['proxy-groups'][0]['strategy'], algorithm)

    def test_missing_connection_and_unsupported_process_never_silently_downgrade(self):
        raw = self.raw([{'name': 'Media', 'members': ['connection:deleted']}])
        with self.assertRaises(HTTPException): policy.groups(raw, [], {})
        raw = self.raw(rules=[{'kind': 'process', 'value': 'browser.exe'}])
        for options in ({'mode': 'proxy'}, {'mode': 'tun', 'os': 'android'}):
            with self.assertRaises(HTTPException): policy.rules(raw, **options)
        self.assertEqual(policy.rules(raw, mode='tun', os='windows'), ['PROCESS-NAME,browser.exe,GATE.312'])
        empty = policy.groups(self.raw([{'name': 'Empty'}]), [], {})
        self.assertEqual(empty[0]['proxies'], ['REJECT'])

    def test_geo_catalog_and_legacy_profiles_preserve_behavior(self):
        raw = self.raw(rules=[{'kind': 'geoip', 'value': 'cn', 'target': 'DIRECT'}, {'kind': 'geosite', 'value': 'youtube'}])
        self.assertEqual(policy.rules(raw), ['RULE-SET,geoip-cn,DIRECT,no-resolve', 'RULE-SET,geosite-youtube,GATE.312'])
        item = profile()
        before = manager.render_profile(item)
        item['routing'] = {'structured_policy': '{}'}
        self.assertEqual(before, manager.render_profile(item))
        routing = {'structured_policy': self.raw(rules=[{'kind': 'domain', 'value': 'test.example'}]), 'routing_scope': 'selected'}
        self.assertEqual(manager.profile_final_rules(routing)[-1], 'MATCH,DIRECT')

    def test_inheritance_can_be_restored_without_resurrecting_old_common_policy(self):
        item = profile()
        item['routing'] = {'structured_policy': self.raw(rules=[{'kind': 'domain', 'value': 'old.test'}])}
        item['devices'][0]['routing'] = manager.validate_routing({'structured_policy': '@inherit', 'routing_scope': 'selected'}, current={})
        self.assertNotIn('structured_policy', manager.device_routing(item, 'common'))
        self.assertNotIn('old.test', manager.render_profile(item))

    def test_selection_is_strict_and_disabled_rules_keep_dependency_groups(self):
        for value in ('[]', '{"Media": 1}', '{"../group": false}', 'null', False):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                manager.validate_routing({policy.SELECTION_KEY: value}, current={})
        raw = self.raw([
            {'id': 'media-id', 'name': 'Media', 'members': ['group:Reserve']},
            {'id': 'reserve-id', 'name': 'Reserve', 'members': ['*']},
        ], [
            {'kind': 'domain', 'value': 'media.test', 'target': 'Media'},
            {'kind': 'domain', 'value': 'reserve.test', 'target': 'Reserve'},
            {'kind': 'domain', 'value': 'direct.test', 'target': 'DIRECT'},
        ])
        result = policy.parse(policy.effective({'structured_policy': raw, policy.SELECTION_KEY: '{"reserve-id":false,"@rules":false}'}))
        self.assertEqual([rule.value for rule in result.rules], ['media.test'])
        self.assertEqual([group.name for group in result.groups], ['Media', 'Reserve'])

    def test_global_definitions_stay_live_while_profile_selection_changes_export(self):
        raw = self.raw([{'id': 'media-id', 'name': 'Media', 'members': ['*']}, {'id': 'work-id', 'name': 'Work', 'members': ['*']}], [
            {'kind': 'domain', 'value': 'media.test', 'target': 'Media'},
            {'kind': 'domain', 'value': 'work.test', 'target': 'Work'},
            {'kind': 'domain', 'value': 'direct.test', 'target': 'DIRECT'},
        ])
        defaults = {**manager.routing_settings(), 'structured_policy': raw}
        item = profile()
        item['routing'] = {policy.SELECTION_KEY: '{"media-id":false}'}
        with patch.object(manager, 'routing_settings', return_value=defaults):
            config = yaml.safe_load(manager.render_profile(item))
            self.assertNotIn('DOMAIN,media.test,policy:Media', config['rules'])
            self.assertIn('DOMAIN,work.test,policy:Work', config['rules'])
            self.assertIn('DOMAIN,direct.test,DIRECT', config['rules'])
            self.assertNotIn('policy:Media', [group['name'] for group in config['proxy-groups']])
            defaults['structured_policy'] = raw.replace('work.test', 'new-work.test').replace('Media', 'Video')
            config = yaml.safe_load(manager.render_profile(item))
            self.assertIn('DOMAIN,new-work.test,policy:Work', config['rules'])
            self.assertNotIn('policy:Video', [group['name'] for group in config['proxy-groups']])
        self.assertNotIn('structured_policy', item['routing'])

    def test_device_selection_overrides_only_one_group_and_keeps_profile_choices(self):
        item = profile()
        item['routing'] = {policy.SELECTION_KEY: '{"Media":false,"Work":false}'}
        item['devices'] += [{'id': 'phone', 'os': 'android', 'routing': {policy.SELECTION_KEY: '{"Media":true}'}}]
        common = json.loads(manager.device_routing(item, 'common')[policy.SELECTION_KEY])
        phone = json.loads(manager.device_routing(item, 'phone')[policy.SELECTION_KEY])
        self.assertEqual(common, {'Media': False, 'Work': False})
        self.assertEqual(phone, {'Media': True, 'Work': False})
        del item['devices'][1]['routing'][policy.SELECTION_KEY]
        self.assertEqual(json.loads(manager.device_routing(item, 'phone')[policy.SELECTION_KEY]), common)

    def test_each_group_can_override_health_checks_in_exact_mihomo_units(self):
        raw = self.raw([
            {'name': 'Reserve', 'timeout': 5000, 'max_failed_times': 1, 'interval': 300, 'url': 'https://health.test/generate_204'},
            {'name': 'Fast', 'type': 'url-test', 'tolerance': 50},
            {'name': 'Manual', 'type': 'select', 'timeout': 5000},
            {'name': 'Balance', 'type': 'load-balance', 'strategy': 'round-robin'},
        ])
        result = policy.groups(raw, [({'id': 'c'}, 'Node', None, None)], {'health_timeout': 2000, 'interval': 60, 'max_failed_times': 3})
        self.assertEqual({key: result[0][key] for key in ('timeout', 'interval', 'max-failed-times', 'url')},
                         {'timeout': 5000, 'interval': 300, 'max-failed-times': 1, 'url': 'https://health.test/generate_204'})
        self.assertEqual(result[1]['timeout'], 2000)
        self.assertEqual(result[1]['interval'], 60)
        self.assertEqual(result[1]['tolerance'], 50)
        self.assertNotIn('timeout', result[2])
        self.assertEqual(result[3]['strategy'], 'round-robin')
        for patch in ({'timeout': 5}, {'interval': 0}, {'max_failed_times': True}, {'tolerance': -1},
                      {'url': 'file:///private'}, {'url': 'https://user:password@health.test/'}, {'strategy': 'random'}):
            with self.subTest(patch=patch), self.assertRaises(HTTPException): policy.parse(self.raw([{'name': 'Invalid', **patch}]))

    def test_disabled_process_rules_are_not_validated_as_active_on_mobile(self):
        item = profile()
        item['devices'] += [{'id': 'phone', 'os': 'android', 'routing': {policy.SELECTION_KEY: '{"@rules":false}'}}]
        defaults = {'connection_mode': 'tun', 'structured_policy': self.raw(rules=[{'kind': 'process', 'value': 'browser.exe'}])}
        manager.validate_client_capabilities(item, defaults=defaults)
        item['devices'][1]['routing'][policy.SELECTION_KEY] = '{"@rules":true}'
        with self.assertRaises(HTTPException): manager.validate_client_capabilities(item, defaults=defaults)

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires packaged Mihomo')
    def test_packaged_core_validates_each_group_algorithm_and_client_tun(self):
        for algorithm in ('consistent-hashing', 'round-robin', 'sticky-sessions'):
            item = profile()
            item['routing'] = {'strategy': 'load-balance', 'load_balance_strategy': algorithm,
                'direct_ru_banks': True, 'direct_ru_banks_rules': 'DOMAIN-SUFFIX,bank.test,DIRECT',
                'connection_mode': 'tun', 'structured_policy': self.raw([
                    {'name': 'Reserve', 'type': 'fallback'}, {'name': 'Latency', 'type': 'url-test', 'members': ['group:Reserve']},
                    {'name': 'Manual', 'type': 'select'}, {'name': 'Balance', 'type': 'load-balance', 'strategy': algorithm}], [
                    {'kind': 'library', 'value': 'preset:direct_ru_banks', 'target': 'Balance'},
                    {'kind': 'domain', 'value': 'example.com', 'target': 'Balance'}, {'kind': 'ip-cidr', 'value': '2001:db8::/32', 'target': 'REJECT'}])}
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'config.yaml'
                target.write_text(manager.render_profile(item), encoding='utf-8')
                result = subprocess.run([os.environ['PRIVACY_MIHOMO_BIN'], '-t', '-d', folder, '-f', str(target)], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires packaged Mihomo')
    def test_real_traffic_fails_over_and_never_escapes_via_direct(self):
        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                if not self.server.healthy:
                    self.send_error(503)
                    return
                self.send_response(200, 'Connection established')
                self.end_headers()
                request = self.rfile.readline()
                while self.rfile.readline().strip(): pass
                if b'/health ' in request:
                    self.wfile.write(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                else:
                    marker = self.server.marker
                    self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(marker)).encode() + b'\r\nConnection: close\r\n\r\n' + marker)
                self.wfile.flush()
            def log_message(self, *_): pass
        servers = []
        try:
            for marker in (b'via-primary', b'via-reserve'):
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
                server.daemon_threads = True
                server.healthy, server.marker = True, marker
                servers.append(server)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            item = profile()
            item['connections'].append({**deepcopy(item['connections'][0]), 'id': 'second', 'name': 'second'})
            item['routing'] = {'structured_policy': self.raw([{'name': 'Reserve', 'type': 'fallback', 'members': ['connection:c', 'connection:second']}],
                [{'kind': 'domain', 'value': 'routing.audit.test', 'target': 'Reserve'}])}
            with tempfile.TemporaryDirectory() as folder, mihomo_readiness() as ready:
                config = yaml.safe_load(manager.render_profile(item))
                port = free_port(udp=True)
                config.update({'mixed-port': port, 'external-controller': '', 'log-level': 'silent'})
                config['tun']['enable'] = False
                config['dns']['enable'] = False
                config['rules'].insert(0, f'DST-PORT,{ready},DIRECT')
                config['proxies'] = [{'name': entry['name'], 'type': 'http', 'server': '127.0.0.1', 'port': server.server_port} for entry, server in zip(config['proxies'], servers)]
                for group in config['proxy-groups']:
                    group.update(url='http://health.test/health', interval=1, timeout=500, **{'max-failed-times': 1})
                target = Path(folder) / 'config.yaml'
                target.write_text(yaml.safe_dump(config), encoding='utf-8')
                process = subprocess.Popen([os.environ['PRIVACY_MIHOMO_BIN'], '-d', folder, '-f', str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    wait_port(port, process)
                    wait_http_proxy(port, process, ready)
                    def request():
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                                client.settimeout(2)
                                client.sendall(b'GET http://routing.audit.test:80/ HTTP/1.1\r\nHost: routing.audit.test\r\nConnection: close\r\n\r\n')
                                data = b''
                                while chunk := client.recv(8192): data += chunk
                                return data
                        except OSError: return b''
                    def expect(marker):
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            if marker in request(): return
                            time.sleep(.1)
                        self.fail('Fallback did not reach ' + marker.decode())
                    expect(b'via-primary')
                    servers[0].healthy = False
                    expect(b'via-reserve')
                    servers[1].healthy = False
                    time.sleep(1.2)
                    response = request()
                    self.assertNotIn(b'via-', response)
                    self.assertNotIn(b'proxy-ready', response)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
