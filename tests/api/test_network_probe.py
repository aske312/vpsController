import copy
import json
import tempfile
import unittest
import socket
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api
import network_probe
import network_routes


class NetworkProbePlanTests(unittest.TestCase):
    def setUp(self):
        self.binding = network_routes.migrate({"cdn_domains": ["cdn.example"]})["bindings"][0]
        self.consumers = [{"id": "direct:phone", "owner": "direct", "kind": "cdn",
                           "address": "cdn.example", "reference": "snapshot"}]
        self.clients = [{"id": "phone", "protocol": "vless-reality-xhttp", "public_key": "secret-uuid",
                         "vless_routes": ["cdn"]}]
        self.config = {"inbounds": [{"tag": "vless-cdn", "protocol": "vless", "port": 18080,
                       "settings": {"clients": [{"id": "secret-uuid"}]},
                       "streamSettings": {"network": "xhttp", "xhttpSettings": {"path": "/live", "mode": "auto"}}}]}
        self.environment = {"CDN_ENABLED": "yes", "CDN_DOMAIN": "cdn.example", "CDN_PATH": "/stale"}

    def prepare(self):
        return network_probe.prepare_direct_vless(self.binding, self.consumers, self.clients, self.config, self.environment)

    def test_plan_uses_exact_consumer_and_live_path_without_mutating_inputs(self):
        original = copy.deepcopy((self.binding, self.consumers, self.clients, self.config, self.environment))
        plan = self.prepare()
        self.assertEqual(plan.client["streamSettings"]["xhttpSettings"]["path"], "/live")
        self.assertEqual(plan.client["settings"]["vnext"][0]["port"], 443)
        self.assertEqual(plan.listener_port, 18080)
        self.assertFalse(plan.client["streamSettings"]["tlsSettings"]["allowInsecure"])
        self.assertEqual(original, (self.binding, self.consumers, self.clients, self.config, self.environment))
        self.assertNotIn("secret-uuid", json.dumps(plan.summary()))
        self.assertNotIn("secret-uuid", repr(plan))
        plan.client["streamSettings"]["xhttpSettings"]["path"] = "/changed"
        self.assertEqual(self.config["inbounds"][0]["streamSettings"]["xhttpSettings"]["path"], "/live")

    def test_identity_changes_with_listener_configuration_or_binding_revision(self):
        first = self.prepare().fingerprint
        self.assertEqual(self.prepare().fingerprint, first)
        self.config["inbounds"][0]["streamSettings"]["xhttpSettings"]["path"] = "/new"
        second = self.prepare().fingerprint
        self.assertNotEqual(second, first)
        self.binding["desired_revision"] = 2
        self.assertNotEqual(self.prepare().fingerprint, second)

    def test_unrelated_or_revoked_credential_cannot_prepare_a_probe(self):
        for change in ("address", "owner", "credential", "route", "retired", "duplicate", "port"):
            with self.subTest(change=change):
                self.setUp()
                if change == "address": self.consumers[0]["address"] = "other.example"
                elif change == "owner": self.consumers[0]["owner"] = "mihomo"
                elif change == "credential": self.config["inbounds"][0]["settings"]["clients"] = []
                elif change == "route": self.clients[0]["vless_routes"] = ["direct"]
                elif change == "retired": self.binding["state"] = "retired"
                elif change == "duplicate": self.config["inbounds"].append(copy.deepcopy(self.config["inbounds"][0]))
                elif change == "port": self.binding["listen_port"] = 8443
                with self.assertRaises(network_probe.ProbeUnavailable): self.prepare()

    def test_tls_plan_never_borrows_cdn_listener(self):
        self.binding["kind"] = "tls_relay"
        self.environment.update(TLS_ENABLED="yes", TLS_DOMAIN="cdn.example")
        self.consumers[0]["kind"] = "tls_relay"
        self.clients[0]["vless_routes"] = ["tls"]
        with self.assertRaises(network_probe.ProbeUnavailable): self.prepare()
        self.config["inbounds"][0]["tag"] = "vless-tls"
        self.assertEqual(self.prepare().listener_tag, "vless-tls")

    def test_missing_producer_and_udp_stay_unavailable(self):
        for kind in ("cdn", "udp_relay"):
            self.binding["kind"] = kind
            self.environment["CDN_ENABLED"] = "no"
            with self.assertRaises(network_probe.ProbeUnavailable): self.prepare()

    def test_supported_transports_use_live_settings(self):
        for transport, key, settings in (("ws", "wsSettings", {"path": "/socket"}),
                                         ("grpc", "grpcSettings", {"serviceName": "tunnel"})):
            with self.subTest(transport=transport):
                self.config["inbounds"][0]["streamSettings"] = {"network": transport, key: settings}
                self.assertEqual(self.prepare().client["streamSettings"][key], settings)

    def test_api_preparation_never_marks_route_ready_or_persists_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            env = root / "reality.env"
            config.write_text(json.dumps(self.config))
            env.write_text("CDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n")
            clients_file = root / "clients.json"
            clients_file.write_text(json.dumps(self.clients))
            for attribute, value in (("DATA_DIR", root), ("NETWORK_ENDPOINTS_FILE", root / "network.json"),
                                     ("VLESS_CONFIG", config), ("VLESS_ENV", env), ("CLIENTS_FILE", clients_file)):
                self.enterContext(patch.object(api, attribute, value))
            self.enterContext(patch.object(api, "network_consumers", return_value=self.consumers))
            self.enterContext(patch.object(api, "read_clients", return_value=self.clients))
            self.enterContext(patch.object(api, "run", side_effect=AssertionError("Probe preparation must not run commands")))
            api.write_network_endpoint_settings({"cdn_domains": ["cdn.example"]})
            probe = {"domain": "cdn.example", "resolved": ["203.0.113.10"],
                     "matches_origin": False, "route": "proxy_or_cdn"}
            before = (config.read_bytes(), env.read_bytes())
            with patch.object(api, "network_domain_probe", return_value=probe), patch.object(network_probe, "execute_direct_vless", return_value={"state": "unavailable", "message": "test executor unavailable"}):
                result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="cdn", domain="cdn.example"))
            self.assertEqual(result["probe"]["state"], "unavailable")
            self.assertEqual(result["status"], "unchecked")
            self.assertFalse(result["ready"])
            self.assertEqual(result["verification"], "dns_only")
            self.assertNotIn("secret-uuid", json.dumps(result))
            self.assertNotIn("secret-uuid", api.NETWORK_ENDPOINTS_FILE.read_text())
            self.assertEqual(before, (config.read_bytes(), env.read_bytes()))

    def test_round_trip_requires_fresh_challenge_and_this_origin_peer(self):
        with network_probe.OriginWitness("127.0.0.1", 2) as witness:
            with socket.create_connection(("127.0.0.1", witness.port)) as client:
                client.sendall(b"x" * 32)
                self.assertEqual(client.recv(32), b"")
            self.assertFalse(witness.seen.is_set())
            with socket.create_connection(("127.0.0.1", witness.port), source_address=("127.0.0.2", 0)) as client:
                client.sendall(witness.challenge)
                try:
                    self.assertEqual(client.recv(32), b"")
                except ConnectionResetError:
                    pass
            self.assertFalse(witness.seen.is_set())
            with socket.create_connection(("127.0.0.1", witness.port)) as client:
                client.sendall(witness.challenge)
                self.assertEqual(network_probe._receive(client, 32), witness.response)
            self.assertTrue(witness.seen.is_set())
            port = witness.port
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)

    def test_credential_rotation_and_file_replacement_invalidate_evidence(self):
        before = self.prepare()
        self.clients[0]["public_key"] = "rotated-credential"
        self.config["inbounds"][0]["settings"]["clients"][0]["id"] = "rotated-credential"
        after = self.prepare()
        self.assertEqual(before.fingerprint, after.fingerprint)
        self.assertFalse(network_probe.same_plan(before, after))
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "config.json"
            file.write_text("original")
            paths = {"config": file}
            check = {"proof": {"method": "direct_vless_nonce_v1", "source_revision": network_probe.source_revision(paths)}}
            self.assertTrue(network_probe.evidence_current(check, paths))
            replacement = file.with_suffix(".new")
            replacement.write_text("original")
            replacement.replace(file)
            self.assertFalse(network_probe.evidence_current(check, paths))

    def test_missing_binary_cleans_responder_and_private_temporary_config(self):
        witnesses, directories = [], []
        witness_class = network_probe.OriginWitness
        temporary_class = tempfile.TemporaryDirectory
        def witness(*args):
            item = witness_class(*args)
            witnesses.append(item)
            return item
        def temporary(*args, **kwargs):
            item = temporary_class(*args, **kwargs)
            directories.append(Path(item.name))
            return item
        with patch.object(network_probe, "OriginWitness", side_effect=witness), patch.object(network_probe.tempfile, "TemporaryDirectory", side_effect=temporary):
            result = network_probe.execute_direct_vless(self.prepare(), "/missing/probe/xray", "127.0.0.1", timeout=0.2)
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("secret-uuid", json.dumps(result))
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))
        self.assertEqual(witnesses[0].socket.fileno(), -1)
        self.assertFalse(witnesses[0].thread.is_alive())

    def test_api_only_records_verified_current_plan_and_never_secrets(self):
        for change in (None, "credential", "revision", "retired", "listener", "file"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                self.setUp()
                root = Path(temporary)
                config, env, clients = root / "config.json", root / "reality.env", root / "clients.json"
                config.write_text(json.dumps(self.config))
                clients.write_text(json.dumps(self.clients))
                env.write_text("CDN_ENABLED=yes\nCDN_DOMAIN=cdn.example\n")
                with patch.object(api, "DATA_DIR", root), patch.object(api, "NETWORK_ENDPOINTS_FILE", root / "network.json"), patch.object(api, "VLESS_CONFIG", config), patch.object(api, "VLESS_ENV", env), patch.object(api, "CLIENTS_FILE", clients), patch.object(api, "network_consumers", return_value=self.consumers), patch.object(api, "read_clients", side_effect=lambda: json.loads(clients.read_text())):
                    api.write_network_endpoint_settings({"cdn_domains": ["cdn.example"]})
                    def execute(plan, *args):
                        if change in {"credential", "listener", "file"}:
                            current = json.loads(config.read_text())
                            if change == "credential":
                                current["inbounds"][0]["settings"]["clients"] = []
                            if change == "listener":
                                current["inbounds"][0]["streamSettings"]["xhttpSettings"]["path"] = "/changed"
                            if change == "file":
                                # An actual replacement has a distinct inode even when
                                # the filesystem clock gives rapid writes the same mtime.
                                replacement = config.with_suffix('.replacement')
                                replacement.write_text(json.dumps(current))
                                replacement.replace(config)
                            else:
                                config.write_text(json.dumps(current))
                        if change in {"revision", "retired"}:
                            current = json.loads(api.NETWORK_ENDPOINTS_FILE.read_text(encoding="utf-8"))
                            binding = current["route_registry"]["bindings"][0]
                            if change == "revision": binding["desired_revision"] += 1
                            else: binding["state"] = "retired"
                            api.NETWORK_ENDPOINTS_FILE.write_text(json.dumps(current), encoding="utf-8")
                        return {"state": "verified", "method": "direct_vless_nonce_v1", "consumer_id": plan.consumer_id, "fingerprint": plan.fingerprint, "origin_address": "203.0.113.1"}
                    observation = {"domain": "cdn.example", "resolved": ["203.0.113.1"], "matches_origin": False, "route": "proxy_or_cdn"}
                    with patch.object(api, "network_domain_probe", return_value=observation), patch.object(network_probe, "execute_direct_vless", side_effect=execute):
                        result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="cdn", domain="cdn.example"))
                        self.assertEqual(result["ready"], change is None)
                        self.assertEqual(api.network_endpoint_check("cdn", "cdn.example")["ready"], change is None)
                    serialized = api.NETWORK_ENDPOINTS_FILE.read_text(encoding="utf-8") + json.dumps(result)
                    self.assertNotIn("secret-uuid", serialized)
                    if change is None:
                        config.write_text(json.dumps(self.config) + " ")
                        with patch.object(api, "network_domain_probe", return_value=observation):
                            self.assertTrue(api.network_endpoint_check("cdn", "cdn.example")["ready"])
