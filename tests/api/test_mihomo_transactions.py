import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import load_module

manager = load_module("mihomo_manager_under_test", "protocol-images/mihomo/manager.py")


class MihomoTransactionTests(unittest.TestCase):
    def test_vless_limit_is_applied_separately_per_route(self):
        def connections(route, count, device="phone"):
            return [{"component": "transport-reality", "device_id": device, "settings": {"route_mode": route}} for _ in range(count)]

        with patch.object(manager, "max_vless_connections_per_device", return_value=5):
            manager.validate_vless_connection_limit(connections("direct", 5) + connections("cdn", 5) + connections("tls", 5))
            with self.assertRaisesRegex(manager.HTTPException, "режиме REALITY"):
                manager.validate_vless_connection_limit(connections("direct", 6))
            with self.assertRaisesRegex(manager.HTTPException, "режиме CDN"):
                manager.validate_vless_connection_limit(connections("cdn", 6))
            with self.assertRaisesRegex(manager.HTTPException, "режиме TLS"):
                manager.validate_vless_connection_limit(connections("tls", 6))

    def test_fault_during_mutation_restores_profile_and_adapter_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            config = root / "config"
            profile = root / "profiles.json"
            config.mkdir()
            (config / "adapter.json").write_text('{"before": true}\n', encoding="utf-8")
            profile.write_text('[{"id":"before"}]\n', encoding="utf-8")
            with (
                patch.object(manager, "CONFIG_ROOT", config),
                patch.object(manager, "PROFILE_FILE", profile),
                patch.object(manager, "SERVICE_BY_MODULE", {}),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with manager.profile_runtime_transaction({"transport-reality"}):
                        (config / "adapter.json").write_text('{"after": true}\n', encoding="utf-8")
                        profile.write_text('[{"id":"after"}]\n', encoding="utf-8")
                        raise RuntimeError("injected")
            self.assertEqual(json.loads((config / "adapter.json").read_text()), {"before": True})
            self.assertEqual(json.loads(profile.read_text()), [{"id": "before"}])

    def test_batched_reality_apply_restarts_and_reloads_once(self):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch.object(manager, "load_json", return_value={"inbounds": []}),
            patch.object(manager, "apply_reality_config"),
            patch.object(manager, "rebuild_vless_cdn_snippet"),
            patch.object(manager, "service_stably_active", return_value=True),
            patch.object(manager, "run", side_effect=fake_run),
            patch.object(manager.Path, "is_file", return_value=False),
        ):
            manager.apply_batched_reality_runtime()
        self.assertEqual(sum(call[:2] == ("systemctl", "restart") for call in calls), 1)
        self.assertEqual(sum(call[:2] == ("systemctl", "reload") for call in calls), 1)


if __name__ == "__main__":
    unittest.main()
