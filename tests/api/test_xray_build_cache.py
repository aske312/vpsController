"""Real Xray packaging/cache flow with a tiny fake compiler and source download."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.api.support import ROOT

BASH = shutil.which('bash') if os.name != 'nt' else next((str(p) for p in (Path('D:/Git/bin/bash.exe'), Path('C:/Program Files/Git/bin/bash.exe')) if p.exists()), None)


def bash_path(path):
    value = path.resolve().as_posix()
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value


@unittest.skipUnless(BASH, 'Requires Bash')
class XrayBuildCacheTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='xray-cache-', dir=runtime)))
        scripts = self.root / 'scripts'
        (scripts / 'xray').mkdir(parents=True)
        self.patch = scripts / 'xray/fixture.go'
        self.patch.write_text('fixture patch')
        # Substitute only the fixture archive digest; the actual shell flow,
        # cache fingerprint, checksum validation and artifact copies are unchanged.
        source = (ROOT / 'scripts/build-xray.sh').read_text().replace(
            '14fa566ee0a801d3d51144c67018b449f5dcf462ddfacadd032da069787e61f9',
            hashlib.sha256(b'fixture source').hexdigest())
        (scripts / 'build-xray.sh').write_text(source, encoding='utf-8')
        for name in ('trojan_first_read.go', 'trojan_first_read_test.go'):
            (scripts / 'xray' / name).write_text('fixture')

    def invoke(self):
        script = r'''set -eu
event() { printf '%s\n' "$*" >> "$FIXTURE_ROOT/events"; }
python3() { "$FIXTURE_PYTHON" "$@"; }
go() {
  case "$1" in
    version) printf 'go version go1.26.10 linux/fixture\n' ;;
    test) event TEST ;;
    build)
      event BUILD
      while [[ "$1" != -o ]]; do shift; done
      shift
      printf '#!/usr/bin/env bash\necho Xray vpsController-trojan-fragment-v1\n' > "$1"
      chmod +x "$1"
      ;;
  esac
}
curl() {
  event DOWNLOAD
  while [[ "$1" != -o ]]; do shift; done
  printf 'fixture source' > "$2"
}
tar() {
  mkdir -p "$WORK_DIR/Xray-core-$COMMIT/proxy/trojan"
  printf 'firstLen, err := first.ReadFrom(conn)' > "$WORK_DIR/Xray-core-$COMMIT/proxy/trojan/server.go"
  printf 'fixture license' > "$WORK_DIR/Xray-core-$COMMIT/LICENSE"
}
export XRAY_CACHE_DIR="$FIXTURE_ROOT/cache"
source "$FIXTURE_ROOT/scripts/build-xray.sh" "$FIXTURE_ROOT/output/xray"
'''
        result = subprocess.run([BASH, '-s'], input=script, env={**os.environ, 'FIXTURE_ROOT': bash_path(self.root),
                                'FIXTURE_PYTHON': bash_path(Path(sys.executable))},
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.root / 'events').read_text().splitlines()

    def test_warm_build_reuses_only_verified_binary_source_and_license(self):
        self.assertEqual(self.invoke(), ['DOWNLOAD', 'TEST', 'BUILD'])
        self.assertEqual(self.invoke(), ['DOWNLOAD', 'TEST', 'BUILD'])
        output = self.root / 'output/xray'
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        self.assertEqual(output.with_suffix('.sha256').read_text().split()[0], digest)
        self.assertEqual(output.with_suffix('.source.tar.gz').read_bytes(), b'fixture source')
        self.assertEqual(output.with_suffix('.LICENSE').read_text(), 'fixture license')

    def test_changed_patch_invalidates_cache_and_repeats_compiler_tests(self):
        self.invoke()
        self.patch.write_text('changed patch')
        self.assertEqual(self.invoke(), ['DOWNLOAD', 'TEST', 'BUILD'] * 2)

    def test_corrupt_cached_binary_is_rebuilt_instead_of_shipped(self):
        self.invoke()
        cached = next((self.root / 'cache').glob('*/xray'))
        cached.write_text('corrupted')
        self.assertEqual(self.invoke(), ['DOWNLOAD', 'TEST', 'BUILD'] * 2)
        self.assertNotEqual((self.root / 'output/xray').read_text(), 'corrupted')
