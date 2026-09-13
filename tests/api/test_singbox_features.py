"""Capabilities must correspond to configs accepted by each actual client core."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.api.support import manager, ROOT
from client_capabilities import device_capabilities, connection_supported, compatible_routing
from client_singbox import build_singbox_config, singbox_rules, UnsupportedClientConfig


class SingboxFeaturesTests(unittest.TestCase):
    def connections(self):
        return [
            {"component": "transport-wg", "credential": {"port": 51820, "ip": "10.0.0.2/32", "mtu": 1420, "private_key": "A" * 43 + "=", "server_public_key": "A" * 43 + "="}},
            {"component": "transport-reality", "settings": {"route_mode": "cdn"}, "credential": {"uuid": "00000000-0000-4000-8000-000000000001", "cdn_enabled": True, "cdn_domain": "example.com", "cdn_path": "/test", "cdn_transport": "websocket"}},
        ]

    def config(self, connections=None, routing=None, rules=None, client=None):
        return build_singbox_config(connections or self.connections(), routing or {"strategy": "select", "tunnel_ech": True},
            {"nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}, rules or [], "192.0.2.1", lambda: {}, True,
            device_capabilities("singbox", "windows", client))

    def check(self, config, lx=False):
        folder = ".runtime/singbox-lx-bin" if lx else ".runtime/singbox-check"
        candidates = list((ROOT / folder).glob("*/sing-box.exe"))
        variable = "PRIVACY_SINGBOX_LX_BIN" if lx else "PRIVACY_SINGBOX_BIN"
        binary = Path(os.environ.get(variable, candidates[0] if candidates else "missing"))
        if not binary.is_file():
            self.skipTest(f"Set {variable} to check the generated configuration")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            checked = subprocess.run([str(binary.resolve()), "check", "-c", str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_standard_wireguard_ech_and_selector(self):
        result = self.config()
        self.assertEqual(result["endpoints"][0]["peers"][0]["allowed_ips"], ["0.0.0.0/0", "::/0"])
        self.assertEqual(result["outbounds"][-1]["type"], "selector")
        self.assertIn(result["endpoints"][0]["tag"], result["outbounds"][-1]["outbounds"])
        self.assertTrue(result["outbounds"][0]["tls"]["ech"]["enabled"])
        self.assertEqual(result["outbounds"][0]["domain_resolver"], "remote")
        self.check(result)

    def test_all_profile_presets_compile_to_current_rules(self):
        routing = {key: True for key in manager.RULES}
        rules = manager.profile_rules(routing)
        rules.append("PROCESS-NAME-WILDCARD,game*.exe,DIRECT")
        result = self.config(rules=rules)
        self.assertEqual({item["tag"] for item in result["route"]["rule_set"]}, {"geoip-ru", "geosite-category-ads-all"})
        self.assertTrue(any("process_name" in rule for rule in result["route"]["rules"]))
        self.assertTrue(any("process_path_regex" in rule for rule in result["route"]["rules"]))
        self.assertTrue(any(rule.get("port") == [53] for rule in result["route"]["rules"]))
        self.check(result)

    def test_wildcard_matching_is_anchored_and_geo_sets_deduplicated(self):
        rules, sets = singbox_rules(["PROCESS-NAME-WILDCARD,game*.exe,DIRECT", "GEOIP,RU,DIRECT,no-resolve", "GEOIP,ru,REJECT"])
        self.assertEqual(rules[0]["process_path_regex"], [r"(?:^|[/\\])game.*\.exe$"])
        self.assertEqual(len(sets), 1)
        self.assertIn("geoip-ru.srs", sets[0]["url"])
        with self.assertRaises(UnsupportedClientConfig):
            singbox_rules(["GEOSITE,../../secret,DIRECT"])

    def test_launcher_extensions_are_not_exposed_to_standard_clients(self):
        for client in (None, "Karing", "Hiddify", "sing-box MT"):
            self.assertNotIn("transport-awg", device_capabilities("singbox", client=client)["components"])
            self.assertNotIn("tunnel_privacy", device_capabilities("singbox", client=client)["features"])
        routing = {"tunnel_privacy": True, "tunnel_ech": True, "strategy": "select"}
        self.assertFalse(compatible_routing(routing, "singbox")["tunnel_privacy"])
        self.assertTrue(compatible_routing(routing, "singbox", client="Sing-Box Launcher")["tunnel_privacy"])
        self.assertIn("direct_games_udp_enabled", device_capabilities("singbox", "ios")["rules"])
        self.assertNotIn("direct_games_enabled", device_capabilities("singbox", "ios")["rules"])

    def test_launcher_awg_xhttp_and_encryption_pass_its_core(self):
        connections = self.connections()
        connections[0]["component"] = "transport-awg"
        awg = {"jc": 4, "jmin": 40, "jmax": 70, "s1": 1, "s2": 2, "h1": 3, "h2": 4, "h3": 5, "h4": 6}
        connections[0]["credential"]["amnezia"] = awg
        connections[1]["credential"].update(cdn_transport="xhttp", cdn_xhttp_mode="packet-up", encryption="mlkem768x25519plus.native.0rtt." + "A" * 43)
        self.assertFalse(connection_supported(connections[0], "singbox"))
        self.assertTrue(connection_supported(connections[0], "singbox", client="Sing-Box Launcher"))
        result = self.config(connections=connections, client="Sing-Box Launcher")
        self.assertEqual({key: result["endpoints"][0][key] for key in awg}, awg)
        self.assertEqual(result["outbounds"][0]["transport"]["type"], "xhttp")
        self.assertEqual(result["outbounds"][0]["transport"]["mode"], "packet-up")
        self.assertEqual(result["outbounds"][0]["encryption"], connections[1]["credential"]["encryption"])
        self.check(result, lx=True)
