"""Export mapping and real route decisions, without public destinations."""
from copy import deepcopy
import http.server
import json
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


def profile():
    return {"id": "audit", "name": "audit", "common_device_id": "common",
            "devices": [{"id": "common", "routing": {}}],
            "connections": [{"id": "c", "device_id": "common", "component": "transport-shadowsocks",
                             "credential": {"port": 18888, "method": "aes-256-gcm", "password": "test"}}]}


class RuleExportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="127.0.0.1"))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))

    def test_each_mihomo_switch_changes_its_exported_setting(self):
        fields = {"sniffer": ("sniffer", "enable"), "tcp_concurrent": ("tcp-concurrent",),
                  "tun_enabled": ("tun", "enable"), "tun_force": ("tun", "enable"),
                  "tun_strict_route": ("tun", "strict-route"), "dns_ipv6": ("dns", "ipv6"),
                  "dns_prefer_h3": ("dns", "prefer-h3")}
        for key, path in fields.items():
            for enabled in (True, False):
                with self.subTest(key=key, enabled=enabled):
                    item = profile()
                    item["devices"][0]["routing"] = {key: enabled}
                    result = yaml.safe_load(manager.render_profile(item))
                    for part in path:
                        result = result[part]
                    self.assertEqual(result, enabled)
        for enabled in (True, False):
            item = profile()
            item["devices"][0]["routing"] = {"dns_hijack_force": enabled, "dns_fake_ip": enabled}
            result = yaml.safe_load(manager.render_profile(item))
            self.assertEqual("dns-hijack" in result["tun"], enabled)
            self.assertEqual(result["dns"]["enhanced-mode"], "fake-ip" if enabled else "redir-host")

    def test_dns_policy_and_secure_override(self):
        settings = {"enhanced_mode": "redir-host", "nameserver": "1.1.1.1", "fallback": "8.8.8.8",
                    "ipv6": True, "prefer_h3": True, "cache_algorithm": "arc", "fake_ip_filter": "*.test"}
        with patch.object(manager, "dns_settings", return_value=settings):
            item = profile()
            result = yaml.safe_load(manager.render_profile(item))["dns"]
            self.assertEqual(result["nameserver"], ["1.1.1.1"])
            self.assertEqual(result["fallback"], ["8.8.8.8"])
            self.assertEqual(result["cache-algorithm"], "arc")
            self.assertTrue(result["ipv6"])
            self.assertTrue(result["prefer-h3"])
            item["devices"][0]["routing"] = {"dns_secure": True, "dns_fake_ip": True}
            result = yaml.safe_load(manager.render_profile(item))["dns"]
            self.assertTrue(all(value.startswith("https://") for value in result["nameserver"] + result["fallback"]))
            self.assertEqual(result["fake-ip-filter"], ["*.test"])

    def test_presets_are_conditional_and_custom_lists_replace_defaults(self):
        for key, rules in manager.DIRECT_RULE_PRESETS.items():
            with self.subTest(key=key):
                self.assertTrue(rules)
                self.assertTrue(set(rules).issubset(manager.profile_rules({key: True})))
                self.assertEqual(manager.profile_rules({key: False}), [])
                if key != "windows_geolocation":
                    self.assertEqual(manager.profile_rules({key: True, key + "_rules": "DOMAIN,audit.test,DIRECT"}), ["DOMAIN,audit.test,DIRECT"])
        self.assertIn("PROCESS-NAME,qbittorrent.exe,DIRECT", manager.profile_rules({"direct_p2p_enabled": True, "direct_p2p_clients": "qbittorrent"}))
        self.assertIn("NETWORK,UDP,DIRECT", manager.profile_rules({"direct_games_udp_enabled": True}))
        self.assertTrue(any(rule.startswith("PROCESS-NAME") for rule in manager.profile_rules({"direct_games_enabled": True})))

    def test_strategy_parameters_reach_group(self):
        for strategy in ("select", "url-test", "fallback"):
            item = profile()
            item["devices"][0]["routing"] = {"strategy": strategy, "interval": 77, "test_url": "http://audit.test/check", "health_timeout": 1700, "max_failed_times": 4, "tolerance": 99}
            shared = {**manager.routing_settings(), "interval": 77, "test_url": "http://audit.test/check", "health_timeout": 1700, "max_failed_times": 4, "tolerance": 99}
            item["devices"][0]["routing"].update(interval=1, test_url="http://stale.test", tolerance=1, health_timeout=1, max_failed_times=1)
            item["routing"] = {"interval": 2, "test_url": "http://stale-profile.test"}
            with patch.object(manager, "routing_settings", return_value=shared):
                group = yaml.safe_load(manager.render_profile(item))["proxy-groups"][0]
            self.assertEqual(group["type"], strategy)
            if strategy != "select":
                self.assertEqual((group["interval"], group["timeout"], group["max-failed-times"], group["url"]), (77, 1700, 4, "http://audit.test/check"))
            if strategy == "url-test":
                self.assertEqual(group["tolerance"], 99)

    def test_mobile_ad_rules_are_inherited_and_exported_to_both_rule_clients(self):
        item = profile()
        item['routing'] = {'block_ads': True}
        source = deepcopy(item)
        yaml_rules = yaml.safe_load(manager.render_profile(deepcopy(item)))['rules']
        config = json.loads(manager.render_client_profile(deepcopy(item), 'common', 'singbox')[0])
        blocked_suffixes = {domain for rule in config['route']['rules'] if rule.get('outbound') == 'block'
                            for domain in rule.get('domain_suffix', [])}
        for domain in ['bidmachine.io', 'adsmoloco.com', 'pangle.io', 'tapjoyads.com',
                       'appodealx.com', 'adservice.google.com', 'iadsdk.apple.com']:
            with self.subTest(domain=domain):
                self.assertIn(f'DOMAIN-SUFFIX,{domain},REJECT', yaml_rules)
                self.assertIn(domain, blocked_suffixes)
        # Device switches still override inherited profile settings.
        item['devices'][0]['routing']['block_ads'] = False
        self.assertNotIn('DOMAIN-SUFFIX,adsmoloco.com,REJECT', yaml.safe_load(manager.render_profile(deepcopy(item)))['rules'])
        item['devices'][0]['routing'] = {}
        self.assertEqual(item, source)

    def test_mihomo_ad_database_updates_daily_and_respects_custom_lists(self):
        item = profile()
        item['routing'] = {'block_ads': True}
        with patch.object(manager, 'personal_rule_lines_for_device', return_value=['DOMAIN,allowed.ads.audit.test,DIRECT']):
            config = yaml.safe_load(manager.render_profile(deepcopy(item)))
        provider = config['rule-providers'][manager.AD_RULE_PROVIDER]
        self.assertEqual(provider['url'], manager.AD_RULE_PROVIDER_URL)
        self.assertEqual((provider['behavior'], provider['format'], provider['interval']), ('domain', 'yaml', 86400))
        self.assertEqual(provider['proxy'], manager.MIHOMO_PROXY_GROUP)
        self.assertLess(config['rules'].index('DOMAIN,allowed.ads.audit.test,DIRECT'),
                        config['rules'].index(manager.mihomo_ad_base_rule(manager.AD_RULE_PROVIDER)))
        for routing in ({'block_ads': False}, {'block_ads': True, 'block_ads_rules': 'DOMAIN,ads.audit.test,REJECT'}):
            item['devices'][0]['routing'] = routing
            self.assertNotIn('rule-providers', yaml.safe_load(manager.render_profile(deepcopy(item))))

    def test_extended_delivery_domains_export_to_both_clients_without_shared_video_hosts(self):
        item = profile()
        item['routing'] = {'block_ads': True}
        rules = yaml.safe_load(manager.render_profile(deepcopy(item)))['rules']
        config = json.loads(manager.render_client_profile(deepcopy(item), 'common', 'singbox')[0])
        blocked = {domain for rule in config['route']['rules'] if rule.get('outbound') == 'block'
                   for domain in rule.get('domain_suffix', [])}
        for domain in ['adservice.google.ru', 'googleadsserving.cn', 'mgid.com', 'triplelift.com',
                       'bidswitch.net', 'adcash.com', 'ad.xiaomi.com', 'ssp.rutube.ru']:
            self.assertIn(f'DOMAIN-SUFFIX,{domain},REJECT', rules)
            self.assertIn(domain, blocked)
        for domain in ['youtube.com', 'googlevideo.com', 'ytimg.com', 'googleapis.com', 'gvt1.com', 'rutube.ru']:
            self.assertNotIn(f'DOMAIN-SUFFIX,{domain},REJECT', rules)
            self.assertNotIn(domain, blocked)

    def test_sniffer_covers_quic_and_ip_connections_without_overriding_destinations(self):
        item = profile()
        for enabled in (True, False):
            item['devices'][0]['routing'] = {'sniffer': enabled}
            sniffer = yaml.safe_load(manager.render_profile(deepcopy(item)))['sniffer']
            self.assertEqual(sniffer['enable'], enabled)
            self.assertEqual(sniffer['sniff']['QUIC']['ports'], [443, 8443])
            self.assertTrue(sniffer['force-dns-mapping'])
            self.assertTrue(sniffer['parse-pure-ip'])
            self.assertFalse(sniffer['override-destination'])

    def test_export_uses_applied_quic_settings_without_mutating_saved_credentials(self):
        connections = [{"component": "transport-hysteria2", "credential": {"port": 1234, "password": "client", "obfs": False}}]
        with patch.object(manager, "load_json", return_value={"inbounds": [{"type": "hysteria2", "listen_port": 5678, "up_mbps": 123, "down_mbps": 456, "obfs": {"type": "salamander", "password": "applied-obfs"}}]}):
            result = manager.effective_client_connections(connections)
        self.assertEqual(result[0]["credential"]["port"], 5678)
        self.assertEqual(result[0]["credential"]["up_mbps"], 123)
        self.assertEqual(result[0]["credential"]["obfs_password"], "applied-obfs")
        self.assertEqual(connections[0]["credential"], {"port": 1234, "password": "client", "obfs": False})

    def test_browser_routes_do_not_bypass_protection_but_domain_exceptions_still_win(self):
        for process in ('PROCESS-NAME,chrome.exe,GATE.312', 'PROCESS-NAME-WILDCARD,*firefox*,DIRECT'):
            with self.subTest(process=process):
                personal = [process, 'DOMAIN,allowed.trafficjunky.net,DIRECT']
                routing = {'block_ads': True, 'block_privacy': True, 'direct_ru_sites': True,
                           'direct_games_udp_enabled': True, '_personal_rule_lines': personal}
                rules = manager.profile_rules(routing)
                self.assertLess(rules.index(personal[1]), rules.index('DOMAIN-SUFFIX,trafficjunky.net,REJECT'))
                self.assertLess(rules.index('DOMAIN-SUFFIX,trafficjunky.net,REJECT'), rules.index(process))
                self.assertLess(rules.index('DOMAIN-SUFFIX,google-analytics.com,REJECT'), rules.index(process))
                self.assertLess(rules.index(process), rules.index('NETWORK,UDP,DIRECT'))
                routing.update(block_ads=False, block_privacy=False)
                disabled = manager.profile_rules(routing)
                self.assertNotIn('DOMAIN-SUFFIX,trafficjunky.net,REJECT', disabled)
                self.assertNotIn('DOMAIN-SUFFIX,google-analytics.com,REJECT', disabled)
                self.assertEqual(disabled[:2], [personal[1], process])

    def test_video_ad_networks_reach_mihomo_and_singbox_and_switch_off(self):
        item = profile()
        item['routing'] = {'block_ads': True}
        domains = ['ads.youtube.com', 'trafficjunky.net', 'trafficjunky.com', 'exoclick.com',
                   'exosrv.com', 'juicyads.com', 'tsyndicate.com', 'trafficstars.com',
                   'trafficfactory.biz', 'realsrv.com', 'adsco.re', 'vidoomy.com', 'popads.net']
        for enabled in (True, False):
            item['devices'][0]['routing'] = {'block_ads': enabled}
            rules = yaml.safe_load(manager.render_profile(deepcopy(item)))['rules']
            config = json.loads(manager.render_client_profile(deepcopy(item), 'common', 'singbox')[0])
            blocked = {domain for rule in config['route']['rules'] if rule.get('outbound') == 'block'
                       for domain in rule.get('domain_suffix', [])}
            for domain in domains:
                with self.subTest(enabled=enabled, domain=domain):
                    self.assertEqual(f'DOMAIN-SUFFIX,{domain},REJECT' in rules, enabled)
                    self.assertEqual(domain in blocked, enabled)
            for domain in ['youtube.com', 'googlevideo.com', 'pornhub.com', 'phncdn.com',
                           'xvideos.com', 'xhcdn.com', 'dailymotion.com', 'twitch.tv']:
                self.assertNotIn(f'DOMAIN-SUFFIX,{domain},REJECT', rules)
                self.assertNotIn(domain, blocked)

    def test_unsupported_custom_fragment_ranges_are_not_silently_saved(self):
        with self.assertRaises(manager.HTTPException):
            manager.validate_routing({"tunnel_fragment_size": "300-500"}, current={})
        with self.assertRaises(manager.HTTPException):
            manager.validate_routing({"tunnel_fragment_interval": "50-100"}, current={})

    @unittest.skipUnless(os.getenv("PRIVACY_MIHOMO_BIN"), "Requires real Mihomo")
    def test_real_core_routes_direct_blocks_and_respects_personal_override(self):
        class Origin(http.server.BaseHTTPRequestHandler):
            def do_CONNECT(self):
                self.send_response(200, "Connection established")
                self.end_headers()
                # Consume the tunneled HTTP request and return the relay marker.
                self.rfile.readline()
                while self.rfile.readline().strip():
                    pass
                body = self.server.marker
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
                self.wfile.flush()
            def do_GET(self):
                body = self.server.marker
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        servers = []
        for marker in (b"via-direct", b"via-tunnel"):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
            server.marker = marker
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        try:
            with tempfile.TemporaryDirectory() as folder, mihomo_readiness() as readiness_port:
                root = Path(folder)
                item = profile()
                # Exercise the expanded provider and explicit fallbacks offline.
                ad_rules = [rule for rule in manager.configured_preset_rules({}, 'block_ads')
                            if not rule.startswith('GEOSITE,') and rule not in {
                                'DOMAIN-SUFFIX,adform.com,REJECT',
                                'DOMAIN-SUFFIX,ero-advertising.com,REJECT'}]
                item["devices"][0]["routing"] = {"strategy": "select", "direct_ru_banks": True,
                    "block_malware": True, "quic_compatibility": True, "direct_ru_services": True,
                    "block_ads": True, "block_ads_rules": '\n'.join(['GEOSITE,category-ads-all,REJECT', *ad_rules, 'DOMAIN-SUFFIX,ads.audit.test,REJECT'])}
                with patch.object(manager, "personal_rule_lines_for_device", return_value=["DOMAIN,allowed.ads.audit.test,DIRECT"]):
                    config = yaml.safe_load(manager.render_profile(item))
                provider_file = root / 'ads.yaml'
                provider_file.write_text('payload:\n  - "+.fresh-provider.audit.test"\n  - "+.ads.audit.test"\n'
                                         '  - "+.adform.com"\n  - "+.ero-advertising.com"\n', encoding='utf-8')
                config['rule-providers'][manager.AD_RULE_PROVIDER] = {
                    'type': 'file', 'behavior': 'domain', 'format': 'yaml', 'path': str(provider_file)}
                for name, domain in [('security-threats', 'malware.audit.test'), ('security-phishing', 'phishing.audit.test')]:
                    feed = root / f'{name}.txt'
                    feed.write_text(f'# Security fixture\n{domain}\n', encoding='utf-8')
                    config['rule-providers'][name] = {'type': 'file', 'behavior': 'domain', 'format': 'text', 'path': str(feed)}
                cases = [('sberbank.ru', b'via-direct'), ('unmatched.audit.test', b'via-tunnel'),
                         # Disabled named entries remain disabled even when
                         # the aggregate provider still contains their domains.
                         ('adform.com', b'via-tunnel'), ('ads.ero-advertising.com', b'via-tunnel'),
                         ('malware.audit.test', b'502'), ('phishing.audit.test', b'502'),
                         ('gosuslugi.ru', b'via-direct'), ('t2.ru', b'via-direct'), ('rt.ru', b'via-direct'),
                         ('fresh-provider.audit.test', b'502'), ('delivery.mgid.com', b'502'),
                         ('adservice.google.ru', b'502'), ('ssp.rutube.ru', b'502'),
                         ('ads.audit.test', b'502'), ('allowed.ads.audit.test', b'via-direct'),
                         ('delivery.adsmoloco.com', b'502'), ('api.pangle.io', b'502'),
                         ('iadsdk.apple.com', b'502'), ('adservice.google.com', b'502'),
                         ('ads.youtube.com', b'502'), ('ads.trafficjunky.net', b'502'),
                         ('static.trafficjunky.com', b'502'), ('syndication.exoclick.com', b'502'),
                         ('ads.trafficstars.com', b'502'), ('delivery.vidoomy.com', b'502'),
                         ('pornhub.com', b'via-tunnel'), ('video.phncdn.com', b'via-tunnel'),
                         ('youtube.com', b'via-tunnel'), ('ads.trafficjunky.net.audit.test', b'via-tunnel'),
                         ('www.apple.com', b'via-tunnel'), ('video.googlevideo.com', b'via-tunnel'),
                         ('adservice.google.com.audit.test', b'via-tunnel')]
                config["hosts"] = {name: "127.0.0.1" for name, _ in cases}
                config["dns"]["enable"] = False
                config["log-level"] = "debug"
                config["rules"].insert(0, f"DST-PORT,{readiness_port},DIRECT")
                config["mixed-port"] = port = free_port(udp=True)
                # Isolate routing from the transport layer already covered by
                # the encrypted and QUIC traffic tests.
                proxy_name = config["proxies"][0]["name"]
                config["proxies"] = [{"name": proxy_name, "type": "http", "server": "127.0.0.1", "port": servers[1].server_port}]
                path = root / "config.yaml"
                path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
                with open(root / "core.log", "w") as log:
                    process = subprocess.Popen([os.environ["PRIVACY_MIHOMO_BIN"], "-d", str(root), "-f", str(path)], stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    try:
                        wait_port(port, process)
                        wait_http_proxy(port, process, readiness_port)
                        for domain, expected in cases:
                            with self.subTest(domain=domain), socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                                connection.settimeout(5)
                                connection.sendall(f"GET http://{domain}:{servers[0].server_port}/ HTTP/1.1\r\nHost: {domain}:{servers[0].server_port}\r\nConnection: close\r\n\r\n".encode())
                                response = b""
                                while chunk := connection.recv(8192):
                                    response += chunk
                                self.assertIn(expected, response)
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
                        log.flush()
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
