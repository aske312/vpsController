import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.api.support import api, ROOT
from component_state import component_state, health_state, image_files, installation_state


class ComponentStateTests(unittest.TestCase):
    def unit(self, present=True, active=False):
        return {"unit_present": present, "active": active, "unit_file_state": "disabled",
                "runtime": {"state": "running" if active else "stopped", "reason": "observed", "checked_at": "now"}}

    def test_installation_uses_files_registration_version_not_autostart(self):
        cases = [([True], True, "1", "installed"), ([True, False], True, "1", "incomplete"),
                 ([True], False, "1", "incomplete"), ([True], None, "1", None),
                 ([None], True, "1", None), ([True], True, "", None),
                 ([False], False, "", "not_installed"), ([], True, "1", None)]
        for files, present, version, expected in cases:
            with self.subTest(files=files, present=present, version=version):
                result = installation_state(files, self.unit(present), version, templated=False, checked_at="now")
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["checked_at"], "now")
                self.assertTrue(result["reason"])

    def test_shared_template_alone_is_not_a_direct_installation(self):
        self.assertEqual(installation_state([False], self.unit(), "1", templated=True, checked_at="now")["state"], "not_installed")
        self.assertEqual(installation_state([False], self.unit(active=True), "1", templated=True, checked_at="now")["state"], "incomplete")

    def test_installation_runtime_health_and_operation_remain_independent(self):
        unit = self.unit()
        manifest = {"service": "example.service"}
        action = {"action": "protocol-update:example", "unit": "update-1", "state": "running"}
        result = component_state("example", manifest, [True], unit, "1", None, action)
        self.assertEqual(result["installation"]["state"], "installed")
        self.assertEqual(result["runtime"]["state"], "stopped")
        self.assertEqual(result["health"]["state"], "unchecked")
        self.assertEqual(result["operation"]["kind"], "updating")
        self.assertIsNone(component_state("other", manifest, [True], unit, "1", None, action)["operation"])
        action["state"] = "succeeded"
        self.assertIsNone(component_state("example", manifest, [True], unit, "1", None, action)["operation"])
        self.assertEqual(component_state("example", manifest, [True], unit, "1", None, None)["operation"]["state"], "unknown")

    def test_health_requires_fresh_check_and_not_running_process(self):
        now = datetime.now(timezone.utc)
        for source, expected in [("healthy", "healthy"), ("warning", "possible_issues"), ("critical", "error"), ("pending", "unchecked")]:
            result = health_state({"status": source, "checked_at": now.isoformat()}, now=now)
            self.assertEqual(result["state"], expected)
        for stamp in [(now - timedelta(seconds=181)).isoformat(), "invalid", None, {}, (now + timedelta(seconds=10)).isoformat()]:
            self.assertEqual(health_state({"status": "healthy", "checked_at": stamp}, now=now)["state"], "unknown")

    def test_file_probe_preserves_unknown_and_uses_image_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "wg0.conf").write_text("fixture")
            manifest = {"installation": {"required_paths": [{"path": "{install_dir}/{interface}.conf"}, {"path": "{install_dir}/absent"}]}}
            self.assertEqual(image_files(manifest, "wg0", root), [True, False])
            with patch("component_state.Path.stat", side_effect=PermissionError):
                self.assertEqual(image_files(manifest, "wg0", root), [None, None])

    def test_existing_images_have_evidence_and_api_keeps_stopped_installation(self):
        for manifest_path in (ROOT / "protocol-images").glob("*/manifest.json"):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["installation"]["required_paths"], manifest_path)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "example"
            image.mkdir()
            (image / "install.sh").write_text("fixture")
            config = image / "config"
            config.write_text("fixture")
            (image / "manifest.json").write_text(json.dumps({"id": "example", "installer": "install.sh", "service": "example.service",
                "installation": {"required_paths": [{"path": str(config)}]}}))
            with patch.object(api, "PROTOCOL_IMAGES_DIR", root), patch.object(api, "ACTION_FILE", root / "action.json"), \
                 patch.object(api, "observe_service", return_value=self.unit()), \
                 patch.object(api, "protocol_version_info", return_value={"installed_version": "1"}):
                result = api.protocol_image_manifests()["example"]
            self.assertEqual(result["component_state"]["installation"]["state"], "installed")
            self.assertEqual(result["component_state"]["runtime"]["state"], "stopped")
            self.assertEqual(result["component_state"]["health"]["state"], "unchecked")
