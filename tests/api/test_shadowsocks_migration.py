import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.api.support import manager, managed_mihomo_fixture


class ShadowsocksMigrationTests(unittest.TestCase):
    def setUp(self):
        managed_mihomo_fixture(self)

    def test_existing_runtime_is_migrated_even_when_binary_is_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            template = Path(temp) / "ss.service"
            template.touch()
            with patch.object(manager, "SHADOWSOCKS_SERVICE_TEMPLATE", template), \
                    patch.object(manager.shutil, "which", return_value="/usr/bin/ss-server"), \
                    patch.object(manager, "run") as run:
                manager.ensure_shadowsocks_protection()
            run.assert_called_once_with(
                "bash", str(manager.SUBMODULE_ROOT / "transport-shadowsocks" / "protect-runtime.sh"),
                "--restart-active", check=True,
            )

    def test_absent_transport_is_not_installed_by_maintenance(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(manager, "SHADOWSOCKS_SERVICE_TEMPLATE", Path(temp) / "missing"), \
                    patch.object(manager, "run") as run:
                manager.ensure_shadowsocks_protection()
            run.assert_not_called()

    def test_failed_migration_is_retried_without_blocking_other_maintenance(self):
        stopped = threading.Event()
        cycles = []

        def wait(_):
            cycles.append(True)
            if len(cycles) == 2:
                stopped.set()

        with patch.object(stopped, "wait", side_effect=wait), \
                patch.object(manager, "ensure_shadowsocks_protection", side_effect=[RuntimeError("failed"), None]) as migrate, \
                patch.object(manager, "cleanup_profile_transitions") as cleanup, \
                patch.object(manager, "ensure_reality_telemetry"), \
                patch.object(manager, "ensure_quic_telemetry"), \
                patch.object(manager.logger, "error") as error:
            manager.transition_worker(stopped)
        self.assertEqual(migrate.call_count, 2)
        self.assertEqual(cleanup.call_count, 2)
        error.assert_called_once()
