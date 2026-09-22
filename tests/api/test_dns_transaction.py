import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tests.api.support import api
import application_operation as operations
import dns_transaction as dns


class DnsTransactionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.active = "running"
        paths = {name: self.root / name for name in (*dns.FILES, "ENV_FILE")}
        self.fixture = SimpleNamespace(DATA_DIR=self.root, **paths)
        self.fixture.ENV_FILE.write_text("ADMIN_PASSWORD=previous\nWG_DNS=1.1.1.1\n")
        self.fixture.DNS_SETTINGS_FILE.write_text('{"before":true}')
        self.fixture.VLESS_CONFIG.write_text('{"before":true}')
        self.fixture.observe_service = Mock(side_effect=lambda _: {"unit_present": True, "runtime": {"state": self.active}})
        def command(*args, **kwargs):
            if args[0] == "systemctl":
                self.active = "stopped" if args[1] == "stop" else "running"
            return ""
        self.fixture.run = Mock(side_effect=command)
        self.settings = api.DnsSettingsUpdate(selected_id="cloudflare", apply_wg=False, apply_awg=False,
            apply_shadowsocks=False, apply_vrx=True, apply_openvpn=False, apply_ikev2=False)

    def interrupted(self):
        def apply(_):
            self.fixture.DNS_SETTINGS_FILE.write_text('{"changed":true}')
            self.fixture.VLESS_CONFIG.write_text('{"changed":true}')
            self.fixture.ENV_FILE.write_text("ADMIN_PASSWORD=previous\nWG_DNS=9.9.9.9\n")
            raise KeyboardInterrupt()
        self.fixture.update_dns_settings = apply
        with self.assertRaises(KeyboardInterrupt):
            dns.configure(self.fixture, self.settings)

    def test_recovery_survives_interruption_and_preserves_unrelated_credentials(self):
        self.interrupted()
        snapshot = dns.snapshot_path(self.fixture)
        self.assertTrue(snapshot.exists())
        self.fixture.ENV_FILE.write_text("ADMIN_PASSWORD=rotated\nWG_DNS=9.9.9.9\n")
        dns.recover(self.fixture)
        self.assertEqual(json.loads(self.fixture.DNS_SETTINGS_FILE.read_text()), {"before": True})
        self.assertEqual(json.loads(self.fixture.VLESS_CONFIG.read_text()), {"before": True})
        self.assertIn("ADMIN_PASSWORD=rotated", self.fixture.ENV_FILE.read_text())
        self.assertIn("WG_DNS=1.1.1.1", self.fixture.ENV_FILE.read_text())
        self.assertFalse(snapshot.exists())
        self.fixture.run.reset_mock()
        dns.recover(self.fixture)
        self.fixture.run.assert_not_called()

    def test_unknown_recovery_result_keeps_snapshot_and_blocks_new_mutations(self):
        self.interrupted()
        self.fixture.observe_service.side_effect = lambda _: {"unit_present": None, "runtime": {"state": "unknown"}}
        with self.assertRaises(RuntimeError):
            dns.recover(self.fixture)
        self.assertTrue(dns.snapshot_path(self.fixture).exists())
        with self.assertRaises(operations.OperationConflict):
            with operations.short_mutation(self.root):
                self.fail("Mutation must not be admitted")
        launch = Mock()
        with self.assertRaises(operations.OperationConflict):
            operations.start(self.root, self.root / "action.json", "update", ["update"], launch)
        launch.assert_not_called()

    def test_stopped_service_stays_stopped_after_successful_apply(self):
        self.active = "stopped"
        def apply(_):
            self.active = "running"
        self.fixture.update_dns_settings = apply
        dns.configure(self.fixture, self.settings)
        self.assertEqual(self.active, "stopped")
        self.assertFalse(dns.snapshot_path(self.fixture).exists())

    def test_malformed_snapshot_cannot_write_arbitrary_file(self):
        self.interrupted()
        snapshot = dns.snapshot_path(self.fixture)
        value = json.loads(snapshot.read_text())
        value["files"]["../../foreign"] = None
        snapshot.write_text(json.dumps(value))
        self.fixture.run.reset_mock()
        with self.assertRaises(ValueError):
            dns.recover(self.fixture)
        self.fixture.run.assert_not_called()
