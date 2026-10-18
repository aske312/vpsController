import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from tests.api.support import manager


class TunnelAddressTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = root / "mh-awg0.conf"
        self.config.write_text("[Interface]\nPrivateKey = fixture-server-private\n")
        self.enterContext(patch.object(manager, "WG_CONFIG_BY_MODULE", {"transport-awg": self.config}))
        self.enterContext(patch.object(manager, "module_settings", return_value={
            "subnet": "10.83.0.0/24", "port": 51831, "mtu": 1280,
            **{key: 1 for key in ("jc", "jmin", "jmax", "s1", "s2", "h1", "h2", "h3", "h4")},
        }))

    def test_batch_peers_reserve_ips_before_profile_is_saved(self):
        with patch.object(manager, "profiles", return_value=[]), patch.object(manager, "keypair", side_effect=[("private-a", "public-a"), ("private-b", "public-b")]), patch.object(manager, "run", return_value=CompletedProcess([], 0, "server-public\n", "")):
            first = manager.add_wg_credential("profile", "transport-awg", "first")
            second = manager.add_wg_credential("profile", "transport-awg", "second")
        self.assertEqual(first["ip"], "10.83.0.2/24")
        self.assertEqual(second["ip"], "10.83.0.3/24")
        self.assertIn("AllowedIPs = 10.83.0.2/32", self.config.read_text())
        self.assertIn("AllowedIPs = 10.83.0.3/32", self.config.read_text())

    def test_existing_prefixes_and_retiring_credentials_remain_reserved(self):
        self.config.write_text("[Peer]\nAllowedIPs = 10.83.0.2/31, 10.83.0.4/32\n")
        profile = {"connections": [], "retiring_connections": [{"component": "transport-awg", "credential": {"ip": "10.83.0.5/24"}}]}
        with patch.object(manager, "profiles", return_value=[profile]):
            self.assertEqual(manager.next_tunnel_address("transport-awg")[0], "10.83.0.6")
