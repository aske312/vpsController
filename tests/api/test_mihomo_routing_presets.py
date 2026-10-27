"""Profile/device routing for QUIC, public services and threat feeds."""
from copy import deepcopy
import json
import http.server
import os
from pathlib import Path
import subprocess
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT, free_port, wait_port, wait_http_proxy, mihomo_readiness
from tests.api.test_mihomo_rules_runtime import profile
from routing_presets import quic_rule, quic_domains, QUIC_DEFAULT_GROUPS, QUIC_GROUPS, QUIC_AVAILABLE_RULES, SECURITY_PROVIDERS, SERVICE_RULES
from client_singbox import singbox_rules, UnsupportedClientConfig
from client_capabilities import compatible_routing, device_capabilities


class RoutingPresetTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='127.0.0.1'))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))

    def test_schema_validation_and_safe_defaults(self):
        result = manager.validate_routing({'quic_compatibility': True, 'block_malware': True,
                                          'direct_ru_services': True, 'quic_domains': 'Video.Example.org, видео.рф\nvideo.example.org'}, current={})
        self.assertEqual(result['quic_domains'], 'video.example.org\nxn--b1aedk6a.xn--p1ai')
        for domain in ['https://example.com', 'example.com:443', '*.example.com', '127.0.0.1', 'bad..example', 'localhost']:
            with self.subTest(domain=domain), self.assertRaises(manager.HTTPException):
                manager.validate_routing({'quic_domains': domain}, current={})
        for key in ['quic_compatibility', 'block_malware', 'direct_ru_services']:
            self.assertFalse(manager.routing_defaults()[key])
            with self.assertRaises(manager.HTTPException):
                manager.validate_routing({key: 'yes'}, current={})
        with self.assertRaises(ValueError):
            quic_domains('\n'.join(f'domain{i}.example' for i in range(257)))

    def test_russian_sites_do_not_bypass_unrelated_mail_com(self):
        rules = manager.profile_rules({'direct_ru_sites': True})
        self.assertNotIn('DOMAIN-SUFFIX,mail.com,DIRECT', rules)
        self.assertIn('DOMAIN-SUFFIX,ru,DIRECT', rules)
        translated, _ = singbox_rules(rules)
        self.assertFalse(any('mail.com' in rule.get('domain_suffix', []) for rule in translated))

    def test_singbox_aggregate_ads_excludes_named_group_resources(self):
        rules, providers = singbox_rules(['GEOSITE,category-ads-all,REJECT',
                                         'DOMAIN-SUFFIX,advertising.com,REJECT'])
        base, named = rules
        self.assertEqual(base['mode'], 'and')
        self.assertEqual(base['rules'][0], {'rule_set': ['geosite-category-ads-all']})
        excluded = base['rules'][1]
        self.assertTrue(excluded['invert'])
        for domain in ['advertising.com', 'adform.com', 'ero-advertising.com', 'eroadvertising.com']:
            self.assertIn(domain, excluded['domain_suffix'])
        self.assertNotIn('youtube.com', excluded['domain_suffix'])
        self.assertEqual(named, {'domain_suffix': ['advertising.com'], 'outbound': 'block'})
        self.assertEqual(len(providers), 1)

    def test_profile_inheritance_device_override_and_no_mutation(self):
        item = profile()
        item['routing'] = {'quic_compatibility': True, 'block_malware': True, 'direct_ru_services': True,
                           'quic_domains': 'video.example.org'}
        original = deepcopy(item)
        config = yaml.safe_load(manager.render_profile(deepcopy(item)))
        self.assertIn(quic_rule('video.example.org'), config['rules'])
        self.assertIn('DOMAIN-SUFFIX,t2.ru,DIRECT', config['rules'])
        self.assertEqual(set(config['rule-providers']), set(SECURITY_PROVIDERS))
        self.assertEqual(item, original)
        item['devices'][0]['routing'] = {'quic_compatibility': False, 'block_malware': False, 'direct_ru_services': False}
        disabled = yaml.safe_load(manager.render_profile(item))
        self.assertNotIn('rule-providers', disabled)
        self.assertFalse(any(rule.startswith('AND,') for rule in disabled['rules']))
        self.assertNotIn('DOMAIN-SUFFIX,t2.ru,DIRECT', disabled['rules'])
        with patch.object(manager, 'routing_settings', return_value=original['routing']):
            empty = yaml.safe_load(manager.render_profile(profile()))
        self.assertNotIn('rule-providers', empty)
        self.assertFalse(any(rule.startswith('AND,') for rule in empty['rules']))

    def test_quic_matches_domain_and_udp443_only_in_singbox(self):
        item = profile()
        item['routing'] = {'quic_compatibility': True, 'quic_domains': 'video.example.org'}
        config = json.loads(manager.render_client_profile(item, 'common', 'singbox')[0])
        blocked = [rule for rule in config['route']['rules'] if rule.get('outbound') == 'block']
        self.assertTrue(blocked)
        for rule in blocked:
            self.assertEqual(rule['network'], ['udp'])
            self.assertEqual(rule['port'], [443])
            self.assertTrue(rule['domain_suffix'])
        self.assertIn('video.example.org', {domain for rule in blocked for domain in rule['domain_suffix']})
        self.assertNotIn('NETWORK,UDP,REJECT', manager.profile_rules(item['routing']))
        with self.assertRaises(UnsupportedClientConfig):
            singbox_rules(['AND,((NETWORK,UDP),(DST-PORT,444)),REJECT'])

    def test_video_groups_are_selectable_and_preserve_saved_selections(self):
        catalog = (ROOT / 'src/features/mihomo/catalog.ts').read_text(encoding='utf-8')
        catalog = catalog.split('  quic_compatibility: [', 1)[1].split('  direct_ru_services:', 1)[0]
        for group, domains in QUIC_GROUPS.items():
            with self.subTest(group=group):
                row = next(line for line in catalog.splitlines() if f'id: "{group}"' in line)
                for domain in domains:
                    self.assertIn(f'"DOMAIN-SUFFIX,{domain})"', row)
                selected = [quic_rule(domain) for domain in domains]
                routing = {'quic_compatibility': True, 'quic_compatibility_rules': '\n'.join(selected)}
                emitted = manager.profile_rules(routing)
                self.assertEqual([rule for rule in emitted if rule.startswith('AND,')], selected)
                translated, providers = singbox_rules(selected)
                self.assertEqual(providers, [])
                self.assertEqual({d for rule in translated for d in rule['domain_suffix']}, set(domains))
                for rule in translated:
                    self.assertEqual(rule['network'], ['udp'])
                    self.assertEqual(rule['port'], [443])
        schema = next(row for row in manager.routing_rule_lists({}) if row['id'] == 'quic_compatibility')
        self.assertEqual(set(schema['available_rules'].splitlines()), set(QUIC_AVAILABLE_RULES))
        self.assertEqual(schema['default_rules'].splitlines(), [quic_rule(d) for group in ('youtube', 'instagram', 'media_library') for d in QUIC_GROUPS[group]])
        self.assertEqual(QUIC_DEFAULT_GROUPS, ('youtube', 'instagram', 'media_library'))
        self.assertFalse({'vk_video', 'rutube', 'ok_video', 'kinopoisk', 'okko', 'wink'} & set(QUIC_GROUPS))
        legacy = quic_rule('rutube.ru')
        self.assertIn(legacy, manager.profile_rules({'quic_compatibility': True, 'quic_compatibility_rules': legacy}))
        for shared_cdn in ['cloudfront.net', 'akamaized.net', 'edgesuite.net', 'yandex.ru', 'googleapis.com']:
            self.assertNotIn(quic_rule(shared_cdn), [quic_rule(d) for key, domains in QUIC_GROUPS.items()
                                                   if key != 'google' for d in domains])

    def test_priorities_protect_against_general_direct_routes(self):
        personal = ['DOMAIN,allowed.evil.example,DIRECT', 'PROCESS-NAME,browser.exe,DIRECT']
        rules = manager.profile_rules({'block_malware': True, 'quic_compatibility': True, 'direct_ru_services': True,
                                      'direct_games_udp_enabled': True, '_personal_rule_lines': personal})
        self.assertLess(rules.index(personal[0]), rules.index('RULE-SET,security-threats,REJECT'))
        self.assertLess(rules.index('RULE-SET,security-threats,REJECT'), rules.index(personal[1]))
        self.assertLess(rules.index(quic_rule('youtube.com')), rules.index(personal[1]))
        self.assertLess(rules.index(quic_rule('youtube.com')), rules.index('NETWORK,UDP,DIRECT'))
        self.assertLess(rules.index('RULE-SET,security-threats,REJECT'), rules.index('DOMAIN-SUFFIX,gosuslugi.ru,DIRECT'))

    def test_security_provider_selection_and_refresh(self):
        item = profile()
        item['routing'] = {'block_malware': True, 'block_malware_rules': 'RULE-SET,security-phishing,REJECT'}
        config = yaml.safe_load(manager.render_profile(item))
        self.assertEqual(set(config['rule-providers']), {'security-phishing'})
        definition = config['rule-providers']['security-phishing']
        self.assertEqual(definition['format'], 'text')
        self.assertEqual(definition['interval'], 21600)
        self.assertEqual(definition['proxy'], manager.MIHOMO_PROXY_GROUP)
        item['routing']['block_malware_rules'] = 'DOMAIN,evil.example,REJECT'
        self.assertNotIn('rule-providers', yaml.safe_load(manager.render_profile(item)))

    def test_services_reach_rule_exporters_and_legacy_routes_remain(self):
        item = profile()
        item['routing'] = {'direct_ru_services': True}
        for format in ('mihomo', 'singbox', 'xray'):
            config, _ = manager.render_client_profile(deepcopy(item), 'common', format)
            self.assertIn('gosuslugi.ru', config)
            self.assertIn('t2.ru', config)
        legacy = manager.profile_rules({'direct_ru_sites': True})
        self.assertIn('DOMAIN-SUFFIX,gosuslugi.ru,DIRECT', legacy)
        self.assertIn('GEOIP,RU,DIRECT,no-resolve', legacy)
        self.assertNotIn('GEOIP,RU,DIRECT,no-resolve', SERVICE_RULES)
        self.assertNotIn('DOMAIN-SUFFIX,ru,DIRECT', SERVICE_RULES)

    def test_export_capabilities_and_singbox_security_providers(self):
        self.assertIn('block_malware', device_capabilities('mihomo')['rules'])
        self.assertIn('quic_compatibility', device_capabilities('singbox')['rules'])
        item = profile()
        item['routing'] = {'block_malware': True}
        config = json.loads(manager.render_client_profile(item, 'common', 'singbox')[0])
        self.assertEqual({provider['tag'] for provider in config['route']['rule_set']}, set(SECURITY_PROVIDERS))
        for provider in config['route']['rule_set']:
            self.assertEqual(provider['url'], SECURITY_PROVIDERS[provider['tag']]['singbox_url'])
            self.assertEqual(provider['update_interval'], f"{SECURITY_PROVIDERS[provider['tag']]['interval']}s")
        self.assertTrue(config['experimental']['cache_file']['enabled'])
        for format in ('xray', 'uri'):
            self.assertNotIn('block_malware', device_capabilities(format)['rules'])
            self.assertFalse(compatible_routing({'block_malware': True}, format)['block_malware'])

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires real Mihomo')
    def test_all_new_presets_pass_real_mihomo_validation(self):
        item = profile()
        item['routing'] = {'quic_compatibility': True, 'block_malware': True, 'direct_ru_services': True,
                           'quic_compatibility_rules': '\n'.join(QUIC_AVAILABLE_RULES)}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.yaml'
            path.write_text(manager.render_profile(item), encoding='utf-8')
            result = subprocess.run([os.environ['PRIVACY_MIHOMO_BIN'], '-t', '-d', folder, '-f', str(path)],
                                    capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(os.getenv('PRIVACY_MIHOMO_BIN'), 'Requires real Mihomo')
    def test_real_quic_rule_blocks_selected_udp_only_and_preserves_tcp(self):
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                data, udp = self.request
                udp.sendto(data, self.client_address)

        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '6')
                self.end_headers()
                self.wfile.write(b'tcp-ok')

            def log_message(self, *_):
                pass

        destination = free_port(udp=True)
        servers = [socketserver.ThreadingUDPServer(('127.0.0.1', destination), Echo),
                   socketserver.ThreadingUDPServer(('127.0.0.1', 0), Echo),
                   http.server.ThreadingHTTPServer(('127.0.0.1', destination), Origin)]
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as folder, mihomo_readiness() as readiness:
                root = Path(folder)
                item = profile()
                item['routing'] = {'quic_compatibility': True, 'strategy': 'select', 'sniffer': False}
                config = yaml.safe_load(manager.render_profile(item))
                self.assertIn(quic_rule('youtube.com'), config['rules'])
                # High local ports keep the same three-way rule semantics testable
                # without root privileges or touching public UDP/443 services.
                config['rules'] = [rule.replace('(DST-PORT,443)', f'(DST-PORT,{destination})')
                                   for rule in config['rules'] if not rule.startswith('MATCH,')] + ['MATCH,DIRECT']
                config['mixed-port'] = port = free_port(udp=True)
                config['dns']['enable'] = False
                config['hosts'] = {domain: '127.0.0.1' for domain in ['youtube.com', 'www.youtube.com', 'unselected.audit.test', 'youtube.com.audit.test']}
                path = root / 'config.yaml'
                path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
                with open(root / 'core.log', 'w') as log:
                    process = subprocess.Popen([os.environ['PRIVACY_MIHOMO_BIN'], '-d', folder, '-f', str(path)], stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    try:
                        wait_port(port, process)
                        wait_http_proxy(port, process, readiness)
                        for domain, target_port, blocked in [
                                ('youtube.com', destination, True), ('www.youtube.com', destination, True),
                                ('unselected.audit.test', destination, False), ('youtube.com.audit.test', destination, False),
                                ('youtube.com', servers[1].server_address[1], False)]:
                            with self.subTest(domain=domain, port=target_port), socket.create_connection(('127.0.0.1', port), timeout=3) as control:
                                control.sendall(b'\x05\x01\x00')
                                self.assertEqual(control.recv(2), b'\x05\x00')
                                control.sendall(b'\x05\x03\x00\x01\x7f\x00\x00\x01\x00\x00')
                                reply = control.makefile('rb').read(10)
                                self.assertEqual(reply[:4], b'\x05\x00\x00\x01')
                                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                                    udp.settimeout(.5 if blocked else 3)
                                    packet = b'\x00\x00\x00\x03' + bytes([len(domain)]) + domain.encode() + target_port.to_bytes(2, 'big') + b'udp-ok'
                                    udp.sendto(packet, ('127.0.0.1', int.from_bytes(reply[-2:], 'big')))
                                    if blocked:
                                        with self.assertRaises(socket.timeout):
                                            udp.recvfrom(8192)
                                    else:
                                        self.assertTrue(udp.recvfrom(8192)[0].endswith(b'udp-ok'))
                        with socket.create_connection(('127.0.0.1', port), timeout=3) as tcp:
                            tcp.sendall(f'GET http://youtube.com:{destination}/ HTTP/1.1\r\nHost: youtube.com:{destination}\r\nConnection: close\r\n\r\n'.encode())
                            response = tcp.makefile('rb').read()
                            self.assertIn(b'tcp-ok', response)
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
