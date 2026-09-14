"""Device-scoped Mihomo rule library coverage."""
import unittest
from unittest.mock import patch

from tests.api.support import manager


class PersonalRuleTests(unittest.TestCase):
    def test_domain_and_process_rules_are_compiled_for_mihomo(self):
        domain = manager.validate_personal_rule(manager.PersonalRuleInput(
            code="SITES",
            title="Рабочие сайты",
            entries=["*.Example.com", "api.example.com"],
            target="GATE.312",
        ))
        process = manager.validate_personal_rule(manager.PersonalRuleInput(
            code="APPS",
            title="Игровые приложения",
            kind="process",
            entries=["game.exe", "com.example.game*"],
            target="GATE.312",
        ))
        self.assertEqual(domain["entries"], ["example.com", "api.example.com"])
        self.assertEqual(manager.personal_rule_lines(domain), ["DOMAIN-SUFFIX,example.com,GATE.312", "DOMAIN-SUFFIX,api.example.com,GATE.312"])
        self.assertEqual(manager.personal_rule_lines(process), [
            "PROCESS-NAME,game.exe,GATE.312",
            "PROCESS-NAME-WILDCARD,com.example.game*,GATE.312",
        ])

    def test_only_rules_selected_on_device_are_rendered(self):
        selected = {
            "id": "personal-aaaaaaaaaaaa",
            "code": "APP",
            "title": "App",
            "description": "",
            "kind": "process",
            "entries": ["game.exe"],
            "target": "GATE.312",
        }
        other = {**selected, "id": "personal-bbbbbbbbbbbb", "entries": ["other.exe"]}
        profile = {"devices": [{"id": "desktop", "personal_rule_ids": [selected["id"]]}]}
        with patch.object(manager, "personal_rules", return_value=[selected, other]):
            self.assertEqual(manager.personal_rule_lines_for_device(profile, "desktop"), ["PROCESS-NAME,game.exe,GATE.312"])

    def test_process_rule_enables_process_matching_in_mihomo_config(self):
        rule = {
            "id": "personal-aaaaaaaaaaaa",
            "code": "APP",
            "title": "App",
            "description": "",
            "kind": "process",
            "entries": ["game.exe"],
            "target": "GATE.312",
        }
        profile = {
            "id": "profile",
            "common_device_id": "common",
            "devices": [{"id": "common", "name": "Common", "routing": {}, "personal_rule_ids": [rule["id"]]}],
            "connections": [{"id": "ss", "device_id": "common", "component": "transport-shadowsocks", "settings": {}, "credential": {"port": 1, "method": "aes-128-gcm", "password": "secret"}}],
        }
        with patch.object(manager, "personal_rules", return_value=[rule]), patch.object(manager, "routing_settings", return_value={"mode": "rule", "strategy": "select"}), patch.object(manager, "dns_settings", return_value={"ipv6": False, "enhanced_mode": "redir-host", "prefer_h3": False, "cache_algorithm": "lru", "nameserver": "https://dns.example", "fallback": "https://fallback.example"}):
            config = manager.render_profile(profile, "common")
        self.assertIn('find-process-mode: strict', config)
        self.assertIn('PROCESS-NAME,game.exe,GATE.312', config)


if __name__ == "__main__":
    unittest.main()
