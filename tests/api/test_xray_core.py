import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.api.support import api, manager, ROOT
import xray_core


class XrayCoreTests(unittest.TestCase):
    def test_reviewed_build_is_distinguished_from_same_official_version(self):
        self.assertEqual(xray_core.version('Xray 26.3.27 (Xray) official'), '26.3.27')
        self.assertEqual(xray_core.version('Xray 26.3.27 (Xray) '+xray_core.BUILD_MARKER), '26.3.27+vps1')
        self.assertEqual(xray_core.version('unavailable'), '')
        for patched in (False, True):
            installed = '26.3.27+vps1' if patched else '26.3.27'
            with self.subTest(patched=patched):
                with patch.object(api, 'xray_installed_version', side_effect=[installed, '26.3.27+vps1']), patch.object(api, 'github_latest_tag') as github:
                    status = api.protocol_version_info({}, 'vless-reality-xhttp', True)
                    self.assertEqual(status['update_available'], not patched)
                    github.assert_not_called()
                with patch.object(manager, 'xray_installed_version', side_effect=[installed, '26.3.27+vps1']), patch.object(manager, 'github_latest_tag') as github:
                    status = manager.module_version_info('transport-reality', {}, True)
                    self.assertEqual(status['update_available'], not patched)
                    github.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'Linux shell staging and executable permissions')
    def test_installer_stages_only_verified_bundle_and_preserves_destination_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            shutil.copy(ROOT/'scripts/xray-core.sh', root/'scripts/xray-core.sh')
            bundle = root/'api/bin/xray'
            bundle.parent.mkdir(parents=True)
            destination = root/'candidate'
            def stage():
                return subprocess.run(['bash', '-c', 'set -euo pipefail; source "$1"; stage_xray_core "$2"', '_',
                                       str(root/'scripts/xray-core.sh'), str(destination)], capture_output=True, text=True)
            def digest():
                bundle.with_suffix('.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest()+'  xray\n')
            bundle.write_text('#!/bin/sh\necho "Xray 26.3.27 '+xray_core.BUILD_MARKER+'"\n')
            bundle.chmod(0o755)
            digest()
            self.assertEqual(stage().returncode, 0)
            previous = destination.read_bytes()
            bundle.write_text('#!/bin/sh\necho "modified"\n')
            self.assertNotEqual(stage().returncode, 0)
            self.assertEqual(destination.read_bytes(), previous)
            digest()
            self.assertNotEqual(stage().returncode, 0)
            self.assertEqual(destination.read_bytes(), previous)
