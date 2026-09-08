"""Gateway policy and rollback tests; optional Caddy checks use isolated loopback ports."""
import http.client
import http.server
import json
import os
import socket
import ssl
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_privacy import api, manager, ROOT
from test_tunnel_privacy import free_port, wait_port

security = api.cdn_security


class CdnSecurityTests(unittest.TestCase):
    def test_missing_packaged_ca_is_reported_before_starting_mutation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(security, "RESOURCES", Path(temp)), patch.object(api.subprocess, "run") as run:
            with self.assertRaises(api.HTTPException) as error:
                api.update_cdn_security(api.CdnSecuritySettings(authenticated_origin_pulls=True))
            self.assertEqual(error.exception.status_code, 409)
            self.assertIn("отсутствует публичный сертификат", error.exception.detail)
            run.assert_not_called()

    def test_cdn_is_restricted_but_direct_tls_is_preserved(self):
        routes = [{"domain": "cdn.example", "path": "/cdn", "port": 12345}, {"domain": "direct.example", "path": "/tls", "port": 12346, "cloudflare": False}]
        text = security.render_routes(routes, {"authenticated_origin_pulls": True}, protected=True)
        cdn, direct = text.split("direct.example {")
        self.assertIn("not remote_ip", cdn)
        self.assertIn("require_and_verify", cdn)
        self.assertNotIn("client_ip", text)
        self.assertNotIn("require_and_verify", direct)
        self.assertNotIn("not remote_ip", direct)
        self.assertIn("reverse_proxy 127.0.0.1:12346", direct)

    def test_shared_hostname_cannot_accidentally_break_direct_tls(self):
        routes = [{"domain": "example.com", "path": "/cdn", "port": 12345}, {"domain": "example.com", "path": "/tls", "port": 12346, "cloudflare": False}]
        with self.assertRaises(ValueError):
            security.render_routes(routes, {"authenticated_origin_pulls": True}, protected=True)
        text = security.render_routes(routes, {"authenticated_origin_pulls": False}, protected=True)
        self.assertEqual(text.count("not remote_ip"), 1)

    def test_legacy_route_classification_preserves_tls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("profile-channel", "tls-profile-channel"):
                (root / f"{name}.json").write_text(json.dumps({"domain": "example.com", "path": "/" + name, "port": 12345}))
            with patch.object(security, "ROUTES", root), patch.object(security, "DIRECT_ENV", root / "missing"):
                routes = security.read_routes()
            self.assertEqual([item["cloudflare"] for item in routes], [True, False])

    def test_failed_cf_probe_restores_files_and_reloads_previous_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snippet, state = root / "snippet", root / "state.json"
            snippet.write_text("previous config")
            state.write_text('{"authenticated_origin_pulls": false}')
            routes = [{"domain": "example.com", "path": "/channel", "port": 12345}]
            with patch.object(security, "SNIPPET", snippet), patch.object(security, "STATE", state), patch.object(security, "read_routes", return_value=routes), patch.object(security, "reload_caddy") as reload, patch.object(security.urllib.request, "urlopen", side_effect=OSError("probe failed")):
                with self.assertRaises(RuntimeError):
                    security.configure_aop(True)
            self.assertEqual(snippet.read_text(), "previous config")
            self.assertFalse(json.loads(state.read_text())["authenticated_origin_pulls"])
            self.assertEqual(reload.call_count, 2)

    def test_protected_firewall_adds_cf_before_removing_blanket_rule(self):
        with patch.object(security, "read_env", return_value={"ACCESS_MODE": "vpn"}), patch.object(security, "read_routes", return_value=[{"cloudflare": True}]), patch.object(security.subprocess, "run") as run:
            security.configure_firewall()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][1:3], ["allow", "from"])
        self.assertIn(["ufw", "--force", "delete", "allow", "443/tcp"], commands)
        self.assertFalse(any(command[1:3] == ["allow", "443/tcp"] for command in commands))
        self.assertTrue(any(":" in command[3] for command in commands if command[1:3] == ["allow", "from"]))

    def test_resource_networks_are_ipv4_and_ipv6(self):
        ranges = security.networks()
        self.assertGreater(len(ranges), 15)
        self.assertTrue(any(":" in value for value in ranges))


