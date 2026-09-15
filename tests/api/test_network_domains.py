import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api


class NetworkDomainsTests(unittest.TestCase):
    def test_ipv6_origin_is_ready_before_dns_record_is_added(self):
        def fake_run(*args, **kwargs):
            outputs = {
                ("ip", "-6", "-o", "addr", "show", "scope", "global"): "2: eth0    inet6 2a01:db8::10/64 scope global",
                ("ip", "-6", "route", "show", "default"): "default via 2a01:db8::1 dev eth0",
                ("ss", "-H", "-lnt6"): "LISTEN 0 4096 [::]:443 [::]:* users:(\"caddy\")",
            }
            return outputs.get(args, "")

        with patch.object(api, "PUBLIC_IPV6", ""), patch.object(api, "run", side_effect=fake_run):
            state = api.network_ipv6_state()
            check = next(item for item in api.network_capabilities(state)["checks"] if item["id"] == "ipv6")
        self.assertEqual(state["address"], "2a01:db8::10")
        self.assertTrue(state["ready"])
        self.assertEqual(check["status"], "ready")
        self.assertIn("AAAA", check["detail"])

    def test_shared_panel_and_cdn_domain_retains_both_roles(self):
        with patch.object(api, 'PUBLIC_DOMAIN', 'shared.example.com'), patch.object(api, 'VLESS_CDN_DOMAIN', ''), patch.object(api.cdn_security, 'read_routes', return_value=[{'domain': 'shared.example.com'}]), patch.object(api.socket, 'getaddrinfo', return_value=[]), patch.object(api, 'run', return_value=''):
            domains = api.network_status()['domains']
        self.assertEqual(len(domains), 1)
        self.assertEqual(domains[0]['role'], 'panel, VLESS CDN')

    def test_gateway_domains_are_visible_without_global_cdn_setting(self):
        routes = [
            {"domain": "cdn.example.com", "cloudflare": True},
            {"domain": "cdn.example.com", "cloudflare": True},
            {"domain": "tls.example.com", "cloudflare": False},
        ]
        with patch.object(api, "PUBLIC_DOMAIN", "panel.example.com"), patch.object(api, "VLESS_CDN_DOMAIN", ""), patch.object(api.cdn_security, "read_routes", return_value=routes), patch.object(api.socket, "getaddrinfo", return_value=[]), patch.object(api, "run", return_value=""):
            domains = api.network_status()["domains"]
        self.assertEqual([(d["value"], d["role"], d["source"]) for d in domains], [
            ("panel.example.com", "panel", "environment"),
            ("cdn.example.com", "VLESS CDN", "gateway"),
            ("tls.example.com", "VLESS TLS", "gateway"),
        ])

    def test_transport_endpoints_are_saved_and_exposed_as_network_domains(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            endpoint_file = data_dir / "network-endpoints.json"
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", endpoint_file):
                saved = api.write_network_endpoint_settings({
                    "cdn_domain": "CDN.Example.com", "tls_relay_domain": "tls.example.com", "udp_relay_domain": "udp.example.com",
                })
                self.assertEqual(saved["cdn_domain"], "cdn.example.com")
                self.assertEqual(json.loads(endpoint_file.read_text())["tls_relay_domain"], "tls.example.com")
                self.assertEqual(api.read_network_endpoint_settings()["udp_relay_domain"], "udp.example.com")
                with patch.object(api, "network_status", return_value={"transport_endpoints": saved}):
                    response = api.update_network_endpoints(api.NetworkEndpointSettings(**saved), None)
                self.assertEqual(response["transport_endpoints"], saved)
                with self.assertRaises(api.HTTPException):
                    api.update_network_endpoints(api.NetworkEndpointSettings(cdn_domain="not a domain"), None)

    def test_protected_channel_mode_requires_matching_endpoint_and_is_exposed_to_channel_settings(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", data_dir / "network-endpoints.json"), patch.object(api, "PROTECTED_CHANNELS_FILE", data_dir / "protected-channels.json"):
                api.write_network_endpoint_settings({"cdn_domain": "", "tls_relay_domain": "", "udp_relay_domain": "udp.example.com"})
                api.save_channel_mode("wg", "udp_relay")
                self.assertEqual(api.channel_mode_for("wg"), "udp_relay")
                self.assertEqual(api.channel_mode_endpoint("wg", "198.51.100.1"), ("udp.example.com", "udp_relay"))
                self.assertEqual(api.editable_protocol_settings("wg", {})[0]["key"], "channel_mode")
                with self.assertRaises(api.HTTPException):
                    api.save_channel_mode("trojan", "udp_relay")

    def test_endpoint_check_reports_dns_and_origin_warning(self):
        with patch.object(api, "PUBLIC_IPV4", "198.51.100.1"), patch.object(api, "PUBLIC_IPV6", ""), patch.object(api, "PUBLIC_IP", "198.51.100.1"), patch.object(api.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("198.51.100.2", 0))]):
            result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="tls_relay", domain="relay.example.com"), None)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["ready"])
        with patch.object(api.socket, "getaddrinfo", return_value=[]):
            result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="udp_relay", domain="missing.example.com"), None)
        self.assertEqual(result["status"], "unresolved")

    def test_domain_identity_reports_dns_provider_and_ip_owner(self):
        self.assertEqual(api.network_dns_provider(["ns1.desec.io", "ns2.desec.org"]), "deSEC")
        self.assertEqual(api.network_known_hoster("Beget LLC", "BEGET-AS"), "Beget")
        self.assertEqual(api.network_known_hoster("SpaceWeb Ltd", "SWEB-AS"), "Sweb / SpaceWeb")
        self.assertEqual(api.network_known_hoster("SIA VEESP", "VEESP-LV-AS"), "Veesp")
        with patch.object(api, "network_dns_records", side_effect=lambda _, record_type: ["ns1.cloudflare.com", "ns2.cloudflare.com"] if record_type == 2 else ["edge.fastly.net"]), patch.object(api, "network_rdap_identity", return_value={"address": "203.0.113.10", "ptr": "edge.example.net", "provider": "Example Networks", "hoster": "Unknown", "asn": "AS64500", "network": "EXAMPLE-NET", "source": "RDAP"}):
            dns, edge, ips = api.network_domain_identity("cdn.example.com", ["203.0.113.10"], set())
        self.assertEqual(dns["provider"], "Cloudflare")
        self.assertEqual(dns["nameservers"], ["ns1.cloudflare.com", "ns2.cloudflare.com"])
        self.assertEqual(edge["provider"], "Fastly")
        self.assertEqual(ips[0]["asn"], "AS64500")
        self.assertEqual(ips[0]["ptr"], "edge.example.net")

    def test_direct_ip_identity_does_not_claim_a_dns_provider(self):
        with patch.object(api, "network_dns_records") as records, patch.object(api, "network_rdap_identity", return_value={"address": "203.0.113.10", "hoster": "Unknown"}):
            dns, edge, _ = api.network_domain_identity("203.0.113.10", ["203.0.113.10"], set())
        records.assert_not_called()
        self.assertEqual(dns["provider"], "DNS не используется")
        self.assertEqual(edge["provider"], "Unknown")

    def test_relay_endpoint_accepts_ip_without_relaxing_cdn_domain(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", data_dir / "network-endpoints.json"), patch.object(api, "network_status", return_value={"transport_endpoints": {}}):
                response = api.update_network_endpoints(api.NetworkEndpointSettings(cdn_domain="", tls_relay_domain="198.51.100.20", udp_relay_domain=""), None)
            self.assertEqual(json.loads((data_dir / "network-endpoints.json").read_text())["tls_relay_domain"], "198.51.100.20")
            with self.assertRaises(api.HTTPException):
                api.update_network_endpoints(api.NetworkEndpointSettings(cdn_domain="198.51.100.20"), None)

    def test_cdn_endpoint_connects_existing_panel_vless_origin(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root)
            config = data_dir / "vless.json"
            env = data_dir / "reality.env"
            config.write_text("{}")
            env.write_text("CDN_DOMAIN=old.example.com\nCDN_ENABLED=yes\n")
            with patch.object(api, "DATA_DIR", data_dir), patch.object(api, "NETWORK_ENDPOINTS_FILE", data_dir / "network-endpoints.json"), patch.object(api, "VLESS_CONFIG", config), patch.object(api, "VLESS_ENV", env), patch.object(api, "update_protocol_settings") as update, patch.object(api, "network_status", return_value={"transport_endpoints": {}}):
                api.update_network_endpoints(api.NetworkEndpointSettings(cdn_domain="new.example.com"), None)
            update.assert_called_once()
            self.assertEqual(update.call_args.args[0], "vless-reality-xhttp")
            self.assertEqual(update.call_args.args[1].cdn_domain, "new.example.com")
