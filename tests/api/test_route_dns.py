"""DNS settings, first-match resource policy and client exporter contracts."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from tests.api.support import manager, ROOT
from tests.api.test_mihomo_rules_runtime import profile
from dns_routing import domain_dns_routes, mihomo_route_dns, singbox_route_dns


class RouteDnsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, 'SUBMODULE_ROOT', ROOT / 'protocol-images/mihomo/modules'))
        self.enterContext(patch.object(manager, 'public_endpoint', return_value='192.0.2.1'))
        self.enterContext(patch.object(manager, 'module_is_installed', return_value=True))
        self.settings = manager.dns_defaults()

    def test_schema_safe_defaults_and_validation(self):
        self.assertFalse(self.settings['route_dns'])
        for key in ('direct_nameserver', 'proxy_nameserver'):
            field = next(row for row in manager.dns_schema() if row['key'] == key)
            self.assertIn(self.settings[key], {option['value'] for option in field['options']})
        result = manager.validate_dns({'route_dns': True})
        self.assertTrue(result['route_dns'])
        for key, value in [('route_dns', 'true'), ('bootstrap_nameserver', 'dns.example.com'),
                           ('bootstrap_nameserver', 'https://dns.example.com/dns-query'),
                           ('local_nameserver', 'udp://127.0.0.1:0'),
                           ('local_nameserver', '1.1.1.1#GATE.312'),
                           ('local_nameserver', 'bad\nresolver'),
                           ('nameserver', '208.67.222.222'),
                           ('fallback', self.settings['nameserver'])]:
            with self.subTest(key=key, value=value), self.assertRaises(manager.HTTPException):
                manager.validate_dns({'route_dns': True, key: value})
        for bootstrap in ['1.1.1.1', 'https://1.1.1.1/dns-query', 'tcp://127.0.0.1:5300', '::1']:
            self.assertEqual(manager.validate_dns({'route_dns': True, 'bootstrap_nameserver': bootstrap})['bootstrap_nameserver'], bootstrap)

    def test_domain_policy_preserves_routing_order_and_exact_exceptions(self):
        routes = domain_dns_routes([
            'DOMAIN-SUFFIX,parent.test,GATE.312', 'DOMAIN,child.parent.test,DIRECT',
            'DOMAIN,exact.test,GATE.312', 'DOMAIN-SUFFIX,exact.test,DIRECT',
            'DOMAIN-SUFFIX,sub.other.test,DIRECT', 'DOMAIN-SUFFIX,other.test,GATE.312',
            'DOMAIN-SUFFIX,ads.ru,REJECT', 'DOMAIN-SUFFIX,ru,DIRECT'])
        self.assertNotIn(('DOMAIN', 'child.parent.test', 'DIRECT'), routes)
        self.settings['route_dns'] = True
        policy = mihomo_route_dns(self.settings, [','.join(row) for row in routes], 'VPN')['nameserver-policy']
        self.assertEqual(policy['exact.test'], ['https://cloudflare-dns.com/dns-query#VPN', 'https://dns.google/dns-query#VPN'])
        self.assertEqual(policy['.exact.test'], ['https://cloudflare-dns.com/dns-query#DIRECT'])
        self.assertTrue(policy['.ads.ru'][0].endswith('#VPN'))
        self.assertEqual(domain_dns_routes(['PROCESS-NAME,browser.exe,GATE.312', 'DOMAIN-SUFFIX,ru,DIRECT']), [])

    def test_match_and_secure_profile_do_not_override_explicit_dns_choices(self):
        self.settings.update(route_dns=True, nameserver='https://dns.quad9.net/dns-query')
        rules = ['DOMAIN,exception.test,GATE.312', 'MATCH,DIRECT', 'DOMAIN,unreachable.test,DIRECT']
        config = mihomo_route_dns(self.settings, rules, 'VPN')
        self.assertEqual(config['nameserver'], [self.settings['direct_nameserver'] + '#DIRECT'])
        self.assertTrue(config['nameserver-policy']['exception.test'][0].endswith('#VPN'))
        self.assertNotIn('unreachable.test', config['nameserver-policy'])
        with patch.object(manager, 'dns_settings', return_value=self.settings):
            self.assertEqual(manager.client_dns_settings({'dns_secure': True})['nameserver'], self.settings['nameserver'])
        sb = singbox_route_dns(self.settings, rules, 'VPN')
        self.assertEqual(sb['final'], 'direct-dns')
        self.assertEqual(sb['rules'][0]['query_type'], ['AAAA'])

    def test_legacy_mode_is_preserved_and_global_mode_does_not_bypass(self):
        item = profile()
        item['routing'] = {'direct_ru_sites': True}
        with patch.object(manager, 'dns_settings', return_value=self.settings):
            legacy = yaml.safe_load(manager.render_profile(deepcopy(item)))['dns']
        self.assertEqual(legacy['nameserver'], [self.settings['nameserver']])
        self.assertIn('fallback', legacy)
        self.assertNotIn('direct-nameserver', legacy)
        self.settings['route_dns'] = True
        item['routing']['mode'] = 'global'
        with patch.object(manager, 'dns_settings', return_value=self.settings):
            current = yaml.safe_load(manager.render_profile(deepcopy(item)))['dns']
        self.assertNotIn('.ru', current['nameserver-policy'])
        self.assertEqual(current['nameserver-policy']['cloudflare-dns.com'], ['1.1.1.1#DIRECT'])
        self.assertTrue(all(value.endswith('#' + manager.MIHOMO_PROXY_GROUP) for value in current['nameserver']))
        self.assertNotIn('fallback', current)

    def test_profile_and_device_rules_generate_different_dns_policy(self):
        self.settings['route_dns'] = True
        item = profile()
        item['routing'] = {'direct_ru_sites': True, 'dns_secure': True}
        original = deepcopy(item)
        with patch.object(manager, 'dns_settings', return_value=self.settings), patch.object(
                manager, 'personal_rule_lines_for_device', return_value=['DOMAIN-SUFFIX,private.ru,GATE.312']):
            first = yaml.safe_load(manager.render_profile(deepcopy(item)))['dns']
            self.assertTrue(first['nameserver-policy']['.ru'][0].endswith('#DIRECT'))
            self.assertTrue(first['nameserver-policy']['.private.ru'][0].endswith('#' + manager.MIHOMO_PROXY_GROUP))
            item['devices'][0]['routing'] = {'direct_ru_sites': False}
            second = yaml.safe_load(manager.render_profile(deepcopy(item)))['dns']
            self.assertNotIn('.ru', second['nameserver-policy'])
        self.assertEqual(item['routing'], original['routing'])
        self.assertEqual(first['default-nameserver'], ['1.1.1.1'])
        self.assertEqual(first['proxy-server-nameserver'], [self.settings['proxy_nameserver'] + '#DIRECT'])

    def test_all_client_configs_compile_and_keep_resolver_transport_separate(self):
        self.settings['route_dns'] = True
        item = profile()
        item['routing'] = {'direct_ru_services': True}
        binaries = {
            'mihomo': os.getenv('PRIVACY_MIHOMO_BIN'),
            'singbox': os.getenv('PRIVACY_SINGBOX_BIN'),
            'xray': os.getenv('PRIVACY_XRAY_BIN'),
        }
        for format in binaries:
            with self.subTest(format=format), patch.object(manager, 'dns_settings', return_value=self.settings):
                text, extension = manager.render_client_profile(deepcopy(item), 'common', format)
                if format == 'singbox':
                    config = json.loads(text)
                    by_tag = {row['tag']: row for row in config['dns']['servers']}
                    self.assertEqual(by_tag['direct-dns']['detour'], 'direct')
                    self.assertEqual(by_tag['remote']['detour'], config['route']['final'])
                    self.assertEqual(by_tag['fallback']['detour'], config['route']['final'])
                    self.assertEqual(by_tag['bootstrap']['detour'], 'direct')
                    self.assertEqual(config['route']['default_domain_resolver'], 'remote')
                    self.assertTrue(any(row.get('server') == 'direct-dns' for row in config['dns']['rules']))
                if format == 'xray':
                    config = json.loads(text)[0]
                    self.assertTrue(config['dns']['disableFallbackIfMatch'])
                    self.assertEqual(config['routing']['rules'][0]['outboundTag'], 'direct')
                    text = json.dumps(config)
                binary = binaries[format]
                if binary:
                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder) / ('config.' + extension)
                        path.write_text(text, encoding='utf-8')
                        args = ['-t', '-d', folder, '-f', str(path)] if format == 'mihomo' else (
                            ['check', '-c', str(path)] if format == 'singbox' else ['run', '-test', '-config', str(path)])
                        checked = subprocess.run([binary, *args], capture_output=True, text=True, timeout=30)
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == '__main__':
    unittest.main()
