"""Capture selection, opt-in VPN routing and DNS share one effective policy."""
from copy import deepcopy
import ipaddress
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port, wait_http_proxy, mihomo_readiness
from tests.api.test_mihomo_rules_runtime import profile
from profile_modes import VPN_SERVICES


class ProfileModeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='127.0.0.1'))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))
        self.enterContext(patch.object(manager, 'routing_settings', return_value={}))

    def config(self, routing, personal=()):
        item = profile()
        item['routing'] = deepcopy(routing)
        before = deepcopy(item)
        with patch.object(manager, 'personal_rule_lines_for_device', return_value=list(personal)):
            result = yaml.safe_load(manager.render_profile(item))
        self.assertEqual(item, before)
        return result

    def test_legacy_profiles_keep_capture_mode_and_vpn_default(self):
        for flags, tun in (({}, False), ({'tun_enabled': True}, True), ({'tun_force': True}, True)):
            with self.subTest(flags=flags):
                config = self.config(flags)
                self.assertEqual(config['tun']['enable'], tun)
                self.assertEqual(config['rules'][-1], 'MATCH,' + manager.MIHOMO_PROXY_GROUP)
                self.assertNotIn('connection_mode', config)
        self.assertEqual(self.config({'mode': 'global'})['mode'], 'global')

    def test_capture_and_routing_scope_are_independent(self):
        for mode in ('tun', 'proxy'):
            for scope in ('all', 'selected'):
                with self.subTest(mode=mode, scope=scope):
                    config = self.config({'connection_mode': mode, 'routing_scope': scope,
                                          'tun_enabled': True, 'tun_force': True, 'mode': 'global'})
                    self.assertEqual(config['tun']['enable'], mode == 'tun')
                    self.assertEqual(config['mode'], 'rule')
                    self.assertEqual(config['rules'][-1], 'MATCH,' + ('DIRECT' if scope == 'selected' else manager.MIHOMO_PROXY_GROUP))
                    self.assertEqual(config['mixed-port'], 7890)

    def test_proxy_removes_tun_hooks_and_process_routes_without_deleting_settings(self):
        routing = {'connection_mode': 'proxy', 'routing_scope': 'selected', 'vpn_services': 'youtube',
                   'tun_force': True, 'dns_hijack_force': True, 'tun_strict_route': True, 'dns_fake_ip': True}
        personal = ['PROCESS-NAME,telegram.exe,GATE.312', 'DOMAIN-SUFFIX,custom.test,GATE.312']
        config = self.config(routing, personal)
        self.assertFalse(config['tun']['auto-route'])
        self.assertFalse(config['tun']['strict-route'])
        self.assertNotIn('dns-hijack', config['tun'])
        self.assertNotIn('find-process-mode', config)
        self.assertEqual(config['dns']['enhanced-mode'], 'redir-host')
        self.assertFalse(any('PROCESS-' in rule for rule in config['rules']))
        self.assertIn('DOMAIN-SUFFIX,custom.test,' + manager.MIHOMO_PROXY_GROUP, config['rules'])
        routing['connection_mode'] = 'tun'
        restored = self.config(routing, personal)
        self.assertIn('dns-hijack', restored['tun'])
        self.assertEqual(restored['find-process-mode'], 'strict')
        self.assertIn('PROCESS-NAME,telegram.exe,' + manager.MIHOMO_PROXY_GROUP, restored['rules'])

    def test_only_chosen_services_use_vpn_and_explicit_exceptions_win(self):
        personal = ['DOMAIN,allowed.youtube.com,DIRECT']
        config = self.config({'routing_scope': 'selected', 'vpn_services': 'youtube',
                              'direct_ru_sites': True, 'direct_games_udp_enabled': True}, personal)
        rules = config['rules']
        selected = 'DOMAIN-SUFFIX,youtube.com,' + manager.MIHOMO_PROXY_GROUP
        self.assertIn(selected, rules)
        self.assertLess(rules.index(personal[0]), rules.index(selected))
        self.assertFalse(any('telegram.org' in rule for rule in rules))
        self.assertNotIn('NETWORK,UDP,DIRECT', rules)
        self.assertNotIn('GEOIP,RU,DIRECT,no-resolve', rules)
        self.assertEqual(rules[-1], 'MATCH,DIRECT')
        self.assertEqual(len(rules), len(set(rules)))
        self.assertEqual(self.config({'routing_scope': 'selected'})['rules'], ['MATCH,DIRECT'])
        self.assertFalse(any('youtube.com' in rule for rule in self.config({'vpn_services': 'youtube'})['rules']))

    def test_telegram_contains_valid_ipv4_and_ipv6_without_resolving_ip_rules(self):
        config = self.config({'routing_scope': 'selected', 'vpn_services': 'telegram'})
        for network in VPN_SERVICES['telegram']['networks']:
            parsed = ipaddress.ip_network(network, strict=True)
            kind = 'IP-CIDR6' if parsed.version == 6 else 'IP-CIDR'
            self.assertIn(f'{kind},{network},{manager.MIHOMO_PROXY_GROUP},no-resolve', config['rules'])

    def test_personal_application_exception_precedes_service_preset_but_not_protection(self):
        config = self.config({'connection_mode': 'tun', 'routing_scope': 'selected',
                              'vpn_services': 'youtube', 'block_privacy': True},
                             ['PROCESS-NAME,browser.exe,DIRECT'])
        rules = config['rules']
        self.assertLess(rules.index('PROCESS-NAME,browser.exe,DIRECT'),
                        rules.index('DOMAIN-SUFFIX,youtube.com,' + manager.MIHOMO_PROXY_GROUP))
        self.assertLess(rules.index('DOMAIN-SUFFIX,google-analytics.com,REJECT'),
                        rules.index('PROCESS-NAME,browser.exe,DIRECT'))

    def test_route_dns_uses_same_fallback_and_domain_selection(self):
        config = self.config({'connection_mode': 'tun', 'routing_scope': 'selected',
                              'vpn_services': 'youtube', 'route_dns': True})
        dns = config['dns']
        self.assertTrue(all(value.endswith('#DIRECT') for value in dns['nameserver']))
        self.assertTrue(all(value.endswith('#' + manager.MIHOMO_PROXY_GROUP)
                            for value in dns['nameserver-policy']['.youtube.com']))
        # An application rule cannot classify DNS before the process is known.
        config = self.config({'connection_mode': 'tun', 'routing_scope': 'selected', 'route_dns': True},
                             ['PROCESS-NAME,telegram.exe,GATE.312'])
        self.assertTrue(all(value.endswith('#' + manager.MIHOMO_PROXY_GROUP) for value in config['dns']['nameserver']))

    def test_device_override_does_not_change_other_devices_or_credentials(self):
        item = profile()
        item['routing'] = {'connection_mode': 'tun', 'routing_scope': 'selected', 'vpn_services': 'youtube'}
        item['devices'].append({'id': 'phone', 'routing': {'connection_mode': 'proxy', 'routing_scope': 'all'}})
        item['connections'].append({**deepcopy(item['connections'][0]), 'id': 'phone-connection', 'device_id': 'phone'})
        before = deepcopy(item)
        common = yaml.safe_load(manager.render_profile(item, 'common'))
        phone = yaml.safe_load(manager.render_profile(item, 'phone'))
        self.assertTrue(common['tun']['enable'])
        self.assertEqual(common['rules'][-1], 'MATCH,DIRECT')
        self.assertFalse(phone['tun']['enable'])
        self.assertEqual(phone['rules'][-1], 'MATCH,' + manager.MIHOMO_PROXY_GROUP)
        self.assertEqual(item, before)

    def test_api_rejects_unknown_modes_services_and_conflicting_catch_all(self):
        for value in ({'connection_mode': 'both'}, {'connection_mode': True}, {'routing_scope': []},
                      {'vpn_services': ['youtube']}, {'vpn_services': 'private-service'},
                      {'routing_scope': 'selected', 'rules': 'MATCH,GATE.312'}):
            with self.subTest(value=value), self.assertRaises(manager.HTTPException) as rejected:
                manager.validate_routing(value, current={})
            self.assertEqual(rejected.exception.status_code, 422)
        self.assertEqual(manager.validate_routing({'vpn_services': 'youtube, telegram,youtube'}, current={})['vpn_services'], 'youtube,telegram')

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires real Mihomo')
    def test_real_core_selected_routes_and_direct_fallback_without_public_traffic(self):
        class Endpoint(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                self.send_response(200, 'Connection established')
                self.end_headers()
                self.rfile.readline()
                while self.rfile.readline().strip():
                    pass
                body = b'via-vpn-fixture'
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
                self.wfile.flush()

            def do_GET(self):
                body = b'via-direct-fixture'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for capture in ('tun', 'proxy'):
            for scope in ('all', 'selected'):
                with self.subTest(capture=capture, scope=scope), tempfile.TemporaryDirectory() as folder, mihomo_readiness() as ready:
                    root = Path(folder)
                    config = self.config({'connection_mode': capture, 'routing_scope': scope,
                                          'vpn_services': 'youtube,telegram', 'strategy': 'select'},
                                         ['DOMAIN,allowed.youtube.com,DIRECT', 'DOMAIN,blocked.audit.test,REJECT'])
                    path = root / 'config.yaml'
                    path.write_text(yaml.safe_dump(config), encoding='utf-8')
                    checked = subprocess.run([os.environ['PRIVACY_MIHOMO_BIN'], '-t', '-d', folder, '-f', str(path)],
                                             capture_output=True, text=True, timeout=20)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    # Exercise generated routing through loopback; never install
                    # TUN routes or contact the real Telegram/YouTube endpoints.
                    cases = [('youtube.com', b'via-vpn-fixture'), ('91.108.4.1', b'via-vpn-fixture'),
                             ('allowed.youtube.com', b'via-direct-fixture'), ('blocked.audit.test', b'502'),
                             ('other.audit.test', b'via-direct-fixture' if scope == 'selected' else b'via-vpn-fixture')]
                    config['hosts'] = {name: '127.0.0.1' for name, _ in cases if name != '91.108.4.1'}
                    config['tun']['enable'] = False
                    config['dns']['enable'] = False
                    config['mixed-port'] = port = free_port(udp=True)
                    config['proxies'] = [{'name': config['proxies'][0]['name'], 'type': 'http',
                                          'server': '127.0.0.1', 'port': server.server_port}]
                    config['rules'].insert(0, f'DST-PORT,{ready},DIRECT')
                    path.write_text(yaml.safe_dump(config), encoding='utf-8')
                    with open(root / 'core.log', 'w') as log:
                        process = subprocess.Popen([os.environ['PRIVACY_MIHOMO_BIN'], '-d', folder, '-f', str(path)],
                                                   stdout=log, stderr=subprocess.STDOUT,
                                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        try:
                            wait_port(port, process)
                            wait_http_proxy(port, process, ready)
                            for domain, expected in cases:
                                with self.subTest(domain=domain), socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
                                    connection.settimeout(5)
                                    connection.sendall(f'GET http://{domain}:{server.server_port}/ HTTP/1.1\r\nHost: {domain}:{server.server_port}\r\nConnection: close\r\n\r\n'.encode())
                                    response = b''
                                    while chunk := connection.recv(8192):
                                        response += chunk
                                    self.assertIn(expected, response)
                        finally:
                            process.terminate()
                            process.wait(timeout=10)


class ProfileModeSubscriptionTests(unittest.TestCase):
    def test_hwid_registration_refresh_and_mode_edit_keep_identity_and_credentials(self):
        from tests.api.test_subscription_formats import SubscriptionFormatTests
        # Reuse the isolated subscription store and provisioning fixture.
        real_rules = manager.profile_rules
        SubscriptionFormatTests.setUp(self)
        self.enterContext(patch.object(manager, 'profile_rules', side_effect=real_rules))
        self.store[0]['routing'] = {'connection_mode': 'tun', 'routing_scope': 'selected', 'vpn_services': 'youtube'}
        fetch = lambda: SubscriptionFormatTests.fetch(self, 'ClashMi/1.0')
        first = yaml.safe_load(fetch().body)
        self.assertTrue(first['tun']['enable'])
        self.assertEqual(first['rules'][-1], 'MATCH,DIRECT')
        before = deepcopy(self.store[0]['connections'])
        device_id = self.store[0]['devices'][1]['id']
        self.assertEqual(yaml.safe_load(fetch().body)['rules'], first['rules'])
        devices = deepcopy(self.store[0]['devices'])
        devices[1]['routing']['connection_mode'] = 'proxy'
        manager.update_profile('profile', manager.ProfileUpdate(devices=[manager.ProfileDeviceInput(**row) for row in devices]))
        edited = yaml.safe_load(fetch().body)
        self.assertFalse(edited['tun']['enable'])
        self.assertEqual(edited['rules'][-1], 'MATCH,DIRECT')
        self.assertEqual(self.store[0]['devices'][1]['id'], device_id)
        self.assertEqual(self.store[0]['connections'], before)
        self.assertEqual(self.provision.call_count, 1)
