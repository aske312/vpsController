import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.api.support import api
from component_registry import ComponentRegistry, RegistryError, inventory, fingerprint
import component_migration as migration


class ComponentMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.registry = ComponentRegistry(self.root / "data")
        self.unit, self.contract = migration.service_contract("mihomo", Path("/opt/vps-control"))
        self.unit_path = self.root / "etc/systemd/system" / self.unit
        self.unit_path.parent.mkdir(parents=True)
        self.unit_path.write_text("\n".join(f"{k}={v}" for k, v in self.contract.items()))

    def observe(self, *args, **kwargs):
        return subprocess.CompletedProcess(args, 0, f"LoadState=loaded\nFragmentPath={self.unit_path}\nDropInPaths=\n", "")

    def proof(self):
        return migration.provenance_paths("mihomo", "", Path("/opt/vps-control"), root=self.root, run=self.observe)

    def test_panel_unit_contract_including_legacy_command_but_not_just_name(self):
        self.assertEqual(self.proof(), [self.unit_path])
        original = self.unit_path.read_text()
        self.unit_path.write_text(original.replace(" --no-server-header", ""))
        self.assertEqual(self.proof(), [self.unit_path])
        for text in (original.replace("manager:app", "external:app"),
                     original + "\nExecStart=/usr/bin/other", original.replace("GATE.312", "External")):
            self.unit_path.write_text(text)
            self.assertEqual(self.proof(), [])

    def test_overrides_and_different_loaded_fragment_prevent_migration(self):
        for props in (f"LoadState=loaded\nFragmentPath={self.unit_path}\nDropInPaths=/etc/override.conf",
                      "LoadState=loaded\nFragmentPath=/run/other.service\nDropInPaths=",
                      f"LoadState=loaded\nFragmentPath={self.unit_path}\nDropInPaths=\nNeedDaemonReload=yes",
                      "LoadState=not-found"):
            result = subprocess.CompletedProcess([], 0, props, "")
            with self.subTest(props=props):
                self.assertEqual(migration.provenance_paths("mihomo", "", Path("/opt/vps-control"), root=self.root,
                    run=lambda *a, **kw: result), [])

    def test_wg_file_and_panel_marker_alone_do_not_claim_external_interface(self):
        config = self.root / "etc/wireguard/wg0.conf"
        config.parent.mkdir(parents=True)
        config.write_text("[Interface]\nListenPort = 51820\n")
        marker = self.root / "etc/sysctl.d/99-vps-control-wireguard.conf"
        marker.parent.mkdir(parents=True)
        marker.write_text("net.ipv4.ip_forward=1\n")
        self.assertEqual(migration.provenance_paths("wg", "wg0", Path("/opt/vps-control"), root=self.root, run=self.observe), [])
        self.assertEqual(migration.provenance_paths("wg", "mh-wg0", Path("/opt/vps-control"), root=self.root, run=self.observe), [])

    def test_panel_wg_rules_require_distribution_unit_without_overrides(self):
        config = self.root / "etc/wireguard/wg0.conf"
        config.parent.mkdir(parents=True)
        config.write_text("[Interface]\nPostUp = iptables -C INPUT -p udp --dport 51820 -j ACCEPT; iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.72.0.0/24 -j MASQUERADE\n"
                          "PostDown = while iptables -C INPUT -p udp --dport 51820 -j ACCEPT; do true; done; while iptables -C FORWARD -i %i -j ACCEPT; do true; done; iptables -t nat -D POSTROUTING -s 10.72.0.0/24 -j MASQUERADE\n")
        marker = self.root / "etc/sysctl.d/99-vps-control-wireguard.conf"
        marker.parent.mkdir(parents=True)
        marker.write_text("net.ipv4.ip_forward=1\n")
        for fragment, expected in (("/usr/lib/systemd/system/wg-quick@.service", [config, marker]),
                                   ("/etc/systemd/system/wg-quick@wg0.service", [])):
            result = subprocess.CompletedProcess([], 0, f"LoadState=loaded\nFragmentPath={fragment}\nDropInPaths=\n", "")
            self.assertEqual(migration.provenance_paths("wg", "wg0", Path("/opt/vps-control"), root=self.root,
                run=lambda *args, **kwargs: result), expected)

    def test_migration_has_private_backup_is_idempotent_and_never_runs_service_commands(self):
        context = {"id": "mihomo", "migration": "legacy-panel-v1"}
        checksum = fingerprint(inventory([self.unit_path]), context)
        before = self.unit_path.read_bytes()
        with patch("subprocess.run", side_effect=AssertionError("Unexpected service action")):
            result = self.registry.adopt("mihomo", [self.unit_path], context, checksum,
                                         migrated=True, verify_provenance=lambda: True)
            self.registry.adopt("mihomo", [self.unit_path], context, checksum,
                                migrated=True, verify_provenance=lambda: True)
        receipt = self.registry.receipt("mihomo")
        self.assertEqual(receipt["origin"], "installed")
        self.assertEqual(receipt["migration"], "legacy-panel-v1")
        backup = self.registry.data_dir / "component-backups/mihomo" / result["backup_id"]
        self.assertTrue((backup / "configuration.tar").is_file())
        self.assertEqual(len(list(backup.parent.iterdir())), 1)
        self.assertEqual(self.unit_path.read_bytes(), before)

    def test_failed_provenance_recheck_or_backup_never_grants_ownership(self):
        checksum = fingerprint(inventory([self.unit_path]), {})
        with self.assertRaises(RegistryError):
            self.registry.adopt("mihomo", [self.unit_path], {}, checksum, migrated=True, verify_provenance=lambda: False)
        self.assertIsNone(self.registry.receipt("mihomo"))
        with patch("component_registry.tarfile.open", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.registry.adopt("mihomo", [self.unit_path], {}, checksum, migrated=True, verify_provenance=lambda: True)
        self.assertIsNone(self.registry.receipt("mihomo"))

    def test_startup_migrates_only_proven_candidates_and_preserves_corrupt_receipt(self):
        path = self.registry.receipt_path("wg")
        path.parent.mkdir(parents=True)
        path.write_text("broken")
        images = {key: {"interface": "", "management": self.registry.management(key)}
                  for key in ("mihomo", "trojan", "wg")}
        fake = SimpleNamespace(DATA_DIR=self.registry.data_dir, INSTALL_DIR=Path("/opt/vps-control"),
            protocol_image_manifests=lambda: images, adoption_plan=lambda key: ({"compatible": True}, [self.unit_path], {"id": key}),
            HTTPException=api.HTTPException, logger=api.logger)
        def evidence(key, *args):
            return [self.unit_path] if key == "mihomo" else []
        with patch.object(migration, "provenance_paths", side_effect=evidence):
            self.assertTrue(migration.migrate(fake))
        self.assertEqual(self.registry.management("mihomo")["state"], "managed")
        self.assertEqual(self.registry.management("trojan")["state"], "unmanaged")
        self.assertEqual(path.read_text(), "broken")

    def test_pending_operation_defers_migration_without_writing_receipts(self):
        self.registry.data_dir.mkdir()
        (self.registry.data_dir / "application-action.json").write_text(json.dumps({"state": "running"}))
        fake = SimpleNamespace(DATA_DIR=self.registry.data_dir)
        self.assertFalse(migration.migrate(fake))
        self.assertFalse(self.registry.root.exists())


if __name__ == "__main__":
    unittest.main()
