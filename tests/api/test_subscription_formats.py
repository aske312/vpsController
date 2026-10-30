"""Mihomo YAML subscriptions preserve device identities and credentials."""
from contextlib import nullcontext
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT, managed_mihomo_fixture


class SubscriptionFormatTests(unittest.TestCase):
    def test_readonly_legacy_subscriptions_keep_existing_devices_without_writes(self):
        self.fetch("ClashMeta/1.0")
        device_id = self.store[0]["devices"][1]["id"]
        bound_token = manager.device_subscription_token("test-token", device_id)
        from component_registry import ComponentRegistry
        ComponentRegistry(manager.DATA_ROOT.parent).receipt_path("mihomo").unlink()
        before = deepcopy(self.store)
        calls = self.provision.call_count
        with patch.object(manager, "save_profiles", side_effect=AssertionError("Read-only subscription wrote profiles")):
            self.fetch("ClashMeta/1.0")
            self.fetch("ClashMeta/1.0", hwid=None)
            request = manager.Request({"type": "http", "method": "GET", "path": "/s/" + bound_token,
                "query_string": b"", "headers": [(b"user-agent", b"ClashMeta/1.0"), (b"x-hwid", b"phone")]})
            self.assertEqual(manager.public_profile_subscription(bound_token, request).status_code, 200)
            with self.assertRaises(manager.HTTPException) as denied:
                self.fetch("ClashMeta/1.0", hwid="new-device")
            self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(self.store, before)
        self.assertEqual(self.provision.call_count, calls)

    def test_legacy_channels_create_uses_the_common_device(self):
        for device_id in ("profile-common", "custom-common"):
            with self.subTest(device_id=device_id), patch.object(manager, "module_is_ready", return_value=True), patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"):
                profile = manager.create_profile(manager.ProfileCreate(
                    name="Legacy client", channels=["transport-shadowsocks"],
                    devices=[manager.ProfileDeviceInput(id=device_id, name="Common")],
                ))
                self.assertEqual(profile["common_device_id"], device_id)
                self.assertEqual(profile["connections"][0]["device_id"], device_id)
                self.assertEqual(self.provision.call_args.args[1][0]["device_id"], device_id)

    def test_browser_qr_opens_import_page_without_registering_device(self):
        before = deepcopy(self.store)
        result = self.fetch("Mozilla/5.0", hwid=None, extra_headers=[(b"accept", b"text/html"), (b"host", b"example.com")])
        body = result.body.decode()
        self.assertNotIn("happ://add/", body)
        self.assertNotIn("sing-box://import-remote-profile", body)
        self.assertNotIn("client=v2rage", body)
        self.assertIn("Mihomo/Clash YAML", body)
        self.assertEqual(self.store, before)
        self.provision.assert_not_called()


    def test_remove_registered_device_preserves_other_devices(self):
        self.fetch("koala-clash/1.0", "first")
        self.fetch("ClashMi/3.0", "second")
        profile = manager.profile_response(self.store[0])
        removed = profile["devices"][1]["id"]
        retained = [entry for entry in profile["connections"] if entry["device_id"] != removed]
        # Deletion must not validate/reprovision retained connections or require
        # their modules to be available. Only the target credentials are revoked.
        with patch.object(manager, "deprovision") as deprovision, patch.object(manager, "validate_connection_inputs", side_effect=AssertionError("unrelated settings validated")):
            manager.delete_profile_device("profile", removed)
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.assertEqual([entry["credential"] for entry in self.store[0]["connections"]], [entry["credential"] for entry in retained])
        deprovision.assert_called_once()
        with patch.object(manager, "deprovision") as deprovision:
            self.assertTrue(manager.delete_profile_device("profile", removed)["already_removed"])
            deprovision.assert_not_called()

    def test_device_delete_protects_common_and_preserves_state_on_failure(self):
        self.fetch("ClashMi/3.0")
        before = deepcopy(self.store)
        with self.assertRaises(manager.HTTPException) as error:
            manager.delete_profile_device("profile", "common")
        self.assertEqual(error.exception.status_code, 422)
        with patch.object(manager, "deprovision", side_effect=RuntimeError("revoke failed")), self.assertRaisesRegex(RuntimeError, "revoke failed"):
            manager.delete_profile_device("profile", before[0]["devices"][1]["id"])
        self.assertEqual(self.store, before)

    def test_device_delete_revokes_retiring_connections_and_batches_reality(self):
        self.fetch("ClashMi/3.0")
        device_id = self.store[0]["devices"][1]["id"]
        self.store[0]["connections"][1]["component"] = "transport-reality"
        self.store[0]["retiring_connections"] = [{"id": "old", "device_id": device_id, "component": "transport-reality", "credential": {"id": "old-credential"}}]
        with patch.object(manager, "deprovision") as revoke, patch.object(manager, "apply_batched_reality_runtime") as apply:
            manager.delete_profile_device("profile", device_id)
        self.assertEqual(revoke.call_count, 2)
        self.assertTrue(all(call.kwargs["defer_reality_restart"] for call in revoke.call_args_list))
        apply.assert_called_once()
        self.assertEqual(self.store[0]["retiring_connections"], [])

    def test_same_hwid_in_different_apps_registers_separate_devices(self):
        self.fetch("koala-clash/1.0")
        first = deepcopy(self.store[0]["devices"][1])
        self.fetch("ClashMi/3.0")
        second = deepcopy(self.store[0]["devices"][2])
        self.assertEqual(first["hwid_hash"], second["hwid_hash"])
        self.assertNotEqual(first["id"], second["id"])
        self.fetch("koala-clash/2.0")
        self.fetch("ClashMi/4.0")
        self.assertEqual(len(self.store[0]["devices"]), 3)
        self.assertEqual(self.provision.call_count, 2)
        self.assertEqual(self.store[0]["devices"][1]["id"], first["id"])

    def test_legacy_device_is_migrated_without_reprovisioning(self):
        self.fetch("Karing/1.0")
        device = self.store[0]["devices"][1]
        device.pop("client_identity_key")
        previous = deepcopy(self.store[0]["connections"])
        self.fetch("Karing/2.0")
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.assertEqual(self.provision.call_count, 1)
        self.assertEqual(self.store[0]["connections"], previous)
        self.assertEqual(self.store[0]["devices"][1]["client_identity_key"], "karing")


    def setUp(self):
        managed_mihomo_fixture(self)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, "WG_CONFIG_BY_MODULE", {
            "transport-wg": root / "wg.conf", "transport-awg": root / "awg.conf",
        }))
        self.enterContext(patch.object(manager, "CONFIG_ROOT", root))
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        self.initial = {"id": "profile", "name": "Test", "common_device_id": "common", "subscription_token": "test-token",
                        "devices": [{"id": "common", "name": "Common", "routing": {}}],
                        "connections": [{"id": "common-ss", "device_id": "common", "component": "transport-shadowsocks", "settings": {},
                                         "credential": {"port": 12000, "method": "aes-128-gcm", "password": "test-only"}}]}
        self.store = [deepcopy(self.initial)]
        self.enterContext(patch.object(manager, "profiles", side_effect=lambda: deepcopy(self.store)))
        self.enterContext(patch.object(manager, "save_profiles", side_effect=lambda data: setattr(self, "store", deepcopy(data))))
        self.enterContext(patch.object(manager, "profile_runtime_transaction", side_effect=lambda *a, **k: nullcontext()))
        self.enterContext(patch.object(manager, "write_action"))
        self.enterContext(patch.object(manager, "reconcile_profile_encryption"))
        self.enterContext(patch.object(manager, "routing_settings", return_value={}))
        self.enterContext(patch.object(manager, "dns_settings", return_value={"enhanced_mode": "fake-ip", "nameserver": "https://dns.google/dns-query", "fallback": "1.1.1.1"}))
        self.enterContext(patch.object(manager, "profile_rules", return_value=[]))
        self.enterContext(patch.object(manager, "module_is_installed", return_value=True))
        self.enterContext(patch.object(manager, "public_endpoint", return_value="192.0.2.1"))
        self.enterContext(patch.object(manager, "validate_rendered_profile"))
        self.provision = self.enterContext(patch.object(manager, "provision_connections", side_effect=lambda pid, definitions, **kw:
            [{**entry, "credential": {"port": 12001, "method": "aes-128-gcm", "password": entry["device_id"]}} for entry in definitions]))

    def fetch(self, agent, hwid="phone", query=b"", extra_headers=()):
        headers = [(b"user-agent", agent.encode()), *extra_headers]
        if hwid:
            headers.append((b"x-hwid", hwid.encode()))
        return manager.public_profile_subscription("test-token", manager.Request({"type": "http", "method": "GET", "path": "/s/test-token",
                                                                                   "query_string": query, "headers": headers}))

    def test_retired_clients_fail_before_profile_writes_or_registration(self):
        clients = ("Happ/3.0", "v2rayN/7.0", "v2RAGE/1.9", "RocketVPN/582994",
                   "singbox-launcher/1.0", "sing-box MT/1.0", "SFA/1.0", "SFI/1.0")
        before = deepcopy(self.store)
        with patch.object(manager, "save_profiles") as save, patch.object(manager, "render_profile") as render:
            for agent, hwid in product(clients, (None, "new-device")):
                with self.subTest(agent=agent, hwid=hwid), self.assertRaises(manager.ProfileFormatError) as rejected:
                    self.fetch(agent, hwid)
                self.assertEqual(rejected.exception.status_code, 410)
                self.assertEqual(rejected.exception.code, "profile_format_retired")
                self.assertIn("Mihomo/Clash YAML", rejected.exception.detail)
            save.assert_not_called()
            render.assert_not_called()
        self.assertEqual(self.store, before)
        self.provision.assert_not_called()

    def test_retired_queries_fail_even_with_generic_or_yaml_user_agent(self):
        queries = [b"format=" + value for value in (b"singbox", b"sing-box", b"xray", b"uri", b"json", b"txt")]
        queries += [b"client=happ", b"client=singbox", b"client=v2rage", b"format=xray&format=yaml"]
        before = deepcopy(self.store)
        for query, agent in product(queries, ("CFNetwork/3860", "ClashMi/1.0")):
            with self.subTest(query=query, agent=agent), self.assertRaises(manager.ProfileFormatError) as rejected:
                self.fetch(agent, query=query)
            self.assertEqual(rejected.exception.status_code, 410)
        self.assertEqual(self.store, before)
        self.provision.assert_not_called()

    def test_unknown_format_is_distinct_and_does_not_echo_input(self):
        with self.assertRaises(manager.ProfileFormatError) as rejected:
            self.fetch("ClashMi/1.0", query=b"format=private-marker")
        self.assertEqual(rejected.exception.status_code, 422)
        self.assertEqual(rejected.exception.code, "unsupported_profile_format")
        self.assertNotIn("private-marker", rejected.exception.detail)
        self.assertEqual(self.store, [self.initial])
        self.provision.assert_not_called()

    def test_yaml_aliases_and_unrecognized_clients_keep_yaml_subscription(self):
        for query in (b"", b"format=mihomo", b"format=yaml"):
            response = self.fetch("UnrecognizedClient/1.0", hwid=None, query=query)
            self.assertIn(".yaml", response.headers["content-disposition"])
            self.assertIn(b'password: "test-only"', response.body)
        self.provision.assert_not_called()

    def test_http_error_contract_and_unknown_token_do_not_expose_state(self):
        from fastapi.testclient import TestClient
        overrides = {manager.subscription_mutation_access: lambda: False, manager.auth_required: lambda: None}
        with patch.dict(manager.app.dependency_overrides, overrides), \
                patch.object(manager, "get_action_payload", side_effect=AssertionError("Format errors must not read operation history")):
            client = TestClient(manager.app)
            for path in ("/s/test-token", "/api/mihomo/subscriptions/test-token",
                         "/api/mihomo/profiles/profile/config"):
                with self.subTest(path=path):
                    response = client.get(path, params={"format": "xray"})
                    self.assertEqual(response.status_code, 410)
                    body = response.json()
                    self.assertEqual(body["code"], "profile_format_retired")
                    self.assertEqual(body["supported_formats"], ["mihomo", "yaml"])
                    self.assertIsInstance(body["detail"], str)
                    self.assertNotIn("test-token", response.text)
                    self.assertNotIn("test-only", response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
        # A retired format must not reveal whether an unknown token was valid.
        with self.assertRaises(manager.HTTPException) as rejected:
            manager.public_profile_subscription("unknown", manager.Request({
                "type": "http", "method": "GET", "path": "/s/unknown", "query_string": b"format=xray", "headers": []}), False)
        self.assertEqual(rejected.exception.status_code, 404)

    def test_bound_subscription_rejects_retired_format_without_changing_binding(self):
        self.fetch("ClashMi/1.0")
        before = deepcopy(self.store)
        token = manager.device_subscription_token("test-token", self.store[0]["devices"][1]["id"])
        request = manager.Request({"type": "http", "method": "GET", "path": "/s/" + token,
                                   "query_string": b"format=singbox", "headers": [(b"x-hwid", b"phone")]})
        with self.assertRaises(manager.ProfileFormatError) as rejected:
            manager.public_profile_subscription(token, request)
        self.assertEqual(rejected.exception.status_code, 410)
        self.assertEqual(self.store, before)
        self.assertEqual(self.provision.call_count, 1)

    def test_mrs_is_automatic_for_new_existing_and_hwidless_devices(self):
        import os
        import yaml
        rules = ['GEOSITE,category-ads-all,REJECT',
                 'RULE-SET,security-threats,REJECT', 'RULE-SET,security-phishing,REJECT']
        with patch.dict(os.environ, {'PUBLIC_DOMAIN': 'panel.example'}), \
                patch.object(manager, 'MRS_CACHE'), \
                patch.object(manager, 'profile_rules', return_value=rules):
            for platform in ('ios', 'android', 'windows', 'macos', 'linux', 'unknown'):
                with self.subTest(platform=platform):
                    headers = [(b'x-device-os', platform.encode())]
                    first = self.fetch('ClashMeta/1.19.32', 'new-' + platform, extra_headers=headers)
                    saved_connections = deepcopy(self.store[0]['connections'])
                    config = yaml.safe_load(first.body)
                    self.assertEqual(len(config['rule-providers']), 2)
                    self.assertTrue(all(p['format'] == 'mrs' for p in config['rule-providers'].values()))
                    second = self.fetch('ClashMeta/1.19.32', 'new-' + platform, extra_headers=headers)
                    self.assertEqual(first.body, second.body)
                    self.assertEqual(self.store[0]['connections'], saved_connections)
            shared = yaml.safe_load(self.fetch('ClashMeta/1.19.32', hwid=None).body)
            self.assertTrue(all(p['format'] == 'mrs' for p in shared['rule-providers'].values()))
            self.assertEqual(len(self.store[0]['devices']), 7)

    def test_first_request_detects_format_and_repeat_keeps_device_credentials(self):
        for agent, extension, supported in [("ClashMeta/1.0", "yaml", ["mihomo"]), ("ClashMi/1.0", "yaml", ["mihomo"]),

                                            ("Karing/1.2", "yaml", ["mihomo", "singbox"]), ("UnrecognizedClient/1.0", "yaml", [])]:
            with self.subTest(agent=agent):
                self.store = [deepcopy(self.initial)]
                first = self.fetch(agent)
                connections = deepcopy(self.store[0]["connections"])
                second = self.fetch(agent)
                self.assertIn(f".{extension}", first.headers["content-disposition"])
                self.assertEqual(first.body, second.body)
                self.assertEqual(len(self.store[0]["devices"]), 2)
                self.assertEqual(connections, self.store[0]["connections"])
                self.assertEqual(manager.profile_response(self.store[0])["devices"][1]["supported_formats"], ["mihomo"])

    def test_application_signatures_precede_core_names_and_stale_client_name(self):
        cases = [
            ("HiddifyNext/2.5 (ios) like ClashMeta v2ray sing-box", "Hiddify", ("mihomo", "singbox")),
            ("HiddifyNextX/3.0 (android) like ClashMeta v2ray sing-box", "Hiddify", ("mihomo", "singbox")),
            ("FlClash/0.8", "FlClash", ("mihomo",)),
            ("Happ/3.13.0 (Windows) like sing-box", "Happ", ("xray",)),
            ("FlClash X/v0.3.0 core/v1.19 Platform/windows", "FlClashX", ("mihomo",)),
            ("FlClashX/0.2.0", "FlClashX", ("mihomo",)),
            ("prizrak-box/1.0.21", "Prizrak-Box", ("mihomo",)),
            ("Clash-Verge/2.0", "Clash Verge Rev", ("mihomo",)),
            ("ClashMi/1.0", "Clash Mi", ("mihomo",)),
            ("ClashMetaForAndroid/2.0", "Clash Meta for Android", ("mihomo",)),
            ("Mihomo-Party/1.0", "Clash Party", ("mihomo",)),
            ("Clash Party/1.0", "Clash Party", ("mihomo",)),
            ("Nyanpasu/2.0", "Clash Nyanpasu", ("mihomo",)),
            ("Sparkle/1.0", "Sparkle", ("mihomo",)),
            ("koala-clash/1.0", "Koala Clash", ("mihomo",)),
            ("singbox-launcher/1.0", "Sing-Box Launcher", ("singbox",)),
            ("sing-box MT/1.0", "sing-box MT", ("singbox",)),
            ("SFA/1.0", "sing-box for Android", ("singbox",)),
            ("SFM/1.0", "sing-box for Apple", ("singbox",)),
            ("SFW/1.0", "sing-box for Desktop", ("singbox",)),
        ]
        for agent, name, formats in cases:
            with self.subTest(agent=agent):
                self.assertEqual(manager.client_identity({"user_agent": agent, "client_name": "Karing"}), (name, formats))
        for agent in ("Unknown/1.0", "Throne/1.0"):
            self.assertEqual(manager.client_formats({"user_agent": agent}), ())

    def test_desktop_headers_register_and_reuse_device(self):
        clients = [("koala-clash/1.0", "Koala Clash"),
                   ("FlClash X/v0.3.0 core/v1.19 Platform/windows", "FlClashX"),
                   ("prizrak-box/1.0.21", "Prizrak-Box")]
        for (agent, name), platform in product(clients, ("Windows", "macOS", "Linux")):
            with self.subTest(client=name, platform=platform):
                self.store = [deepcopy(self.initial)]
                headers = [(b"x-device-os", platform.encode()), (b"x-ver-os", b"test-version"),
                           (b"x-device-model", b"Desktop model")]
                first = self.fetch(agent, hwid="desktop-id", extra_headers=headers)
                connections = deepcopy(self.store[0]["connections"])
                second = self.fetch(agent, hwid="desktop-id", extra_headers=headers)
                self.assertIn(".yaml", first.headers["content-disposition"])
                self.assertEqual(first.body, second.body)
                self.assertEqual(len(self.store[0]["devices"]), 2)
                device = self.store[0]["devices"][1]
                self.assertEqual(device["client_name"], name)
                self.assertEqual(device["os"], platform.lower())
                self.assertEqual(device["os_version"], "test-version")
                self.assertEqual(device["name"], "Desktop model")
                self.assertEqual(connections, self.store[0]["connections"])

    def test_desktop_clients_without_hwid_use_common_profile(self):
        for agent in ("FlClashX/0.2.0", "prizrak-box/1.0.21"):
            with self.subTest(agent=agent):
                response = self.fetch(agent, hwid=None)
                self.assertIn(".yaml", response.headers["content-disposition"])
                self.assertEqual(len(self.store[0]["devices"]), 1)
        self.provision.assert_not_called()

    def test_hwid_enabled_later_on_common_link_registers_device_on_refresh(self):
        common_response = self.fetch("FlClashX/0.2.0", hwid=None)
        self.assertEqual(len(self.store[0]["devices"]), 1)
        self.assertIn(b'password: "test-only"', common_response.body)

        refreshed_response = self.fetch(
            "FlClashX/0.2.0",
            hwid="phone-hwid",
            extra_headers=[
                (b"x-device-os", b"Android"),
                (b"x-ver-os", b"15"),
                (b"x-device-model", b"Pixel"),
            ],
        )

        self.assertEqual(len(self.store[0]["devices"]), 2)
        device = self.store[0]["devices"][1]
        self.assertEqual(device["scope"], "hwid")
        self.assertEqual(device["client_name"], "FlClashX")
        self.assertEqual(device["os"], "android")
        self.assertEqual(device["os_version"], "15")
        self.assertNotEqual(refreshed_response.body, common_response.body)
        self.assertIn(f'password: "{device["id"]}"'.encode(), refreshed_response.body)
        self.assertEqual(
            [entry["device_id"] for entry in self.store[0]["connections"]],
            ["common", device["id"]],
        )
        self.assertEqual(self.provision.call_count, 1)

    def test_multiple_hwid_clients_receive_independent_profiles(self):
        first = self.fetch("HiddifyNext/2.5 (ios) like ClashMeta v2ray sing-box", "hiddify-phone")
        second = self.fetch("ClashMetaForAndroid/1.0", "android-phone")
        self.assertIn(".yaml", first.headers["content-disposition"])
        self.assertIn(".yaml", second.headers["content-disposition"])
        self.assertEqual(len(self.store[0]["devices"]), 3)
        first_device, second_device = self.store[0]["devices"][1:]
        self.assertNotEqual(first_device["id"], second_device["id"])
        self.assertEqual(first_device["client_name"], "Hiddify")
        self.assertEqual(second_device["client_name"], "Clash Meta for Android")
        self.assertEqual(manager.profile_response(self.store[0])["devices"][1]["supported_formats"], ["mihomo"])
        self.assertIn(second_device["id"].encode(), second.body)


    def test_without_hwid_uses_standard_yaml_even_when_legacy_shared_exists(self):
        self.store[0]["devices"].append({"id": "shared", "name": "Shared", "manual": True, "routing": {"client_config_format": "singbox"}})
        connection = deepcopy(self.initial["connections"][0])
        connection.update(id="shared-ss", device_id="shared")
        connection["credential"]["password"] = "shared-password"
        self.store[0]["connections"].append(connection)
        response = self.fetch("ClashMi/1.14", hwid=None)
        self.assertIn(b'password: "test-only"', response.body)
        yaml = self.fetch("ClashMi/1.0", hwid=None)
        self.assertIn('.yaml', yaml.headers["content-disposition"])
        self.assertEqual(len(self.store[0]["devices"]), 2)
        self.provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
