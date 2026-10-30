"""Policy fixtures prove selection mechanics, not application compatibility."""
from copy import deepcopy
import json
import yaml
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager
from tests.api import test_subscription_formats as subscription_tests

policy = manager.client_compatibility
profile_rules = manager.profile_rules


def check(scenario, **changes):
    return {"id": "fixture-check", "client_name": "Clash Mi", "client_version": "3.0",
            "core_name": "Mihomo", "core_version": "25.1", "os": "windows", "os_version": "11",
            "format": "mihomo", "app_url": "https://example.invalid/app",
            "scenario": deepcopy(scenario), "evidence": {"kind": "application", "result": "passed",
            "reference": "test fixture, not a real application check", "checked_at": "2026-10-12"}, **changes}


class VerificationMatrixTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.file = self.root / "matrix.json"
        self.scenario = {"connections": [{"component": "transport-shadowsocks"}]}

    def write(self, records):
        self.file.write_text(json.dumps({"version": 1, "checks": records}), encoding="utf-8")
        return policy.verified_records(self.file)

    def test_catalog_has_no_invented_application_verifications(self):
        self.assertEqual(policy.verified_records(), [])

    def test_invalid_or_unavailable_matrix_fails_closed(self):
        self.assertEqual(policy.verified_records(self.file), [])
        for text in ("invalid", "[]", '{"version":2,"checks":[]}'):
            self.file.write_text(text, encoding="utf-8")
            self.assertEqual(policy.verified_records(self.file), [])
        valid = check(self.scenario)
        invalid = [None, {}, {**valid, "format": []}, {**valid, "app_url": []}, {**valid, "app_url": "javascript:alert(1)"},
                   {**valid, "app_url": "https://user:password@example.invalid"},
                   {**valid, "evidence": {"kind": "parser", "result": "passed"}},
                   {**valid, "core_version": "latest"}, {**valid, "os_version": "*"},
                   {**valid, "os": "unknown"}, {**valid, "scenario": {"connections": []}}]
        self.assertEqual(self.write(invalid + [valid]), [valid])

    def test_only_exact_yaml_scenarios_are_recommended(self):
        record = check(self.scenario)
        self.assertEqual(policy.matching_records(self.scenario, [record]), [record])
        self.assertEqual(policy.matching_records({"connections": []}, [record]), [])
        self.assertEqual(self.write([check(self.scenario, format="xray")]), [])

    def test_versions_are_observed_without_guessing_a_bundled_core(self):
        self.assertEqual(manager.observed_client_versions("ClashMi/3.0 (Mihomo/25.1)", "Clash Mi"),
                         {"client_version": "3.0", "core_name": "Mihomo", "core_version": "25.1"})
        self.assertEqual(manager.observed_client_versions("ClashMi/3.0 like sing-box", "Clash Mi")["core_version"], "")
        self.assertEqual(manager.observed_client_versions("CFNetwork/3860 Darwin/25", "Clash Mi")["client_version"], "")
        self.assertEqual(manager.observed_client_versions("RocketVPN/582994", "Clash Mi")["client_version"], "")


