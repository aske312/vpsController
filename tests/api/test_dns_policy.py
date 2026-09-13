import copy
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
from dns_policy import BOOTSTRAP_URLS, build_xray_dns, dns_probe_query, successful_dns_response


class DnsPolicyTests(unittest.TestCase):
    def test_bootstrap_is_encrypted_and_excluded_from_site_fallback(self):
        upstreams = ["https://dns.example/dns-query", "https://backup.example/dns-query"]
        original = copy.deepcopy(upstreams)
        for bootstrap in BOOTSTRAP_URLS:
            with self.subTest(bootstrap=bootstrap):
                config = build_xray_dns(upstreams, bootstrap)
                first = config["servers"][0]
                self.assertEqual(first["address"], BOOTSTRAP_URLS[bootstrap])
                self.assertTrue(first["skipFallback"])
                self.assertEqual(first["domains"], ["full:dns.example", "full:backup.example"])
                self.assertTrue(config["disableFallbackIfMatch"])
                self.assertEqual(config["servers"][1:], upstreams)
        self.assertEqual(upstreams, original)

    def test_literal_addresses_do_not_require_bootstrap(self):
        for servers in (["1.1.1.1"], ["https://1.1.1.1/dns-query"]):
            self.assertEqual(build_xray_dns(servers)["servers"], servers)
        with self.assertRaises(ValueError):
            build_xray_dns(["https://user:secret@dns.example/dns-query"])

    def test_probe_rejects_error_empty_truncated_and_unrelated_answers(self):
        query = dns_probe_query()
        answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x01\x01\x01\x01"
        def response(flags=0x8180, count=1):
            return query[:2] + struct.pack("!5H", flags, 1, count, 0, 0) + query[12:] + answer
        self.assertTrue(successful_dns_response(response(), query))
        for invalid in (response(0x8182), response(0x8183), response(0x8380), response(count=0), b"", response()[:12], response().replace(b"example", b"invalid")):
            self.assertFalse(successful_dns_response(invalid, query))

    def test_failed_preflight_cannot_modify_live_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "xray.json"
            config.write_text('{"unchanged":true}', encoding="utf-8")
            with patch.object(api, "VLESS_CONFIG", config), patch.object(api, "probe_xray_dns", side_effect=ValueError("unavailable")), patch.object(api, "persist_env_values") as persist, patch.object(api, "apply_vrx_dns") as apply:
                payload = api.DnsSettingsUpdate(selected_id="cloudflare", apply_system=False, apply_wg=False, apply_awg=False, apply_shadowsocks=False, apply_vrx=True, prefer_encrypted=True)
                with self.assertRaises(api.HTTPException) as raised:
                    api.update_dns_settings(payload)
                self.assertEqual(raised.exception.status_code, 422)
                persist.assert_not_called()
                apply.assert_not_called()
                self.assertEqual(config.read_text(encoding="utf-8"), '{"unchanged":true}')

    def test_encrypted_backup_cannot_downgrade_to_plain_dns(self):
        providers = [{"id": "secure", "addresses": ["1.1.1.1"], "doh_url": "https://dns.example/dns-query"}, {"id": "plain", "addresses": ["8.8.8.8"]}]
        with self.assertRaises(api.HTTPException):
            api.dns_vrx_servers({"selected_id": "secure", "prefer_encrypted": True, "fallback_enabled": True, "fallback_id": "plain"}, providers)
