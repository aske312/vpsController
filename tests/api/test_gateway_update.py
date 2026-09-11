"""Release compatibility checks. Shell tests only use temporary application trees."""
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import ROOT, load_module

gateway = load_module("gateway_config", "api/gateway_config.py")


def function(name):
    source = (ROOT / "scripts/vps-control.sh").read_text(encoding="utf-8")
    start = source.index(name + "() {")
    end = re.search(r"\n[a-zA-Z_][a-zA-Z_0-9]*\(\) \{", source[start + 1:])
    return source[start:start + 1 + end.start()] if end else source[start:]


class GatewayTests(unittest.TestCase):
    def test_modes_render_without_unknown_placeholders(self):
        template = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        for mode, site in [("vpn", "http://localhost:8080"), ("external", "panel.example")]:
            text = gateway.render(template, mode, 8080, {"PUBLIC_DOMAIN": "panel.example", "PUBLIC_IP_ENDPOINT": "203.0.113.10"})
            self.assertIn(site + " {", text)
            self.assertNotIn("{PANEL_ACCESS_GUARD}", text)
            self.assertNotIn("{$SITE_ADDRESS}", text)
            self.assertNotIn("{WG_PANEL_ADDRESS}", text)
            self.assertNotIn("{PUBLIC_PANEL_ADDRESS}", text)
            self.assertIn("http://203.0.113.10", text)
            self.assertIn("respond @outsidePanel 403", text)

    def test_invalid_candidate_fails_without_modifying_active_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "active.caddy"
            active.write_text("original")
            (root / "Caddyfile").write_text("invalid_directive")
            with patch.object(gateway.cdn_security, "SNIPPET", root / "snippets/managed.caddy"), patch.object(gateway.cdn_security, "read_routes", return_value=[]), patch.object(gateway.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "caddy")):
                with self.assertRaises(subprocess.CalledProcessError):
                    gateway.validate_candidate(root, "vpn", 8080, {})
            self.assertEqual(active.read_text(), "original")