class VerifiedSubscriptionTests(unittest.TestCase):
    def setUp(self):
        subscription_tests.SubscriptionFormatTests.setUp(self)
        # Keep a minimal routing fixture for application verification.
        values = {**manager.routing_settings(), "strategy": ""}
        self.enterContext(patch.object(manager, "routing_settings", return_value=values))
        self.enterContext(patch.object(manager, "profile_rules", side_effect=profile_rules))

    fetch = subscription_tests.SubscriptionFormatTests.fetch

    def matrix(self, **changes):
        record = check(manager.client_verification_scenario(self.store[0], "common"), **{"format":"mihomo", **changes})
        self.enterContext(patch.object(policy, "verified_records", return_value=[record]))
        return record

    @staticmethod
    def headers():
        return [(b"x-client-version", b"3.0"), (b"x-core-name", b"Mihomo"), (b"x-core-version", b"25.1"),
                (b"x-device-os", b"Windows"), (b"x-ver-os", b"11")]

    def test_verified_client_gets_format_without_hwid_or_profile_mutation(self):
        self.matrix()
        before = manager.normalize_profile(deepcopy(self.store[0]))["devices"]
        response = self.fetch("ClashMi/3.0", hwid=None, extra_headers=self.headers())
        self.assertIn(".yaml", response.headers["content-disposition"])
        self.assertEqual(yaml.safe_load(response.body)["proxies"][0]["type"], "ss")
        self.assertEqual(self.store[0]["devices"], before)
        self.provision.assert_not_called()

    def test_explicit_versions_in_user_agent_can_match_verification(self):
        self.matrix()
        response = self.fetch("ClashMi/3.0 (Mihomo/25.1)", hwid=None,
                              extra_headers=[(b"x-device-os", b"Windows"), (b"x-ver-os", b"11")])
        self.assertIn(".yaml", response.headers["content-disposition"])
        response = self.fetch("ClashMi/3.0 like Mihomo", hwid=None,
                              extra_headers=[(b"x-device-os", b"Windows"), (b"x-ver-os", b"11")])
        self.assertIn(".yaml", response.headers["content-disposition"])

    def test_registered_verified_client_keeps_credentials_and_configuration_on_repeat(self):
        self.matrix()
        response = self.fetch("ClashMi/3.0", extra_headers=self.headers())
        self.assertIn(".yaml", response.headers["content-disposition"])
        device = self.store[0]["devices"][1]
        device_id = device["id"]
        self.assertNotIn("client_config_format", device["routing"])
        credentials = deepcopy(self.store[0]["connections"])
        repeat = self.fetch("ClashMi/3.0", extra_headers=self.headers())
        self.assertEqual(repeat.body, response.body)
        self.assertEqual(self.store[0]["connections"], credentials)
        self.assertEqual(self.provision.call_count, 1)
        public = manager.profile_response(self.store[0])["devices"][1]
        self.assertEqual(public["subscription_format"], "mihomo")
        self.assertEqual(public["verified_clients"][0]["core_version"], "25.1")
        # New app/core version is unverified: default YAML, no credential rotation
        # or rewriting the saved explicit exporter selection.
        headers = [(key, b"26.0" if key == b"x-core-version" else value) for key, value in self.headers()]
        unknown = self.fetch("ClashMi/3.0", extra_headers=headers)
        self.assertIn(".yaml", unknown.headers["content-disposition"])
        self.assertEqual(self.store[0]["connections"], credentials)
        self.assertEqual(self.store[0]["devices"][1]["id"], device_id)
        self.assertNotIn("client_config_format", self.store[0]["devices"][1]["routing"])

    def test_profile_edit_cannot_remove_or_replace_observed_core_metadata(self):
        self.matrix()
        self.fetch("ClashMi/3.0", extra_headers=self.headers())
        devices = [manager.ProfileDeviceInput(**{**device, "client_version": "fake", "os_version": "fake"}) for device in self.store[0]["devices"]]
        manager.update_profile("profile", manager.ProfileUpdate(devices=devices))
        device = self.store[0]["devices"][1]
        self.assertEqual(device["core_name"], "Mihomo")
        self.assertEqual(device["core_version"], "25.1")
        self.assertEqual(device["client_version"], "3.0")
        self.assertEqual(device["os_version"], "11")

    def test_changed_requirements_remove_recommendations_and_format(self):
        self.matrix()
        self.assertEqual(len(manager.verified_profile_clients(self.store[0], "common")), 1)
        self.store[0]["devices"][0]["routing"]["tunnel_ech"] = True
        self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
        response = self.fetch("ClashMi/3.0", hwid=None, extra_headers=self.headers())
        self.assertIn(".yaml", response.headers["content-disposition"])

    def test_inherited_strategy_is_checked_before_recommending_an_exporter(self):
        self.store[0]["routing"] = {}
        global_values = {**manager.routing_settings(), "strategy": "fallback"}
        with patch.object(manager, "routing_settings", return_value=global_values):
            scenario = manager.client_verification_scenario(self.store[0], "common")
            self.assertEqual(scenario["strategy"], "fallback")
            record = check(scenario, format="xray")
            with patch.object(policy, "verified_records", return_value=[record]):
                self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
                self.assertIn(".yaml", self.fetch("ClashMi/3.0", hwid=None, extra_headers=self.headers()).headers["content-disposition"])

    def test_rule_and_resolver_content_changes_invalidate_a_matching_check(self):
        self.store[0]["devices"][0]["routing"] = {"rules": "DOMAIN-SUFFIX,private.example,DIRECT"}
        self.matrix()
        self.assertEqual(len(manager.verified_profile_clients(self.store[0], "common")), 1)
        self.store[0]["devices"][0]["routing"]["rules"] = "DOMAIN-SUFFIX,other.example,DIRECT"
        self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
        self.matrix()
        dns = {**manager.dns_settings(), "nameserver": "https://private.example/dns-query?token=secret"}
        with patch.object(manager, "dns_settings", return_value=dns):
            self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
            scenario = json.dumps(manager.client_verification_scenario(self.store[0], "common"))
            self.assertNotIn("private.example", scenario)
            self.assertNotIn("other.example", scenario)
            self.assertNotIn("secret", scenario)

    def test_global_bypass_and_protection_switches_do_not_become_device_requirements(self):
        before = manager.client_verification_scenario(self.store[0], "common")
        global_values = {**manager.routing_settings(), "direct_games_enabled": True,
                         "direct_local_network": True, "tunnel_ech": True, "tunnel_privacy": True}
        with patch.object(manager, "routing_settings", return_value=global_values):
            self.assertEqual(manager.client_verification_scenario(self.store[0], "common"), before)

    def test_inherited_rules_change_invalidates_yaml_application_evidence(self):
        values = {**manager.routing_settings(), "rules": "DOMAIN-SUFFIX,private.example,DIRECT"}
        with patch.object(manager, "routing_settings", return_value=values):
            self.matrix(format="mihomo")
            self.assertEqual(len(manager.verified_profile_clients(self.store[0], "common")), 1)
            values["rules"] = "DOMAIN-SUFFIX,private.example,GATE.312"
            self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])

    def test_personal_rule_edits_change_scenario_without_exposing_entries(self):
        self.store[0]["devices"][0]["personal_rule_ids"] = ["personal"]
        rules = [{"id": "personal", "kind": "domain", "target": "DIRECT", "entries": ["private.example"]}]
        with patch.object(manager, "personal_rules", return_value=rules):
            self.matrix(format="mihomo")
            self.assertEqual(len(manager.verified_profile_clients(self.store[0], "common")), 1)
            rules[0]["target"] = "GATE.312"
            self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
            self.assertNotIn("private.example", json.dumps(manager.client_verification_scenario(self.store[0], "common")))

    def test_record_cannot_hide_unsupported_connections_or_platform_features(self):
        for routing, os in (({"direct_games_enabled": True}, "ios"),):
            with self.subTest(routing=routing, os=os):
                self.store[0]["devices"][0]["routing"] = routing
                record = check(manager.client_verification_scenario(self.store[0], "common"), os=os)
                with patch.object(policy, "verified_records", return_value=[record]):
                    self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])
        self.store[0]["devices"][0]["routing"] = {}
        self.store[0]["connections"][0]["component"] = "transport-unknown"
        record = check(manager.client_verification_scenario(self.store[0], "common"))
        with patch.object(policy, "verified_records", return_value=[record]):
            self.assertEqual(manager.verified_profile_clients(self.store[0], "common"), [])

    def test_import_page_only_lists_matching_verified_clients(self):
        self.matrix()
        response = self.fetch("Mozilla/5.0", hwid=None, extra_headers=[(b"accept", b"text/html")])
        self.assertIn("Clash Mi 3.0", response.body.decode())
        self.assertIn("Mihomo 25.1", response.body.decode())
        self.assertNotIn("sing-box://", response.body.decode())
        self.store[0]["devices"][0]["routing"] = {"tunnel_ech": True}
        response = self.fetch("Mozilla/5.0", hwid=None, extra_headers=[(b"accept", b"text/html")])
        self.assertNotIn("Clash Mi 3.0", response.body.decode())

    def test_bound_hwid_rechecks_verification_and_manual_legacy_export_is_preserved(self):
        self.matrix()
        self.fetch("ClashMi/3.0", extra_headers=self.headers())
        device = self.store[0]["devices"][1]
        token = manager.device_subscription_token("test-token", device["id"])
        headers = [(b"user-agent", b"ClashMi/3.0"), (b"x-hwid", b"phone"), *self.headers()]
        request = manager.Request({"type": "http", "method": "GET", "path": "/s/" + token, "query_string": b"", "headers": headers})
        self.assertIn(".yaml", manager.public_profile_subscription(token, request).headers["content-disposition"])
        with patch.object(policy, "verified_records", return_value=[]):
            self.assertIn(".yaml", manager.public_profile_subscription(token, request).headers["content-disposition"])
        self.store[0]["devices"].append({"id": "manual", "name": "Legacy", "manual": True, "routing": {"client_config_format": "singbox"}})
        self.store[0]["connections"].append({**deepcopy(self.initial["connections"][0]), "id": "manual-ss", "device_id": "manual"})
        manual_token = manager.device_subscription_token("test-token", "manual")
        self.assertIn(".yaml", manager.public_profile_subscription(manual_token, manager.Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []})).headers["content-disposition"])

    def test_scenario_excludes_secrets_but_detects_protocol_and_dns_changes(self):
        connection = self.store[0]["connections"][0]
        connection["settings"].update(password="settings-secret", private_key="private-secret", target="origin.example")
        scenario = manager.client_verification_scenario(self.store[0], "common")
        text = json.dumps(scenario)
        self.assertNotIn("secret", text)
        self.assertNotIn("test-only", text)
        self.assertNotIn("origin.example", text)
        connection["settings"]["mtu"] = 1300
        self.assertNotEqual(manager.client_verification_scenario(self.store[0], "common"), scenario)
        with patch.object(manager, "dns_settings", return_value={"enhanced_mode": "redir-host"}):
            self.assertNotEqual(manager.client_verification_scenario(self.store[0], "common"), scenario)

    def test_verification_context_endpoint_is_read_only_and_has_no_credentials(self):
        self.matrix()
        before = deepcopy(self.store)
        with patch.object(manager, "save_profiles", side_effect=AssertionError("Read wrote profile")):
            result = manager.profile_client_verification("profile")
        self.assertEqual(result["scenario"], manager.client_verification_scenario(self.store[0], "common"))
        self.assertEqual(result["verified_clients"][0]["client_name"], "Clash Mi")
        self.assertNotIn("test-only", json.dumps(result))
        self.assertNotIn("test-token", json.dumps(result))
        self.assertEqual(self.store, before)
        for profile_id, device_id in (("missing", None), ("profile", "missing")):
            with self.subTest(profile=profile_id, device=device_id), self.assertRaises(manager.HTTPException) as error:
                manager.profile_client_verification(profile_id, device_id)
            self.assertEqual(error.exception.status_code, 404)
