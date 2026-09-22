import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api, manager
import network_routes as routes


class NetworkRouteTests(unittest.TestCase):
    def test_mihomo_tls_cannot_reuse_retired_route_or_ignore_corrupt_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "network.json"
            registry = routes.synchronize({"tls_relay_domains": ["relay.example"]}, {}, [])
            with patch.object(manager, "NETWORK_ENDPOINTS_FILE", path), patch.object(manager, "direct_tls_domain_ready", return_value=True) as probe:
                for content in (json.dumps({"route_registry": registry}), "{", "[]"):
                    path.write_text(content)
                    self.assertFalse(manager.mihomo_route_endpoint_ready("tls", "relay.example"))
                probe.assert_not_called()

    def test_migration_groups_addresses_but_preserves_independent_bindings(self):
        settings = {"cdn_domains": ["same.example"], "tls_relay_domains": ["same.example"]}
        registry = routes.migrate(settings)
        self.assertEqual(registry, routes.migrate(settings))
        self.assertEqual(len(registry["endpoints"]), 1)
        self.assertEqual(len(registry["bindings"]), 2)
        self.assertTrue(all(not binding["check"]["ready"] for binding in registry["bindings"]))
        self.assertTrue(all(binding["applied_revision"] is None for binding in registry["bindings"]))

    def test_retirement_preserves_snapshot_and_readding_address_does_not_heal_old_consumer(self):
        settings = {"tls_relay_domains": ["relay.example"]}
        consumer = {"id": "direct:phone", "kind": "tls_relay", "address": "relay.example", "label": "Phone"}
        retired = routes.synchronize(settings, {}, [consumer])
        snapshot = retired["bindings"][0]
        self.assertEqual(snapshot["state"], "retired")
        self.assertTrue(snapshot["forwarding_retained"])
        self.assertTrue(routes.retired(retired, "tls_relay", "relay.example"))
        readded = routes.synchronize({"route_registry": retired}, settings, [])
        self.assertFalse(routes.retired(readded, "tls_relay", "relay.example"))
        self.assertEqual(routes.consumer_state(readded, "direct:phone")["state"], "stale")
        self.assertEqual(readded["bindings"][0], snapshot)
        self.assertNotEqual(readded["bindings"][0]["id"], readded["bindings"][1]["id"])

    def test_deletion_atomically_preserves_direct_and_mihomo_references_without_touching_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.enterContext(patch.object(api, "DATA_DIR", root))
            for name in ("NETWORK_ENDPOINTS_FILE", "NETWORK_ENDPOINT_RETIREMENTS_FILE", "CLIENTS_FILE", "VLESS_ENV", "VLESS_CONFIG"):
                self.enterContext(patch.object(api, name, root / name))
            self.enterContext(patch.object(api, "require_route_management"))
            self.enterContext(patch.object(api, "network_status", return_value={}))
            mutate = self.enterContext(patch.object(api, "update_protocol_settings", side_effect=AssertionError("Runtime must be preserved")))
            api.VLESS_ENV.write_text("TLS_DOMAIN=relay.example\nTLS_ENABLED=yes\n")
            api.CLIENTS_FILE.write_text(json.dumps([{"id": "phone", "name": "Phone", "protocol": "wg", "channel_mode": "udp_relay", "route_endpoint": "relay.example"}]))
            profiles = root / "mihomo" / "profiles.json"
            profiles.parent.mkdir()
            profiles.write_text(json.dumps([{"id": "profile", "connections": [
                {"id": "tls", "component": "transport-reality", "settings": {"route_mode": "tls", "tls_domain": "relay.example"}},
                {"id": "direct", "component": "transport-reality", "settings": {"route_mode": "direct", "cdn_domain": "relay.example"}}
            ]}]))
            profile_before = profiles.read_bytes()
            api.write_network_endpoint_settings({"udp_relay_domains": ["relay.example"], "tls_relay_domains": ["relay.example"], "cdn_domains": ["relay.example"]})
            before = api.CLIENTS_FILE.read_bytes()
            api.delete_network_endpoint("udp_relay", "relay.example")
            api.delete_network_endpoint("tls_relay", "relay.example")
            api.delete_network_endpoint("cdn", "relay.example")
            stored = json.loads(api.NETWORK_ENDPOINTS_FILE.read_text())
            self.assertEqual(stored["udp_relay_domains"], [])
            self.assertEqual(routes.consumer_state(stored["route_registry"], "direct:phone")["state"], "stale")
            self.assertEqual(api.CLIENTS_FILE.read_bytes(), before)
            self.assertEqual(profiles.read_bytes(), profile_before)
            self.assertEqual(routes.consumer_state(stored["route_registry"], "mihomo:profile:tls")["state"], "stale")
            self.assertEqual(routes.consumer_state(stored["route_registry"], "mihomo:profile:direct")["state"], "current")
            mutate.assert_not_called()

    def test_corrupt_binding_is_not_accepted_as_empty_or_current(self):
        import copy
        registry = routes.migrate({"cdn_domains": ["cdn.example"]})
        for patch_value in ({"endpoint_id": "missing"}, {"desired_revision": 0}, {"consumers": [None]}, {"state": "missing"}):
            with self.subTest(patch_value=patch_value):
                corrupt = copy.deepcopy(registry)
                corrupt["bindings"][0].update(patch_value)
                with self.assertRaises(ValueError):
                    routes.migrate({"route_registry": corrupt})

    def test_failed_atomic_commit_keeps_route_available_and_consumer_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(api, "DATA_DIR", root), patch.object(api, "NETWORK_ENDPOINTS_FILE", root / "network.json"), patch.object(api, "network_consumers", return_value=[]):
                api.write_network_endpoint_settings({"udp_relay_domains": ["relay.example"]})
                before = api.NETWORK_ENDPOINTS_FILE.read_bytes()
                with patch.object(api.application_operation, "atomic_json", side_effect=OSError("disk full")), self.assertRaises(api.HTTPException):
                    api.write_network_endpoint_settings({})
                self.assertEqual(api.NETWORK_ENDPOINTS_FILE.read_bytes(), before)
                self.assertFalse(routes.retired(api.read_route_registry(), "udp_relay", "relay.example"))

    def test_explicit_probe_counts_new_failures_per_revision_and_deduplicates(self):
        registry = routes.migrate({"tls_relay_domains": ["relay.example"]})
        failed = {"status": "unresolved", "ready": False, "route": "unresolved", "resolved": [],
                  "matches_origin": False, "message": "DNS не подтверждён"}
        for _ in range(9):
            routes.record_check(registry, "tls_relay", "relay.example", failed)
        check = registry["bindings"][0]["check"]
        self.assertEqual(check["state"], "warning")
        self.assertEqual(check["failed_checks"], 1)
        # The same observed failure is one completed check, even if the UI polls it.
        routes.record_check(registry, "tls_relay", "relay.example", {**failed, "message": "DNS не подтверждён"})
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 1)
        for index in range(1, 10):
            routes.record_check(registry, "tls_relay", "relay.example", {**failed, "message": f"Ошибка {index}"})
        self.assertEqual(registry["bindings"][0]["check"]["state"], "error")
        routes.record_check(registry, "tls_relay", "relay.example", {"status": "ready", "ready": True,
                             "route": "proxy_or_cdn", "resolved": ["203.0.113.10"], "matches_origin": False,
                             "message": "Адрес подтверждён"})
        self.assertEqual(registry["bindings"][0]["check"]["state"], "ready")
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 0)
        registry["bindings"][0]["desired_revision"] = 2
        routes.record_check(registry, "tls_relay", "relay.example", failed)
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 1)