@unittest.skipIf(os.name == "nt", "Requires deployment Bash")
class ShellGatewayTests(unittest.TestCase):
    def test_release_build_rejects_missing_ca_before_dependency_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            script = root / "scripts/build-release.sh"
            script.write_text((ROOT / "scripts/build-release.sh").read_text())
            result = subprocess.run(["bash", str(script), str(root / "output/release.tar.gz")], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing public Cloudflare origin-pull CA in release", result.stderr)
            self.assertFalse((root / "output/release.tar.gz").exists())

    @unittest.skipUnless(os.getenv("PRIVACY_CADDY_BIN"), "Requires Caddy")
    def test_aop_candidate_loads_certificate_from_release(self):
        routes = [{"domain": "cdn.example", "path": "/test", "port": 12345}]
        with tempfile.TemporaryDirectory() as temp, patch.object(gateway.cdn_security, "SNIPPET", Path(temp) / "transports.caddy"), patch.object(gateway.cdn_security, "read_routes", return_value=routes), patch.object(gateway.cdn_security, "settings", return_value={"authenticated_origin_pulls": True}):
            try:
                gateway.validate_candidate(ROOT, "vpn", 18080, {}, os.environ["PRIVACY_CADDY_BIN"])
            except subprocess.CalledProcessError as error:
                self.fail(error.stderr.decode())

    def test_failed_renderer_propagates_even_in_conditional(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "api").mkdir()
            (root / "api/gateway_config.py").touch()
            script = f'''set -eu
INSTALL_DIR={shlex.quote(temp)}
ACCESS_MODE=vpn
HTTP_PORT=8080
CADDY_CONFIG={shlex.quote(temp)}/output
python3() {{ case "$1" in *gateway_config.py) return 19;; *) return 0;; esac; }}
{function('write_caddy_config')}
if write_caddy_config; then exit 90; else exit $?; fi
'''
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 19, result.stderr)

    def test_rollback_restores_exact_config_and_removes_new_snippets(self):
        for had_snippets in (True, False):
            with self.subTest(had_snippets=had_snippets), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                backup = root / "backup"
                snippets = root / "snippets"
                backup.mkdir()
                snippets.mkdir()
                (backup / "Caddyfile").write_text("previous config\n")
                (root / "active").write_text("broken config")
                (snippets / "new.caddy").write_text("new directive")
                if had_snippets:
                    (backup / "snippets").mkdir()
                    (backup / "snippets/original.caddy").write_text("previous transport")
                script = f'''set -eu
UPDATE_GATEWAY_BACKUP_DIR={shlex.quote(str(backup))}
CADDY_SNIPPET_DIR={shlex.quote(str(snippets))}
CADDY_CONFIG={shlex.quote(str(root / 'active'))}
{function('restore_update_gateway')}
restore_update_gateway
'''
                subprocess.run(["bash", "-c", script], check=True, capture_output=True)
                self.assertEqual((root / "active").read_text(), "previous config\n")
                self.assertFalse((snippets / "new.caddy").exists())
                self.assertEqual((snippets / "original.caddy").exists(), had_snippets)

    def test_preflight_failure_never_stops_services_or_swaps_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "vps-control-release"
            installed = root / "installed"
            for name in ["node_modules/.bin/vinext", "dist/server/index.js", "api/main.py", "api/requirements.txt", "api/gateway_config.py", ".prebuilt-release"]:
                path = payload / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            (payload / "node_modules/.bin/vinext").chmod(0o755)
            (installed / "venv/bin").mkdir(parents=True)
            (installed / "venv/bin/python").write_text("#!/bin/sh\nexit 0\n")
            (installed / "venv/bin/python").chmod(0o755)
            (installed / "venv/.requirements.sha256").write_text("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n")
            (installed / "original").write_text("working release")
            subprocess.run(["bash", "-c", "sha256sum .prebuilt-release > release.sha256"], cwd=payload, check=True)
            archive = root / "release.tar.gz"
            subprocess.run(["tar", "-czf", str(archive), "vps-control-release"], cwd=root, check=True)
            script = f'''set -eu
INSTALL_DIR={shlex.quote(str(installed))}
ACCESS_MODE=vpn
HTTP_PORT=8080
mktemp() {{ command mktemp -d {shlex.quote(temp)}/stage.XXXXXX; }}
python3() {{ return 17; }}
systemctl() {{ touch {shlex.quote(temp)}/services-touched; return 91; }}
die() {{ exit 43; }}
{function('install_prebuilt_release')}
install_prebuilt_release install-release {shlex.quote(str(archive))}
'''
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 43, result.stderr)
            self.assertEqual((installed / "original").read_text(), "working release")
            self.assertFalse((root / "services-touched").exists())
            self.assertEqual(list(root.glob("stage.*")), [])

    @unittest.skipUnless(os.getenv("PRIVACY_CADDY_BIN"), "Requires Caddy")
    def test_installed_legacy_renderer_accepts_new_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "snippets").mkdir()
            template = (ROOT / "Caddyfile").read_text(encoding="utf-8").replace("/etc/caddy/vps-control.d/*.caddy", str(root / "snippets/*.caddy"))
            (root / "Caddyfile").write_text(template)
            legacy = (ROOT / "tests/fixtures/legacy-gateway-renderer.sh").read_text()
            for mode in ("vpn", "external"):
                script = f'''set -eu
INSTALL_DIR={shlex.quote(temp)}
CADDY_CONFIG={shlex.quote(temp)}/rendered
CADDY_SNIPPET_DIR={shlex.quote(temp)}/snippets
ACCESS_MODE={mode}
HTTP_PORT=18080
env_value() {{ case "$1" in PUBLIC_DOMAIN) printf 'panel.example';; esac; }}
{legacy}
write_caddy_config
'''
                subprocess.run(["bash", "-c", script], check=True, capture_output=True)
                result = subprocess.run([os.environ["PRIVACY_CADDY_BIN"], "adapt", "--config", str(root / "rendered"), "--adapter", "caddyfile"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.getenv("PRIVACY_CADDY_BIN"), "Requires Caddy")
    def test_current_candidate_validates_with_real_caddy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(gateway.cdn_security, "SNIPPET", Path(temp) / "transports.caddy"), patch.object(gateway.cdn_security, "read_routes", return_value=[]):
            try:
                gateway.validate_candidate(ROOT, "vpn", 18080, {}, os.environ["PRIVACY_CADDY_BIN"])
            except subprocess.CalledProcessError as error:
                self.fail(error.stderr.decode())
