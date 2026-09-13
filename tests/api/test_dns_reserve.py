import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class DnsReserveTests(unittest.TestCase):
    def setUp(self):
        self.providers = [
            {"id": "primary", "addresses": ["1.1.1.1", "1.0.0.1"], "doh_url": "https://primary.example/dns-query"},
            {"id": "backup", "addresses": ["8.8.8.8", "8.8.4.4"], "doh_url": "https://backup.example/dns-query"},
            {"id": "custom", "addresses": ["9.9.9.9"]},
        ]
        self.settings = {"selected_id": "primary", "fallback_enabled": True, "profiles": {}}

    def test_legacy_reserve_keeps_provider_addresses(self):
        self.assertEqual(api.dns_resolvers_for(self.settings, self.providers, "system")[0], ["1.1.1.1", "1.0.0.1"])

    def test_independent_backup_and_component_primary(self):
        self.settings.update(fallback_id="backup", profiles={"wg": "custom"})
        before = copy.deepcopy(self.providers)
        self.assertEqual(api.dns_resolvers_for(self.settings, self.providers, "wg")[0], ["9.9.9.9", "8.8.8.8"])
        self.assertEqual(api.dns_resolvers_for(self.settings, self.providers, "system")[0], ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(self.providers, before)

    def test_disabled_backup_uses_only_primary(self):
        self.settings.update(fallback_enabled=False, fallback_id="backup")
        self.assertEqual(api.dns_resolvers_for(self.settings, self.providers, "wg")[0], ["1.1.1.1"])

    def test_duplicate_addresses_are_not_repeated(self):
        self.settings["fallback_id"] = "primary"
        self.assertEqual(api.dns_resolvers_for(self.settings, self.providers, "system")[0], ["1.1.1.1"])

    def test_unknown_backup_rejected(self):
        self.settings["fallback_id"] = "missing"
        with self.assertRaises(api.HTTPException) as raised:
            api.dns_resolvers_for(self.settings, self.providers, "system")
        self.assertEqual(raised.exception.status_code, 422)

    def test_save_applies_backup_to_system_and_protocols(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("ENV_FILE", "VLESS_CONFIG", "DNS_SETTINGS_FILE", "SYSTEM_RESOLVED_DROPIN", "SYSTEM_RESOLV_CONF"):
                self.enterContext(patch.object(api, name, root / name))
            api.VLESS_CONFIG.write_text("{}")
            self.enterContext(patch.object(api, "dns_provider_list", return_value=self.providers))
            self.enterContext(patch.object(api, "dns_status", return_value={"ok": True}))
            persist = self.enterContext(patch.object(api, "persist_env_values"))
            system = self.enterContext(patch.object(api, "apply_system_dns"))
            xray = self.enterContext(patch.object(api, "apply_vrx_dns"))
            self.enterContext(patch.object(api, "dns_wire_query", return_value=(True, 1.0)))
            self.enterContext(patch.object(api, "probe_xray_dns"))
            payload = api.DnsSettingsUpdate(selected_id="primary", fallback_id="backup", apply_system=True, prefer_encrypted=True, profiles={"wg": "custom"})
            api.update_dns_settings(payload)
            system.assert_called_once_with(["1.1.1.1", "8.8.8.8"])
            xray.assert_called_once_with(["https://primary.example/dns-query", "https://backup.example/dns-query"], bootstrap_id="cloudflare")
            self.assertEqual(persist.call_args.args[0]["WG_DNS"], "9.9.9.9, 8.8.8.8")
            self.assertEqual(persist.call_args.args[0]["AWG_DNS"], "1.1.1.1, 8.8.8.8")
            self.assertEqual(json.loads(api.DNS_SETTINGS_FILE.read_text())["fallback_id"], "backup")

    def test_status_compares_each_component_with_its_own_profile(self):
        self.settings.update(fallback_id="backup", profiles={"wg": "custom"}, prefer_encrypted=False)
        actual = {"WG_DNS": "9.9.9.9, 8.8.8.8", "AWG_DNS": "1.1.1.1, 8.8.8.8", "SHADOWSOCKS_DNS": "1.1.1.1, 8.8.8.8", "VRX_DNS": "1.1.1.1, 8.8.8.8"}
        with patch.object(api, "read_dns_settings", return_value=self.settings), patch.object(api, "dns_provider_list", return_value=self.providers), patch.object(api, "current_env_value", side_effect=lambda key, default: actual[key]), patch.object(api, "run", return_value="enabled"), patch.object(api, "system_dns_state", return_value={}), patch.object(api, "direct_dns_effects", return_value={}), patch.object(api, "actual_vrx_dns", return_value=actual["VRX_DNS"]):
            details = api.dns_status()["protocol_effect_details"]
        self.assertTrue(all(effect["matches_selected"] for effect in details.values()))


if __name__ == "__main__":
    unittest.main()
