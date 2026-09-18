import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import api
import runtime_dependencies as dependencies


class DependencyTests(unittest.TestCase):
    def setUp(self):
        migration = patch.object(dependencies.legacy_dns, 'migrate', return_value=False)
        self.migrate_dns = migration.start()
        self.addCleanup(migration.stop)

    def test_newer_caddy_is_preserved_without_installing_anything(self):
        with patch.object(dependencies.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'v2.12.0', '')) as run:
            dependencies.install()
            self.assertEqual(run.call_count, 1)
            self.migrate_dns.assert_called_once_with()

    def test_dns_migration_failure_prevents_package_changes(self):
        self.migrate_dns.side_effect = RuntimeError('Invalid legacy DNS')
        with patch.object(dependencies.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Invalid legacy DNS'):
                dependencies.install()
        run.assert_not_called()

    def test_bundled_package_hash_is_verified_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'packages').mkdir()
            spec = {'caddy': {'version': '2.11.4', 'assets': {'amd64': hashlib.sha256(b'expected').hexdigest()}}}
            (root / 'runtime-dependencies.json').write_text(json.dumps(spec))
            (root / 'packages/caddy_2.11.4_linux_amd64.deb').write_bytes(b'wrong')
            with patch.object(dependencies, 'ROOT', root), patch.object(dependencies.platform, 'machine', return_value='x86_64'), patch.object(dependencies, 'caddy_is_current', return_value=False), patch.object(dependencies.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Контрольная сумма'):
                    dependencies.install()
                run.assert_not_called()

    def test_release_validation_uses_candidate_without_installing_it(self):
        with patch.object(dependencies, 'caddy_is_current', return_value=False), patch.object(dependencies, 'package', return_value=Path('/verified.deb')), patch.object(dependencies.subprocess, 'run') as run:
            with dependencies.candidate_caddy() as binary:
                self.assertTrue(Path(binary).as_posix().endswith('payload/usr/bin/caddy'))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], 'dpkg-deb')

    def test_package_install_preserves_config_and_restarts_running_caddy(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, 'v2.11.4', '')
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'Caddyfile'; config.write_text('original')
            with patch.object(dependencies, 'CADDY_CONFIG', config), patch.object(dependencies, 'caddy_is_current', side_effect=[False, True]), patch.object(dependencies, 'package', return_value=Path('/verified.deb')), patch.object(dependencies.subprocess, 'run', side_effect=run):
                dependencies.install()
            self.assertEqual(config.read_text(), 'original')
        install = next(args for args in calls if args[0] == 'apt-get')
        self.assertIn('--no-remove', install)
        self.assertIn('Dpkg::Options::=--force-confold', install)
        self.assertIn(['systemctl', 'restart', 'caddy.service'], calls)
        self.assertLess(next(i for i, args in enumerate(calls) if 'adapt' in args), calls.index(install))

    def test_incompatible_live_config_prevents_package_install(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'Caddyfile'; config.write_text('custom')
            def run(args, **kwargs):
                if 'adapt' in args:
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0, 'v2.11.4', '')
            with patch.object(dependencies, 'CADDY_CONFIG', config), patch.object(dependencies, 'caddy_is_current', return_value=False), patch.object(dependencies, 'package', return_value=Path('/verified.deb')), patch.object(dependencies.subprocess, 'run', side_effect=run) as command:
                with self.assertRaises(subprocess.CalledProcessError):
                    dependencies.install()
                self.assertFalse(any(call.args[0][0] == 'apt-get' for call in command.call_args_list))
