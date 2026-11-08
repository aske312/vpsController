"""Exercise the explicit cleanup boundary and the existing lifecycle dispatcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import ROOT, api
import direct_retirement
from component_registry import ComponentRegistry, RegistryError


class DirectCleanupTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.install = self.root / 'install'
        self.data = self.root / 'data'
        self.install.mkdir()
        (self.install / '.prebuilt-release').write_text('edition=pro\n')
        self.registry = ComponentRegistry(self.data)
        for identity, directory in direct_retirement.DIRECT_IMAGES.items():
            target = self.install / 'protocol-images' / directory
            target.mkdir(parents=True)
            shutil.copyfile(ROOT / 'protocol-images' / directory / 'manifest.json', target / 'manifest.json')

    def test_light_and_vpn_only_panel_cannot_be_cleaned(self):
        (self.install / '.prebuilt-release').write_text('edition=light\n')
        with self.assertRaises(RegistryError):
            direct_retirement.cleanup_plan(self.install, self.data)
        (self.install / '.prebuilt-release').write_text('edition=pro\n')
        with self.assertRaises(RegistryError):
            direct_retirement.cleanup_plan(self.install, self.data, {'ACCESS_MODE': 'vpn'})

    def test_unowned_clients_are_never_deleted(self):
        self.data.mkdir()
        path = self.data / 'clients.json'
        saved = '[{"id":"existing","protocol":"awg"}]'
        path.write_text(saved)
        with self.assertRaises(RegistryError):
            direct_retirement.cleanup_plan(self.install, self.data)
        self.assertEqual(path.read_text(), saved)

    def test_mihomo_interface_and_overridden_shared_paths_are_rejected(self):
        self.registry.write_receipt('wg', 'installed')
        with self.assertRaises(RegistryError):
            direct_retirement.cleanup_plan(self.install, self.data, {'WG_INTERFACE': 'mh-wg0'})
        with patch.dict(os.environ, {'WG_CONFIG': '/etc/vps-control/environment'}), self.assertRaises(RegistryError):
            direct_retirement.cleanup_plan(self.install, self.data, {'WG_INTERFACE': 'wg0'})

    @unittest.skipUnless(os.name == 'posix', 'Linux lifecycle paths')
    def test_all_owned_components_preflight_together_and_retained_data_is_distinct(self):
        for identity in direct_retirement.DIRECT_IMAGES:
            self.registry.write_receipt(identity, 'installed', retained=identity == 'wg')
        self.registry.write_receipt('mihomo', 'installed')
        with patch.object(direct_retirement, 'inventory') as inventory:
            plan = direct_retirement.cleanup_plan(self.install, self.data, {'WG_INTERFACE': 'wg0', 'AWG_INTERFACE': 'awg0'})
        self.assertEqual(dict(plan), {identity: identity == 'wg' for identity in direct_retirement.DIRECT_IMAGES})
        self.assertEqual(inventory.call_count, 9)
        self.assertNotIn('mihomo', dict(plan))

    def shell(self, flag, fail=False):
        executable = shutil.which('bash') if os.name == 'posix' else r'D:\Git\bin\bash.exe'
        if not executable or not Path(executable).exists():
            self.skipTest('Requires Bash')
        source = (ROOT / 'scripts/vps-control.sh').read_text(encoding='utf-8')
        function = source[source.index('cleanup_direct() {'):source.index('\nupdate_protocol_image() {')]
        stub = '''set -eu
INSTALL_DIR=/fixture DATA_DIR=/fixture WG_INTERFACE=wg0 AWG_INTERFACE=awg0 ACCESS_MODE=external
env_value() { :; }
info() { :; }
ok() { :; }
die() { exit 42; }
python3() { [ "$PLAN_FAIL" != 1 ] || return 1; printf 'wg:retained\\nawg:installed\\n'; }
remove_protocol_image() { printf 'remove:%s\\n' "$2" >> calls; }
purge_protocol_data() { printf 'purge:%s\\n' "$2" >> calls; }
'''
        return subprocess.run([executable, '-c', stub + function + '\ncleanup_direct direct-cleanup ' + flag],
                              cwd=self.root, env={**os.environ, 'PLAN_FAIL': '1' if fail else '0'}, capture_output=True)

    def test_cleanup_is_explicit_and_preflight_failure_starts_no_removal(self):
        self.assertEqual(self.shell('').returncode, 42)
        self.assertFalse((self.root / 'calls').exists())
        self.assertEqual(self.shell('--permanent', fail=True).returncode, 42)
        self.assertFalse((self.root / 'calls').exists())

    def test_retained_component_is_only_purged_and_active_component_stops_first(self):
        result = self.shell('--permanent')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['purge:wg', 'remove:awg', 'purge:awg'])

    def test_application_operation_passes_permanent_flag_without_a_second_worker(self):
        with patch.object(api, 'start_application_operation', return_value={'state': 'queued'}) as start:
            api.application_action(api.ApplicationAction(action='direct-cleanup'), None, 'a' * 32)
        self.assertEqual(start.call_args.args[0], 'direct-cleanup')
        self.assertEqual(start.call_args.args[1][-2:], ['direct-cleanup', '--permanent'])
