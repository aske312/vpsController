import unittest

from tests.api.support import api


class VlessClientRouteTests(unittest.TestCase):
    def inbound(self, transport="xhttp", path="/active", mode="auto", reality=False):
        stream = {"network": transport}
        if transport == "xhttp":
            stream["xhttpSettings"] = {"path": path, "mode": mode}
        elif transport == "grpc":
            stream["grpcSettings"] = {"serviceName": path.lstrip("/")}
        if reality:
            stream["realitySettings"] = {"serverNames": ["active.example"]}
        return {"streamSettings": stream}

    def test_direct_export_must_match_the_active_listener(self):
        settings = api.ClientConnectionSettings(
            transport="xhttp", transport_path="/active", xhttp_mode="auto", sni="active.example"
        )
        profile = api.validate_vless_client_route(settings, "direct", self.inbound(reality=True), {"TARGET": "active.example:443"})
        self.assertEqual(profile, {"transport": "xhttp", "path": "/active", "xhttp_mode": "auto"})
        for patch_value in (
            {"transport": "grpc"},
            {"transport_path": "/wrong"},
            {"xhttp_mode": "stream-one"},
            {"sni": "wrong.example"},
        ):
            with self.subTest(patch_value=patch_value), self.assertRaises(api.HTTPException) as raised:
                api.validate_vless_client_route(settings.model_copy(update=patch_value), "direct", self.inbound(reality=True), {"TARGET": "active.example:443"})
            self.assertEqual(raised.exception.status_code, 422)

    def test_tls_and_cdn_transport_are_listener_owned(self):
        tls = api.ClientConnectionSettings(tls_transport="grpc")
        self.assertEqual(api.validate_vless_client_route(tls, "tls", self.inbound("grpc", "/secure"), {})["path"], "/secure")
        with self.assertRaises(api.HTTPException):
            api.validate_vless_client_route(api.ClientConnectionSettings(cdn_transport="websocket"), "cdn", self.inbound("xhttp", "/cdn", "packet-up"), {})

    def test_tcp_listener_is_exposed_as_supported_raw_transport(self):
        profile = api.vless_stream_profile(self.inbound("tcp"))
        self.assertEqual(profile["transport"], "raw")
        self.assertEqual(profile["path"], "")

    def test_export_uses_listener_path_instead_of_stale_environment_value(self):
        query = api.vless_cdn_client_query(
            {"CDN_DOMAIN": "cdn.example", "CDN_PATH": "/stale"},
            transport="xhttp", path="/active",
        )
        self.assertEqual(query["path"], "/active")


if __name__ == "__main__":
    unittest.main()
