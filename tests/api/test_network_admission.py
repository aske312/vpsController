import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.api.support import api, manager, ROOT
from tests.api import test_network_probe as fixtures
import network_probe as probe
import network_routes as routes


class NetworkAdmissionTests(unittest.TestCase):
    def fixture(self, root):
        fixture = fixtures.NetworkProbePlanTests()
        fixture.setUp()
        vless = root / "vless"
        vless.mkdir()
        paths = {"config": vless / "config.json", "environment": vless / "reality.env", "clients": root / "clients.json"}
        paths["config"].write_text(json.dumps(fixture.config))
        paths["environment"].write_text("CDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n")
        paths["clients"].write_text(json.dumps(fixture.clients))
        return fixture, paths

    def test_semantic_evidence_ignores_unrelated_clients_but_detects_selected_changes(self):
        for change, expected in (("format", True), ("other-client", True), ("rename", True),
                                 ("revoke", False), ("uuid", False), ("path", False), ("routing", False), ("key", False)):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture, paths = self.fixture(root)
                plan = fixture.prepare()
                check = {"proof": {"method": "direct_vless_nonce_v1", **plan.evidence(probe.evidence_key(root, create=True))}}
                self.assertTrue(probe.evidence_current(check, paths, binding=fixture.binding, key_root=root))
                config, clients = copy.deepcopy(fixture.config), copy.deepcopy(fixture.clients)
                if change == "other-client":
                    clients.append({"id": "other", "protocol": "wg", "public_key": "unrelated"})
                    config["inbounds"][0]["settings"]["clients"].append({"id": "another-uuid"})
                if change == "rename": clients[0]["name"] = "New display name"
                if change == "revoke": config["inbounds"][0]["settings"]["clients"] = []
                if change == "uuid":
                    clients[0]["public_key"] = "rotated-uuid"
                    config["inbounds"][0]["settings"]["clients"][0]["id"] = "rotated-uuid"
                if change == "path": config["inbounds"][0]["streamSettings"]["xhttpSettings"]["path"] = "/new"
                if change == "routing": config["routing"] = {"rules": [{"outboundTag": "block"}]}
                paths["config"].write_text(json.dumps(config, indent=4) + "\n")
                paths["clients"].write_text(json.dumps(clients, indent=2))
                if change == "key": (root / "network-probe.key").unlink()
                self.assertEqual(probe.evidence_current(check, paths, binding=fixture.binding, key_root=root), expected)
                for secret in ("secret-uuid", "rotated-uuid", "another-uuid"):
                    self.assertNotIn(secret, json.dumps(check))

    def test_first_tls_client_is_provisioned_without_fabricating_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, paths = self.fixture(root)
            config = fixture.config
            config["inbounds"][0]["tag"] = "vless-tls"
            config["inbounds"][0]["settings"]["clients"] = []
            paths["config"].write_text(json.dumps(config))
            paths["environment"].write_text("TLS_ENABLED=yes\nTLS_DOMAIN=tls.example\n")
            paths["clients"].write_text("[]")
            for name, value in (("DATA_DIR", root), ("NETWORK_ENDPOINTS_FILE", root / "network.json"),
                                ("VLESS_CONFIG_DIR", paths["config"].parent), ("VLESS_CONFIG", paths["config"]),
                                ("VLESS_ENV", paths["environment"]), ("CLIENTS_FILE", paths["clients"])):
                self.enterContext(patch.object(api, name, value))
            self.enterContext(patch.object(api, "network_domain_probe", return_value={"domain": "tls.example", "resolved": ["203.0.113.10"], "matches_origin": True, "route": "direct"}))
            self.enterContext(patch.object(api.os, "chown", create=True))
            self.enterContext(patch.object(api.subprocess, "run", return_value=SimpleNamespace(returncode=0)))
            restart = self.enterContext(patch.object(api, "restart_vless_service"))
            self.enterContext(patch.object(api, "vless_client_query", return_value={}))
            self.enterContext(patch.object(api, "vless_reality_inbound", return_value=config["inbounds"][0]))
            api.write_network_endpoint_settings({"tls_relay_domains": ["tls.example"]})
            self.assertFalse(api.network_endpoint_check("tls_relay", "tls.example")["ready"])
            result = api.create_client(api.ClientCreate(name="First", protocol="vless-reality-xhttp", vless_routes=["tls"]))
            self.assertIn("@tls.example:443?", result["config"])
            self.assertEqual(len(api.read_clients()), 1)
            restart.assert_called_once()
            self.assertFalse(api.network_endpoint_check("tls_relay", "tls.example")["ready"])
            binding = routes.active_binding(api.read_route_registry(), "tls_relay", "tls.example")
            self.assertEqual(api.prepare_network_probe(binding)[0].consumer_id, f"direct:{result['id']}")

    def test_first_mihomo_route_and_retired_route_have_distinct_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.json"
            registry = routes.migrate({"cdn_domains": ["cdn.example"], "tls_relay_domains": ["tls.example"]})
            path.write_text(json.dumps({"route_registry": registry}))
            with patch.object(manager, "NETWORK_ENDPOINTS_FILE", path), patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"), patch.object(manager, "module_is_installed", return_value=True), patch.object(manager, "module_is_ready", return_value=True), patch.object(manager, "direct_tls_domain_ready", return_value=True):
                for mode in ("cdn", "tls"):
                    settings = {"route_mode": mode, f"{mode}_domain": f"{mode}.example"}
                    self.assertEqual(manager.validate_connection("transport-reality", settings)[f"{mode}_domain"], f"{mode}.example")
                    self.assertFalse(manager.mihomo_route_endpoint_ready(mode, f"{mode}.example"))
                for binding in registry["bindings"]: binding["state"] = "retired"
                path.write_text(json.dumps({"route_registry": registry}))
                for mode in ("cdn", "tls"):
                    with self.assertRaises(manager.HTTPException):
                        manager.validate_connection("transport-reality", {"route_mode": mode, f"{mode}_domain": f"{mode}.example"})

    def test_checks_are_per_consumer_and_unknown_consumer_cannot_borrow_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, paths = self.fixture(root)
            fixture.clients.append({**fixture.clients[0], "id": "tablet", "public_key": "tablet-uuid"})
            fixture.config["inbounds"][0]["settings"]["clients"].append({"id": "tablet-uuid"})
            refs = [*fixture.consumers, {**fixture.consumers[0], "id": "direct:tablet"}]
            paths["config"].write_text(json.dumps(fixture.config))
            paths["clients"].write_text(json.dumps(fixture.clients))
            for name, value in (("DATA_DIR", root), ("NETWORK_ENDPOINTS_FILE", root / "network.json"), ("VLESS_CONFIG", paths["config"]), ("VLESS_ENV", paths["environment"]), ("CLIENTS_FILE", paths["clients"])):
                self.enterContext(patch.object(api, name, value))
            self.enterContext(patch.object(api, "network_consumers", return_value=refs))
            self.enterContext(patch.object(api, "network_domain_probe", return_value={"domain": "cdn.example", "resolved": ["203.0.113.10"], "matches_origin": False, "route": "proxy_or_cdn"}))
            api.write_network_endpoint_settings({"cdn_domains": ["cdn.example"]})
            def exchange(plan):
                return {"state": "verified", "method": "direct_vless_nonce_v1"} if plan.consumer_id == "direct:phone" else {"state": "failed", "message": "failed tablet"}
            with patch.object(api, "execute_network_probe", side_effect=exchange):
                for consumer_id in ("direct:phone", "direct:tablet", "direct:missing"):
                    result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="cdn", domain="cdn.example", consumer_id=consumer_id))
                    self.assertEqual(result["ready"], consumer_id == "direct:phone")
            self.assertTrue(api.network_endpoint_check("cdn", "cdn.example", consumer_id="direct:phone")["ready"])
            self.assertFalse(api.network_endpoint_check("cdn", "cdn.example", consumer_id="direct:tablet")["ready"])
            self.assertFalse(api.network_endpoint_check("cdn", "cdn.example", consumer_id="direct:missing")["ready"])
            check = api.network_endpoint_check("cdn", "cdn.example", consumer_id="direct:phone")
            self.assertEqual(check["verified_connection"]["transport"], "xhttp")
            with patch.object(api, "network_endpoint_check", return_value={**check, "verified_connection": {"protocol": "vless"}}):
                self.assertFalse(api.network_route_admission("wg", "udp_relay", "cdn.example"))
