"""Shared QUIC listener edits must preserve delivered connections."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import manager, ROOT


class QuicSettingsSafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(manager, "SETTINGS_ROOT", self.root / "settings"))
        self.enterContext(patch.object(manager, "CONFIG_ROOT", self.root / "config"))
        self.enterContext(patch.object(manager, "SUBMODULE_ROOT", ROOT / "protocol-images/mihomo/modules"))
        for module in ("transport-hysteria2", "transport-tuic"):
            manager.atomic_json(manager.SETTINGS_ROOT / f"{module}.json", {"port": 20000 if module.endswith("hysteria2") else 20001})

    def test_breaking_edits_with_current_retiring_or_retained_credentials_reject_before_effects(self):
        for module, edit in (("transport-hysteria2", {"port": 21000}), ("transport-tuic", {"port": 21000}), ("transport-hysteria2", {"obfs": False})):
            for source in ("connections", "retiring_connections", "credentials"):
                with (self.subTest(module=module, edit=edit, source=source),
                      patch.object(manager, "profiles", return_value=[] if source == "credentials" else [{source: [{"component": module}]}]),
                      patch.object(manager, "quic_credentials", return_value=[{"instance_id": "retained"}] if source == "credentials" else []),
                      patch.object(manager, "check_module_port") as port,
                      patch.object(manager, "atomic_json") as write,
                      patch.object(manager, "profile_runtime_transaction") as snapshot,
                      patch.object(manager, "apply_module_settings") as apply,
                      self.assertRaises(manager.HTTPException) as conflict):
                    manager.patch_module_settings(module, manager.ModuleSettingsPatch(values=edit))
                self.assertEqual(conflict.exception.status_code, 409)
                port.assert_not_called(); write.assert_not_called(); snapshot.assert_not_called(); apply.assert_not_called()

    def test_compatible_edits_and_empty_listener_changes_remain_available(self):
        for module, edit, consumers in (("transport-hysteria2", {"up_mbps": 200}, True),
                                        ("transport-tuic", {"heartbeat": "15s"}, True),
                                        ("transport-hysteria2", {"port": 21000}, False),
                                        ("transport-hysteria2", {"obfs": False}, False),
                                        ("transport-tuic", {"port": 21000}, False)):
            with (self.subTest(module=module, edit=edit, consumers=consumers),
                  patch.object(manager, "profiles", return_value=[{"connections": [{"component": module}]}] if consumers else []),
                  patch.object(manager, "quic_credentials", return_value=[]),
                  patch.object(manager, "check_module_port"),
                  patch.object(manager, "module_is_installed", return_value=True),
                  patch.object(manager.shutil, "which", return_value=None),
                  patch("quic_settings.prepare", return_value=(b"managed", {}, "running")),
                  patch.object(manager, "profile_runtime_transaction", return_value=nullcontext()),
                  patch.object(manager, "apply_module_settings", return_value={"saved": True}) as apply):
                self.assertEqual(manager.patch_module_settings(module, manager.ModuleSettingsPatch(values=edit)), {"saved": True})
                apply.assert_called_once()

    def test_active_or_unknown_firewall_rejects_port_change_before_any_effect(self):
        for module in ("transport-hysteria2", "transport-tuic"):
            for code, output in ((0, "Status: active"), (1, ""), (0, "unrecognized status")):
                with (self.subTest(module=module, code=code, output=output),
                      patch.object(manager, "profiles", return_value=[]),
                      patch.object(manager, "quic_credentials", return_value=[]),
                      patch.object(manager, "module_is_installed", return_value=True),
                      patch.object(manager.shutil, "which", return_value="ufw"),
                      patch.object(manager, "run", return_value=subprocess.CompletedProcess([], code, output, "")) as run,
                      patch.object(manager, "atomic_json") as write,
                      patch.object(manager, "profile_runtime_transaction") as snapshot,
                      patch.object(manager, "apply_module_settings") as apply,
                      self.assertRaises(manager.HTTPException) as conflict):
                    manager.patch_module_settings(module, manager.ModuleSettingsPatch(values={"port": 21000}))
                self.assertEqual(conflict.exception.status_code, 409)
                run.assert_called_once_with("ufw", "status")
                write.assert_not_called(); snapshot.assert_not_called(); apply.assert_not_called()

    def test_inactive_firewall_and_uninstalled_module_allow_empty_listener_port_selection(self):
        for installed in (True, False):
            with (self.subTest(installed=installed),
                  patch.object(manager, "profiles", return_value=[]),
                  patch.object(manager, "quic_credentials", return_value=[]),
                  patch.object(manager, "module_is_installed", return_value=installed),
                  patch.object(manager.shutil, "which", return_value="ufw"),
                  patch.object(manager, "run", return_value=subprocess.CompletedProcess([], 0, "Status: inactive", "")) as run,
                  patch.object(manager, "check_module_port"),
                  patch("quic_settings.prepare", return_value=(b"managed", {}, "running")),
                  patch.object(manager, "profile_runtime_transaction", return_value=nullcontext()),
                  patch.object(manager, "apply_module_settings", return_value={"saved": True}) as apply):
                self.assertEqual(manager.patch_module_settings("transport-hysteria2", manager.ModuleSettingsPatch(values={"port": 21000})), {"saved": True})
                if installed:
                    run.assert_called_once_with("ufw", "status")
                else:
                    run.assert_not_called()
                apply.assert_called_once()

    def test_automatic_port_reassignment_does_not_invalidate_retained_credentials(self):
        for module in ("transport-hysteria2", "transport-tuic"):
            with (self.subTest(module=module),
                  patch.object(manager, "check_module_port", side_effect=manager.ModulePortConflict("occupied")),
                  patch.object(manager, "profiles", return_value=[]),
                  patch.object(manager, "quic_credentials", return_value=[{"instance_id": "retained"}]),
                  patch.object(manager.port_allocation, "allocate") as allocate,
                  patch.object(manager, "atomic_json") as write,
                  self.assertRaises(manager.ModulePortConflict)):
                manager.prepare_module_port(module)
            allocate.assert_not_called(); write.assert_not_called()

    def test_unchanged_listener_runtime_never_modifies_ufw_even_on_failure(self):
        for module in ("transport-hysteria2", "transport-tuic"):
            for healthy in (True, False):
                with self.subTest(module=module, healthy=healthy):
                    port = manager.module_settings(module)["port"]
                    path = manager.quic_root(module) / "config.json"
                    manager.atomic_json(path, {"inbounds": [{"listen_port": port}]})
                    original = path.read_bytes()
                    events = []
                    def run(*args, **kwargs):
                        events.append(args)
                        return subprocess.CompletedProcess(args, 0, "Status: active", "")
                    with (patch.object(manager, "run", side_effect=run),
                          patch.object(manager.shutil, "which", return_value="ufw"),
                          patch.object(manager, "quic_telemetry_service", return_value=None),
                          patch.object(manager, "start_quic_telemetry"),
                          patch.object(manager, "service_stably_active", return_value=healthy)):
                        if healthy:
                            manager.write_quic_runtime(module)
                            self.assertEqual(json.loads(path.read_text())["inbounds"][0]["listen_port"], port)
                        else:
                            with self.assertRaises(RuntimeError):
                                manager.write_quic_runtime(module)
                            self.assertEqual(path.read_bytes(), original)
                    self.assertTrue(any(args[0] == "systemctl" for args in events))
                    self.assertFalse(any(args[0] == "ufw" for args in events))
