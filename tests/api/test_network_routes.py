import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import ROOT, api, manager
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

    def test_mihomo_cdn_requires_end_to_end_route_but_preserves_existing_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "network.json"
            registry = routes.migrate({"cdn_domains": ["cdn.example"]})
            path.write_text(json.dumps({"route_registry": registry}))
            with patch.object(manager, "NETWORK_ENDPOINTS_FILE", path):
                self.assertFalse(manager.mihomo_route_endpoint_ready("cdn", "cdn.example"))
                routes.record_check(registry, "cdn", "cdn.example", {
                    "status": "ready", "ready": True, "verification": "end_to_end",
                    "binding_id": registry["bindings"][0]["id"], "checked_revision": 1,
                    "check_id": "cdn-checked", "checked_at": "2026-10-11T12:00:00+00:00",
                })
                path.write_text(json.dumps({"route_registry": registry}))
                self.assertTrue(manager.mihomo_route_endpoint_ready("cdn", "cdn.example"))

        requested = manager.ProfileConnectionInput(
            id="existing", device_id="common", component="transport-reality",
            settings={"route_mode": "cdn", "cdn_domain": "cdn.example"},
        )
        with patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"), \
             patch.object(manager, "validate_channels"), \
             patch.object(manager, "module_is_installed", return_value=True), \
             patch.object(manager, "module_is_ready", return_value=True), \
             patch.object(manager, "mihomo_route_endpoint_ready", return_value=False):
            existing = [{"id": "existing", "device_id": "common", "component": "transport-reality",
                         "settings": {**manager.connection_defaults("transport-reality"),
                                      "route_mode": "cdn", "cdn_domain": "cdn.example"}}]
            retained = manager.validate_connection_inputs([requested], existing)
            self.assertEqual(retained[0]["settings"]["cdn_domain"], "cdn.example")
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection_inputs([requested])

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
                  "matches_origin": False, "message": "DNS не подтверждён",
                  "binding_id": registry["bindings"][0]["id"], "checked_revision": 1}
        for index in range(9):
            routes.record_check(registry, "tls_relay", "relay.example", {**failed, "check_id": f"check-{index}"})
        check = registry["bindings"][0]["check"]
        self.assertEqual(check["state"], "warning")
        self.assertEqual(check["failed_checks"], 9)
        # Redelivery of one result is ignored, while the same failure observed
        # by a new explicit check belongs to a new completed check.
        routes.record_check(registry, "tls_relay", "relay.example", {**failed, "check_id": "check-8"})
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 9)
        routes.record_check(registry, "tls_relay", "relay.example", {**failed, "check_id": "check-9"})
        self.assertEqual(registry["bindings"][0]["check"]["state"], "error")
        routes.record_check(registry, "tls_relay", "relay.example", {"status": "ready", "ready": True, "verification": "end_to_end",
                             "route": "proxy_or_cdn", "resolved": ["203.0.113.10"], "matches_origin": False,
                             "message": "Адрес подтверждён", "binding_id": registry["bindings"][0]["id"],
                             "checked_revision": 1, "check_id": "check-ready", "checked_at": "2026-10-11T12:00:00+00:00"})
        self.assertEqual(registry["bindings"][0]["check"]["state"], "ready")
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 0)
        registry["bindings"][0]["desired_revision"] = 2
        routes.record_check(registry, "tls_relay", "relay.example", {**failed, "checked_revision": 2, "check_id": "check-new-revision"})
        self.assertEqual(registry["bindings"][0]["check"]["failed_checks"], 1)

    def test_late_probe_from_previous_revision_does_not_change_current_check(self):
        registry = routes.migrate({"cdn_domains": ["cdn.example"]})
        binding = registry["bindings"][0]
        binding["desired_revision"] = 2
        before = dict(binding["check"])
        routes.record_check(registry, "cdn", "cdn.example", {
            "status": "unresolved", "ready": False, "message": "Поздний ответ",
            "binding_id": binding["id"], "checked_revision": 1, "check_id": "old-check",
        })
        self.assertEqual(binding["check"], before)

    def test_each_explicit_api_probe_gets_an_identity_and_counts_same_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.enterContext(patch.object(api, "DATA_DIR", root))
            self.enterContext(patch.object(api, "NETWORK_ENDPOINTS_FILE", root / "network.json"))
            self.enterContext(patch.object(api, "network_consumers", return_value=[]))
            api.write_network_endpoint_settings({"tls_relay_domains": ["relay.example"]})
            failure = {"domain": "relay.example", "resolved": [], "matches_origin": False, "route": "unresolved"}
            with patch.object(api, "network_domain_probe", return_value=failure):
                first = api.check_network_endpoint(api.NetworkEndpointCheck(kind="tls_relay", domain="relay.example"))
                second = api.check_network_endpoint(api.NetworkEndpointCheck(kind="tls_relay", domain="relay.example"))
            self.assertNotEqual(first["check_id"], second["check_id"])
            self.assertEqual(first["failed_checks"], 1)
            self.assertEqual(second["failed_checks"], 2)
            stored = json.loads(api.NETWORK_ENDPOINTS_FILE.read_text())
            self.assertEqual(stored["route_registry"]["bindings"][0]["check"]["failed_checks"], 2)

    def test_dns_and_edge_evidence_stays_unchecked_without_end_to_end_probe(self):
        registry = routes.migrate({"cdn_domains": ["cdn.example"]})
        probe = {"domain": "cdn.example", "resolved": ["203.0.113.10"], "matches_origin": False,
                 "route": "proxy_or_cdn"}
        with patch.object(api, "read_route_registry", return_value=registry), \
             patch.object(api, "network_domain_probe", return_value=probe):
            result = api.network_endpoint_check("cdn", "cdn.example", include_saved=False)
        self.assertEqual(result["status"], "unchecked")
        self.assertFalse(result["ready"])
        self.assertEqual(result["verification"], "dns_only")

        legacy = registry["bindings"][0]
        legacy["check"] = {"state": "ready", "ready": True, "reason": "Старый DNS-only результат"}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(api, "NETWORK_ENDPOINTS_FILE", Path(temporary) / "network.json"), \
             patch.object(api, "read_route_registry", return_value=registry), \
             patch.object(api, "network_domain_probe", return_value=probe):
            api.NETWORK_ENDPOINTS_FILE.write_text(json.dumps({"route_registry": registry}))
            result = api.network_endpoint_check("cdn", "cdn.example")
        self.assertEqual(result["status"], "unchecked")
        self.assertFalse(result["ready"])

    def test_ready_requires_end_to_end_verification(self):
        registry = routes.migrate({"tls_relay_domains": ["relay.example"]})
        routes.record_check(registry, "tls_relay", "relay.example", {
            "status": "ready", "ready": True, "route": "proxy_or_cdn",
            "resolved": ["203.0.113.10"], "matches_origin": False,
            "message": "Только DNS", "binding_id": registry["bindings"][0]["id"],
            "checked_revision": 1, "check_id": "dns-only",
        })
        self.assertEqual(registry["bindings"][0]["check"]["state"], "unknown")
        self.assertFalse(registry["bindings"][0]["check"]["ready"])

        legacy = routes.migrate({"cdn_domains": ["cdn.example"]})
        legacy["bindings"][0]["check"] = {"state": "ready", "ready": True, "reason": "DNS ok"}
        migrated = routes.migrate({"route_registry": legacy})
        self.assertEqual(migrated["bindings"][0]["check"]["state"], "unknown")
        self.assertFalse(migrated["bindings"][0]["check"]["ready"])
        self.assertEqual(migrated["bindings"][0]["check"]["verification"], "dns_only")

    def test_api_and_mihomo_use_same_current_binding_evidence(self):
        import copy
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "network.json"
            registry = routes.migrate({"cdn_domains": ["route.example"], "tls_relay_domains": ["route.example"],
                                       "udp_relay_domains": ["route.example"]})
            probe = {"domain": "route.example", "resolved": ["203.0.113.10"], "matches_origin": True, "route": "direct"}
            for kind, alias in (("cdn", "cdn"), ("tls_relay", "tls"), ("udp_relay", "udp")):
                binding = routes.active_binding(registry, kind, "route.example")
                routes.record_check(registry, kind, "route.example", {
                    "status": "ready", "ready": True, "verification": "end_to_end",
                    "binding_id": binding["id"], "checked_revision": 1, "check_id": f"check-{kind}",
                    "checked_at": "2026-10-11T12:00:00+00:00",
                })
                patches = ({}, {"checked_revision": 2}, {"checked_revision": True}, {"binding_id": "other"},
                           {"check_id": ""}, {"checked_at": None}, {"verification": "dns_only"}, {"ready": False})
                for fields in patches:
                    with self.subTest(kind=kind, fields=fields):
                        snapshot = copy.deepcopy(registry)
                        routes.active_binding(snapshot, kind, "route.example")["check"].update(fields)
                        path.write_text(json.dumps({"route_registry": snapshot}))
                        with patch.object(api, "NETWORK_ENDPOINTS_FILE", path), patch.object(manager, "NETWORK_ENDPOINTS_FILE", path), \
                             patch.object(api, "network_domain_probe", return_value=probe), \
                             patch.object(manager, "direct_tls_domain_ready", side_effect=AssertionError("DNS must not grant admission")):
                            result = api.network_endpoint_check(kind, "route.example")
                            self.assertEqual(result["ready"], not fields)
                            self.assertEqual(manager.mihomo_route_endpoint_ready(alias, "route.example"), not fields)
                            self.assertEqual(result["binding_id"], binding["id"])
                            self.assertEqual(result["desired_revision"], 1)
                            # Origin privacy is independent of valid route evidence.
                            if not fields:
                                self.assertTrue(result["matches_origin"])
                                self.assertEqual(result["status"], "ready")

    def test_probe_file_evidence_is_invalidated_in_both_api_and_mihomo(self):
        import network_probe
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, env, clients = root / "config.json", root / "reality.env", root / "clients.json"
            for file in (config, env, clients):
                file.write_text("original")
            registry = routes.migrate({"tls_relay_domains": ["route.example"]})
            binding = registry["bindings"][0]
            routes.record_check(registry, "tls_relay", "route.example", {
                "status": "ready", "ready": True, "verification": "end_to_end",
                "binding_id": binding["id"], "checked_revision": 1, "check_id": "verified",
                "checked_at": "2026-10-11T12:00:00+00:00", "proof": {
                    "method": "direct_vless_nonce_v1", "source_revision": network_probe.source_revision({"config": config, "environment": env, "clients": clients})}})
            path = root / "network.json"
            path.write_text(json.dumps({"route_registry": registry}), encoding="utf-8")
            observation = {"domain": "route.example", "resolved": ["203.0.113.1"], "matches_origin": False, "route": "proxy_or_cdn"}
            with patch.object(api, "NETWORK_ENDPOINTS_FILE", path), patch.object(api, "VLESS_CONFIG", config), patch.object(api, "VLESS_ENV", env), patch.object(api, "CLIENTS_FILE", clients), patch.object(api, "network_domain_probe", return_value=observation), patch.object(manager, "NETWORK_ENDPOINTS_FILE", path), patch.object(manager, "DIRECT_VLESS_ENV", env):
                self.assertTrue(api.network_endpoint_check("tls_relay", "route.example")["ready"])
                self.assertTrue(manager.mihomo_route_endpoint_ready("tls", "route.example"))
                clients.write_text("rotated credential")
                self.assertFalse(api.network_endpoint_check("tls_relay", "route.example")["ready"])
                self.assertFalse(manager.mihomo_route_endpoint_ready("tls", "route.example"))

    def test_changed_binding_revision_invalidates_ready_without_deleting_consumers(self):
        registry = routes.migrate({"cdn_domains": ["route.example"]})
        binding = registry["bindings"][0]
        binding["consumers"] = [{"id": "direct:existing"}]
        routes.record_check(registry, "cdn", "route.example", {
            "status": "ready", "ready": True, "verification": "end_to_end",
            "binding_id": binding["id"], "checked_revision": 1, "check_id": "old",
            "checked_at": "2026-10-11T12:00:00+00:00",
        })
        self.assertTrue(routes.binding_ready(binding))
        binding["desired_revision"] = 2
        self.assertFalse(routes.binding_ready(binding))
        self.assertEqual(binding["consumers"], [{"id": "direct:existing"}])

    def test_tls_dns_only_is_not_mihomo_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "network.json"
            path.write_text(json.dumps({"tls_relay_domains": ["tls.example"]}))
            with patch.object(manager, "NETWORK_ENDPOINTS_FILE", path), \
                 patch.object(manager, "direct_tls_domain_ready", return_value=True) as dns:
                self.assertFalse(manager.mihomo_route_endpoint_ready("tls", "tls.example"))
                dns.assert_not_called()

    def test_existing_tls_connection_is_preserved_but_new_tls_needs_evidence(self):
        requested = manager.ProfileConnectionInput(id="existing", device_id="common", component="transport-reality",
                                                  settings={"route_mode": "tls", "tls_domain": "tls.example"})
        with patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"), \
             patch.object(manager, "validate_channels"), patch.object(manager, "module_is_installed", return_value=True), \
             patch.object(manager, "module_is_ready", return_value=True), \
             patch.object(manager, "mihomo_route_endpoint_ready", return_value=False), \
             patch.object(manager, "direct_tls_domain_ready", return_value=True):
            existing = [{"id": "existing", "device_id": "common", "component": "transport-reality",
                         "settings": {**manager.connection_defaults("transport-reality"), **requested.settings}}]
            self.assertEqual(manager.validate_connection_inputs([requested], existing)[0]["settings"]["tls_domain"], "tls.example")
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection_inputs([requested])
            changed = requested.model_copy(update={"settings": {"route_mode": "tls", "tls_domain": "other.example"}})
            with self.assertRaises(manager.HTTPException):
                manager.validate_connection_inputs([changed], existing)

    def test_direct_tls_creation_rejects_absent_route_before_any_live_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({"inbounds": [{"tag": "vless-tls", "protocol": "vless", "port": 18080,
                "settings": {"clients": []}, "streamSettings": {"network": "xhttp", "xhttpSettings": {"path": "/tls", "mode": "auto"}}}]}))
            env = root / "reality.env"
            env.write_text("TLS_ENABLED=yes\nTLS_DOMAIN=tls.example\n")
            path = root / "network.json"
            path.write_text(json.dumps({"tls_relay_domains": []}))
            before = (config.read_bytes(), env.read_bytes(), path.read_bytes())
            with patch.object(api, "VLESS_CONFIG_DIR", root), patch.object(api, "VLESS_CONFIG", config), \
                 patch.object(api, "VLESS_ENV", env), patch.object(api, "NETWORK_ENDPOINTS_FILE", path), \
                 patch.object(api, "network_domain_probe", return_value={"domain": "tls.example", "resolved": ["203.0.113.10"], "matches_origin": True, "route": "direct"}), \
                 patch.object(api, "run", side_effect=AssertionError("No runtime mutation")), \
                 patch.object(api, "write_clients", side_effect=AssertionError("No new client")):
                with self.assertRaises(api.HTTPException) as error:
                    api.create_client(api.ClientCreate(name="Phone", protocol="vless-reality-xhttp", vless_routes=["tls"]))
                self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(before, (config.read_bytes(), env.read_bytes(), path.read_bytes()))