@unittest.skipUnless(os.getenv("PRIVACY_CADDY_BIN"), "Set Caddy binary for real gateway checks")
class RealCaddyTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "The deployment template runs under bash")
    def test_protected_shell_template_is_valid_without_touching_system_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "api").mkdir()
            shutil.copy2(ROOT / "api/cdn_security.py", root / "api/cdn_security.py")
            shutil.copytree(ROOT / "api/resources", root / "api/resources")
            (root / "snippets").mkdir()
            template = (ROOT / "Caddyfile").read_text().replace("/etc/caddy/vps-control.d/*.caddy", str(root / "snippets/*.caddy"))
            (root / "Caddyfile").write_text(template)
            source = (ROOT / "scripts/vps-control.sh").read_text()
            function = source[source.index("write_caddy_config() {"):source.index("\nenv_value() {")]
            function = function.replace('python3 "${INSTALL_DIR}/api/cdn_security.py" rebuild', ': # no system mutation in template test')
            script = f'''set -eu
INSTALL_DIR='{root}'
CADDY_CONFIG='{root}/rendered.Caddyfile'
CADDY_SNIPPET_DIR='{root}/snippets'
ACCESS_MODE=vpn
HTTP_PORT=18080
env_value() {{
 case "$1" in
 PUBLIC_DOMAIN) printf 'subscriptions.example' ;;
 WG_SUBNET) printf '10.72.0.0/24' ;;
 AWG_SUBNET) printf '10.73.0.0/24' ;;
 esac
}}
{function}
write_caddy_config
'''
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = (root / "rendered.Caddyfile").read_text()
            self.assertNotIn("{PANEL_ACCESS_GUARD}", rendered)
            self.assertIn("respond @outsidePanel 403", rendered)
            result = subprocess.run([os.environ["PRIVACY_CADDY_BIN"], "adapt", "--config", str(root / "rendered.Caddyfile"), "--adapter", "caddyfile"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(json.dumps(json.loads(result.stdout)).count('"status_code": 403'), 3)

    @unittest.skipUnless(os.getenv("PRIVACY_OPENSSL_BIN"), "Set OpenSSL for isolated mTLS handshake test")
    def test_mtls_accepts_trusted_certificate_and_rejects_missing_or_wrong(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("trusted", "wrong"):
                subprocess.run([os.environ["PRIVACY_OPENSSL_BIN"], "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", str(root / f"{name}.key"), "-out", str(root / f"{name}.crt")], check=True, capture_output=True)
            (root / "cloudflare-origin-pull-ca.pem").write_bytes((root / "trusted.crt").read_bytes())
            routes = [{"domain": "test.example", "path": "/cdn", "port": 12345}]
            port = free_port()
            with patch.object(security, "RESOURCES", root), patch.object(security, "networks", return_value=["127.0.0.0/8", "::1/128"]):
                config = security.render_routes(routes, {"authenticated_origin_pulls": True}, protected=True, probe="handshake")
            config = config.replace("test.example {", f"https://localhost:{port} {{").replace("    tls {", f'    tls "{root / "trusted.crt"}" "{root / "trusted.key"}" {{')
            path = root / "Caddyfile"
            path.write_text("{\n admin off\n auto_https off\n}\n" + config)
            process = subprocess.Popen([os.environ["PRIVACY_CADDY_BIN"], "run", "--config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_port(port, process)
                for name, expected in (("trusted", True), (None, False), ("wrong", False)):
                    context = ssl.create_default_context(cafile=str(root / "trusted.crt"))
                    if name:
                        context.load_cert_chain(str(root / f"{name}.crt"), str(root / f"{name}.key"))
                    connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
                    try:
                        try:
                            connection.request("GET", "/__cf_check_handshake")
                            response = connection.getresponse()
                            passed = response.status == 200 and response.read() == b"handshake"
                        except (OSError, http.client.HTTPException) as exc:
                            if expected:
                                self.fail(f"Trusted certificate rejected: {exc}")
                            passed = False
                        self.assertEqual(passed, expected, name)
                    finally:
                        connection.close()
            finally:
                process.terminate()
                process.wait(timeout=10)

    def test_actual_source_filter_rejects_spoofed_forwarded_headers(self):
        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"origin reached")
            def log_message(self, *_):
                pass
        origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                port = free_port()
                routes = [{"domain": "test.example", "path": "/cdn", "port": origin.server_port}, {"domain": "test.example", "path": "/direct", "port": origin.server_port, "cloudflare": False}]
                with patch.object(security, "networks", return_value=["127.0.0.2/32"]):
                    config = security.render_routes(routes, {"authenticated_origin_pulls": False}, protected=True)
                config = "{\n admin off\n auto_https off\n}\n" + config.replace("test.example {", f"http://127.0.0.1:{port} {{")
                path = Path(temp) / "Caddyfile"
                path.write_text(config)
                process = subprocess.Popen([os.environ["PRIVACY_CADDY_BIN"], "run", "--config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    wait_port(port, process)
                    for source, target, expected in (("127.0.0.1", "/cdn", 403), ("127.0.0.2", "/cdn", 200), ("127.0.0.1", "/direct", 200), ("127.0.0.1", "/unknown", 404)):
                        connection = http.client.HTTPConnection("127.0.0.1", port, source_address=(source, 0), timeout=5)
                        try:
                            connection.request("GET", target, headers={"X-Forwarded-For": "127.0.0.2", "CF-Connecting-IP": "127.0.0.2"})
                            response = connection.getresponse()
                            self.assertEqual(response.status, expected, (source, target))
                            response.read()
                        finally:
                            connection.close()
                finally:
                    process.terminate()
                    process.wait(timeout=10)
        finally:
            origin.shutdown()
            origin.server_close()

    def test_caddy_accepts_real_cf_trust_pool(self):
        routes = [{"domain": "test.example", "path": "/cdn", "port": 12345}]
        text = security.render_routes(routes, {"authenticated_origin_pulls": True}, protected=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Caddyfile"
            path.write_text(text)
            result = subprocess.run([os.environ["PRIVACY_CADDY_BIN"], "adapt", "--config", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(result.stdout)
            self.assertIn("require_and_verify", json.dumps(config))
