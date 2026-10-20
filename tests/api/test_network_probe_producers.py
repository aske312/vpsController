import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.api.support import api, manager
import network_probe as probe
import network_routes


class ProbeProducerTests(unittest.TestCase):
    def test_first_hop_cdn_probe_requires_cf_gateway_without_relay_source_acl(self):
        settings = self.profiles[0]['connections'][0]['settings']
        settings.update(relay_cdn=True, relay_host_id='b'*32, relay_cdn_mode='before_cdn', relay_address='relay.example')
        plan = self.prepare()
        self.assertEqual(plan.relay_target_domain,'edge.example')
        self.assertEqual(plan.relay_client_address,'relay.example')
        self.assertEqual(plan.relay_host_id,'b'*32)
        self.descriptors['device-node']['relay_origins'] = ['85.209.155.229']
        with self.assertRaises(probe.ProbeUnavailable):
            self.prepare()

    def test_trojan_relay_probe_rewrites_socket_and_preserves_tls_and_password(self):
        self.binding['kind'] = self.refs[0]['kind'] = 'tls_relay'
        self.credential.update(xray_protocol='trojan', password='temporary-test-password', tls_enabled=True,
                               tls_domain='edge.example', tls_port=10443, tls_path='/live')
        self.profiles[0]['connections'][0]['settings']['route_mode'] = 'tls'
        inbound = self.config['inbounds'][0]
        inbound.update(protocol='trojan', tag='mihomo-vless-tls-device-node', settings={'clients':[{'password':'temporary-test-password'}]})
        self.descriptors['tls-device-node'] = self.descriptors.pop('device-node')
        plan = self.prepare()
        host = {'id':'b'*32, 'certificate_sha256':'a'*64}
        route = {'id':'pro-'+'c'*32, 'target_ip':api.PUBLIC_IPV4, 'transport':'tcp', 'target_port':443, 'listen_port':20000}
        control = Mock()
        control.client.return_value.host = '8.8.4.4'
        with patch.object(api, 'read_route_registry', return_value={'bindings':[self.binding]}), patch.object(api, 'prepare_network_probe', return_value=(plan,{})), patch.object(api, 'relay_control', return_value=control):
            relayed = api.prepare_relay_probe(route, host, plan.consumer_id)
        self.assertEqual(relayed.client['settings']['servers'][0], {'address':'8.8.4.4', 'port':20000, 'password':'temporary-test-password'})
        self.assertEqual(relayed.client['streamSettings'], plan.client['streamSettings'])
        self.assertNotEqual(relayed.fingerprint, plan.fingerprint)

    def test_cdn_relay_probe_requires_exact_gateway_acl_and_keeps_public_cdn_socket(self):
        for transport, key in [('websocket', 'wsSettings'), ('xhttp', 'xhttpSettings'), ('grpc', 'grpcSettings'), ('httpupgrade', 'httpupgradeSettings')]:
            with self.subTest(transport=transport):
                self.setUp()
                opts = {'serviceName': 'live'} if transport == 'grpc' else {'path': '/live'}
                if transport == 'xhttp': opts['mode'] = 'packet-up'
                self.config['inbounds'][0]['streamSettings'] = {'network': transport, key: opts}
                self.descriptors['device-node']['transport'] = transport
                self.profiles[0]['connections'][0]['settings'].update(relay_cdn=True, relay_host_id='b'*32, cdn_relay_ips='85.209.155.229')
                with self.assertRaises(probe.ProbeUnavailable):
                    self.prepare()
                self.descriptors['device-node']['relay_origins'] = ['85.209.155.229']
                plan = self.prepare()
                self.assertEqual((plan.address, plan.port, plan.relay_host_id), ('edge.example', 443, 'b'*32))
                self.assertEqual(plan.client['streamSettings'][key], opts)
                host = {'id':'b'*32, 'certificate_sha256':'a'*64}
                route = {'id':'pro-'+'c'*32, 'target_ip':api.PUBLIC_IPV4, 'transport':'tcp', 'target_port':443, 'listen_port':20000}
                with patch.object(api, 'read_route_registry', return_value={'bindings':[self.binding]}), patch.object(api, 'prepare_network_probe', return_value=(plan,{})):
                    # The domain plan proves the CDN chain; it must not falsely certify
                    # an exact agent ingress rule whose CF mapping is managed externally.
                    with self.assertRaises(probe.ProbeUnavailable):
                        api.prepare_relay_probe(route, host, plan.consumer_id)
                    with self.assertRaises(probe.ProbeUnavailable):
                        api.prepare_relay_probe(route, {**host, 'id':'d'*32}, plan.consumer_id)
                self.descriptors['device-node']['relay_origins'] = ['8.8.4.4']
                with self.assertRaises(probe.ProbeUnavailable):
                    self.prepare()

    def setUp(self):
        self.binding = {"id": "binding", "state": "active", "kind": "cdn", "address": "edge.example", "desired_revision": 1}
        self.credential = {"uuid": "private-uuid", "cdn_enabled": True, "cdn_domain": "edge.example", "cdn_port": 10443, "cdn_path": "/live"}
        self.profiles = [{"id": "device", "connections": [{"id": "node", "component": "transport-reality", "settings": {"route_mode": "cdn"}, "credential": self.credential}]}]
        self.refs = [{"id": "mihomo:device:node", "owner": "mihomo", "kind": "cdn", "address": "edge.example", "reference": "snapshot"}]
        self.config = {"inbounds": [{"tag": "mihomo-vless-cdn-device-node", "protocol": "vless", "port": 10443, "settings": {"clients": [{"id": "private-uuid"}], "decryption": "none"},
                                    "streamSettings": {"network": "websocket", "wsSettings": {"path": "/live"}}}]}
        self.descriptors = {"device-node": {"domain": "edge.example", "path": "/live", "port": 10443, "transport": "websocket"}}

    def prepare(self):
        return probe.prepare_mihomo_vless(self.binding, self.refs, self.profiles, self.config, self.descriptors)

    def test_mihomo_uses_exact_live_credential_and_owned_gateway(self):
        original = copy.deepcopy((self.binding, self.refs, self.profiles, self.config, self.descriptors))
        plan = self.prepare()
        self.assertEqual(plan.producer, "mihomo_vless")
        self.assertEqual(plan.client["settings"]["vnext"][0]["port"], 443)
        self.assertEqual(plan.client["streamSettings"]["wsSettings"]["path"], "/live")
        self.assertFalse(plan.client["streamSettings"]["tlsSettings"]["allowInsecure"])
        self.assertNotIn("private-uuid", json.dumps(plan.summary()) + repr(plan))
        self.assertEqual(original, (self.binding, self.refs, self.profiles, self.config, self.descriptors))

    def test_mihomo_cannot_borrow_listener_or_use_stale_snapshot(self):
        for change in ("uuid", "tag", "port", "path", "domain", "route", "retired", "encryption", "duplicate"):
            with self.subTest(change=change):
                self.setUp()
                inbound = self.config["inbounds"][0]
                if change == "uuid": inbound["settings"]["clients"] = []
                elif change == "tag": inbound["tag"] = "another-node"
                elif change == "port": self.descriptors["device-node"]["port"] = 10444
                elif change == "path": self.credential["cdn_path"] = "/old"
                elif change == "domain": self.credential["cdn_domain"] = "other.example"
                elif change == "route": self.profiles[0]["connections"][0]["settings"]["route_mode"] = "direct"
                elif change == "retired": self.binding["state"] = "retired"
                elif change == "encryption": self.credential["encryption"] = "private-encryption"
                elif change == "duplicate": self.config["inbounds"].append(copy.deepcopy(inbound))
                with self.assertRaises(probe.ProbeUnavailable): self.prepare()

    def test_mihomo_supports_live_httpupgrade_and_tls_without_cdn_borrowing(self):
        self.config["inbounds"][0]["streamSettings"] = {"network": "httpupgrade", "httpupgradeSettings": {"path": "/live"}}
        self.descriptors["device-node"]["transport"] = "httpupgrade"
        self.assertEqual(self.prepare().transport, "httpupgrade")
        self.binding["kind"] = self.refs[0]["kind"] = "tls_relay"
        self.profiles[0]["connections"][0]["settings"]["route_mode"] = "tls"
        self.credential.update(tls_enabled=True, tls_domain="edge.example", tls_path="/live", tls_port=10443)
        with self.assertRaises(probe.ProbeUnavailable): self.prepare()
        self.config["inbounds"][0]["tag"] = "mihomo-vless-tls-device-node"
        self.descriptors["tls-device-node"] = self.descriptors.pop("device-node")
        self.assertEqual(self.prepare().producer, "mihomo_vless")

    def test_scoped_sources_reject_traversal_and_unknown_producer(self):
        with self.assertRaises(probe.ProbeUnavailable):
            probe.mihomo_source_paths(Path("/config"), Path("/profiles"), "mihomo:../secret:node", "cdn")
        with self.assertRaises(probe.ProbeUnavailable):
            probe.scoped_source_paths("unknown", "cdn", "", direct_paths={}, mihomo_root=Path("/config"), data_root=Path("/data"), quic_roots={})

    def quic_fixture(self, protocol="tuic"):
        binding = {**self.binding, "kind": "udp_relay"}
        refs = [{"id": "direct:phone", "owner": "direct", "kind": "udp_relay", "address": "edge.example", "reference": "snapshot"}]
        clients = [{"id": "phone", "protocol": protocol, "public_key": "private-uuid", "channel_mode": "udp_relay", "route_endpoint": "edge.example", "port": 18443}]
        if protocol == "tuic":
            config = {"inbounds": [{"type": "tuic", "tag": "tuic-in", "listen_port": 18443, "users": [{"name": "phone", "uuid": "private-uuid", "password": "private-password"}], "tls": {"enabled": True}}]}
        else:
            config = {"listen": ":18443", "tls": {"cert": "server.crt", "key": "server.key"}, "auth": {"type": "http"}, "obfs": {"type": "salamander", "salamander": {"password": "private-obfs"}}}
        return binding, refs, clients, protocol, config, {"port": 18443}, "PUBLIC CERT", "probe.local", {"phone": "private-password"}

    def test_quic_uses_authentication_and_strict_tls_without_secret_summary(self):
        for protocol in ("tuic", "hysteria2"):
            with self.subTest(protocol=protocol):
                fixture = self.quic_fixture(protocol)
                original = copy.deepcopy(fixture)
                plan = probe.prepare_direct_quic(*fixture)
                self.assertEqual(plan.engine, "singbox")
                self.assertFalse(plan.client["tls"]["insecure"])
                self.assertEqual(plan.client["server"], "edge.example")
                summary = json.dumps(plan.summary()) + repr(plan)
                for secret in ("private-uuid", "private-password", "private-obfs"):
                    self.assertNotIn(secret, summary)
                self.assertEqual(original, fixture)

    def test_quic_rejects_uncertain_reference_revoked_user_wrong_port_and_endpoint(self):
        for protocol in ("tuic", "hysteria2"):
            for change in ("reference", "endpoint", "port", "user", "owner"):
                with self.subTest(protocol=protocol, change=change):
                    fixture = list(self.quic_fixture(protocol))
                    if change == "reference": fixture[1][0]["reference"] = "legacy-uncertain"
                    if change == "owner": fixture[1][0]["owner"] = "mihomo"
                    if change == "endpoint": fixture[2][0]["route_endpoint"] = "other.example"
                    if change == "port": fixture[2][0]["port"] = 18444
                    if change == "user":
                        if protocol == "tuic": fixture[4]["inbounds"][0]["users"] = []
                        else: fixture[-1] = {}
                    with self.assertRaises(probe.ProbeUnavailable): probe.prepare_direct_quic(*fixture)

    def test_quic_semantic_evidence_keeps_unrelated_user_but_invalidates_password_rotation(self):
        for protocol in ("tuic", "hysteria2"):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = list(self.quic_fixture(protocol))
                plan = probe.prepare_direct_quic(*fixture)
                paths = {"config": root / "config", "settings": root / "settings", "certificate": root / "cert", "clients": root / "clients"}
                if protocol == "hysteria2": paths["users"] = root / "users"
                def save():
                    # JSON is also valid YAML for the native Hysteria2 parser.
                    paths["config"].write_text(json.dumps(fixture[4]))
                    paths["settings"].write_text(json.dumps(fixture[5]))
                    paths["certificate"].write_text(fixture[6])
                    paths["clients"].write_text(json.dumps(fixture[2]))
                    if protocol == "hysteria2": paths["users"].write_text(json.dumps(fixture[-1]))
                save()
                check = {"proof": {"method": f"direct_{protocol}_nonce_v1", **plan.evidence(probe.evidence_key(root, create=True))}}
                self.assertTrue(probe.evidence_current(check, paths, binding=fixture[0], key_root=root))
                if protocol == "tuic": fixture[4]["inbounds"][0]["users"].append({"name": "other", "uuid": "other-uuid", "password": "other-password"})
                else: fixture[-1]["other"] = "other-password"
                save()
                self.assertTrue(probe.evidence_current(check, paths, binding=fixture[0], key_root=root))
                if protocol == "tuic": fixture[4]["inbounds"][0]["users"][0]["password"] = "rotated-password"
                else: fixture[-1][fixture[2][0]["id"]] = "rotated-password"
                save()
                self.assertFalse(probe.evidence_current(check, paths, binding=fixture[0], key_root=root))

    def test_udp_witness_requires_nonce_and_origin_source(self):
        with probe.OriginWitness("127.0.0.1", 1, "udp") as witness:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.bind(("127.0.0.2", 0))
                client.sendto(witness.challenge, ("127.0.0.1", witness.port))
                client.settimeout(0.05)
                with self.assertRaises(socket.timeout): client.recv(512)
            self.assertFalse(witness.seen.is_set())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.bind(("127.0.0.1", 0))
                client.settimeout(0.5)
                client.sendto(b"other-connection", ("127.0.0.1", witness.port))
                client.sendto(witness.challenge, ("127.0.0.1", witness.port))
                self.assertEqual(client.recv(512), witness.response)
            self.assertTrue(witness.seen.is_set())

    def test_api_selects_mihomo_when_direct_vless_is_missing_and_invalidates_its_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_root = root / "config"
            sources = probe.mihomo_source_paths(config_root, root / "mihomo" / "profiles.json", "mihomo:device:node", "cdn")
            for path in sources.values(): path.parent.mkdir(parents=True, exist_ok=True)
            sources["config"].write_text(json.dumps(self.config))
            sources["profiles"].write_text(json.dumps(self.profiles))
            sources["gateway_route"].write_text(json.dumps(self.descriptors["device-node"]))
            path = root / "network.json"
            with patch.object(api, "DATA_DIR", root), patch.object(api, "NETWORK_ENDPOINTS_FILE", path), patch.object(api, "VLESS_CONFIG", root / "missing.json"), patch.object(api, "MIHOMO_VLESS_CDN_ROUTES", config_root / "reality" / "caddy-routes"), patch.object(api, "network_consumers", return_value=self.refs), patch.object(manager, "CONFIG_ROOT", config_root), patch.object(manager, "NETWORK_ENDPOINTS_FILE", path):
                api.write_network_endpoint_settings({"cdn_domains": ["edge.example"]})
                binding = network_routes.active_binding(api.read_route_registry(), "cdn", "edge.example")
                plan, snapshot = api.prepare_network_probe(binding)
                self.assertEqual(plan.producer, "mihomo_vless")
                proof = {"method": "mihomo_vless_nonce_v1", "producer": plan.producer, "consumer_id": plan.consumer_id, "source_revision": snapshot}
                registry = api.read_route_registry()
                network_routes.record_check(registry, "cdn", "edge.example", {"ready": True, "verification": "end_to_end", "check_id": "nonce", "checked_at": "now", "binding_id": binding["id"], "checked_revision": 1, "proof": proof})
                path.write_text(json.dumps({"route_registry": registry}))
                observation = {"domain": "edge.example", "resolved": [], "matches_origin": False, "route": "unresolved"}
                with patch.object(api, "network_domain_probe", return_value=observation):
                    self.assertTrue(api.network_endpoint_check("cdn", "edge.example")["ready"])
                    self.assertTrue(manager.mihomo_route_endpoint_ready("cdn", "edge.example"))
                    sources["gateway_route"].write_text("{}")
                    self.assertFalse(api.network_endpoint_check("cdn", "edge.example")["ready"])
                    self.assertFalse(manager.mihomo_route_endpoint_ready("cdn", "edge.example"))

    def test_api_selects_quic_and_rejects_credential_change_after_exchange(self):
        for change in (False, True):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                tuic = root / "tuic"
                tuic.mkdir()
                fixture = self.quic_fixture()
                (tuic / "config.json").write_text(json.dumps(fixture[4]))
                (tuic / "settings.json").write_text(json.dumps(fixture[5]))
                (tuic / "server.crt").write_text("PUBLIC CERT")
                clients = root / "clients.json"
                clients.write_text(json.dumps(fixture[2]))
                with patch.object(api, "DATA_DIR", root), patch.object(api, "NETWORK_ENDPOINTS_FILE", root / "network.json"), patch.object(api, "CLIENTS_FILE", clients), patch.object(api, "VLESS_ENV", root / "vless" / "reality.env"), patch.object(api, "TUIC_DIR", tuic), patch.object(api, "HYSTERIA2_DIR", root / "missing"), patch.object(api, "certificate_server_name", return_value="probe.local"), patch.object(manager, "NETWORK_ENDPOINTS_FILE", root / "network.json"), patch.object(manager, "DIRECT_VLESS_ENV", root / "vless" / "reality.env"):
                    api.write_network_endpoint_settings({"udp_relay_domains": ["edge.example"]})
                    binding = network_routes.active_binding(api.read_route_registry(), "udp_relay", "edge.example")
                    plan, _ = api.prepare_network_probe(binding)
                    self.assertEqual(plan.producer, "direct_tuic")
                    def exchange(plan):
                        if change:
                            config = json.loads((tuic / "config.json").read_text())
                            config["inbounds"][0]["users"][0]["password"] = "rotated"
                            (tuic / "config.json").write_text(json.dumps(config))
                        return {"state": "verified", "method": "direct_tuic_nonce_v1", "producer": "direct_tuic", "consumer_id": plan.consumer_id, "fingerprint": plan.fingerprint}
                    observation = {"domain": "edge.example", "resolved": ["203.0.113.1"], "matches_origin": False, "route": "proxy_or_cdn"}
                    with patch.object(api, "network_domain_probe", return_value=observation), patch.object(api, "execute_network_probe", side_effect=exchange):
                        result = api.check_network_endpoint(api.NetworkEndpointCheck(kind="udp_relay", domain="edge.example"))
                        self.assertEqual(result["ready"], not change)
                        self.assertEqual(manager.mihomo_route_endpoint_ready("udp", "edge.example"), not change)
                        self.assertEqual(api.network_endpoint_check("udp_relay", "edge.example")["ready"], not change)
                        output = json.dumps(result) + api.NETWORK_ENDPOINTS_FILE.read_text(encoding="utf-8")
                        for secret in ("private-uuid", "private-password", "rotated"):
                            self.assertNotIn(secret, output)
