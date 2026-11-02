#!/usr/bin/env bash
set -Eeuo pipefail

archive="$(realpath -- "${1:?Usage: verify-release.sh archive.tar.gz}")"
work="$(mktemp -d)"
trap 'rm -rf -- "${work}"' EXIT
tar -xzf "${archive}" -C "${work}"
cd "${work}/vps-control-release"
sha256sum -c release.sha256 >"${work}/checksums.log" || { cat "${work}/checksums.log"; exit 1; }
python3 - "${BUILD_COMMIT:?Expected BUILD_COMMIT is required}" <<'PY'
from pathlib import Path
import platform
import sys

metadata = dict(line.split('=', 1) for line in Path('.prebuilt-release').read_text().splitlines())
architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()]
assert metadata['schema'] == '1'
assert metadata['edition'] == 'pro'
assert metadata['commit'] == sys.argv[1]
assert metadata['architecture'] == architecture
assert metadata['platform'] == 'linux'
PY
node --input-type=module -e "await import('rolldown'); await import('vinext'); await import('react')"
./api/bin/xray version | grep -F 'vpsController-trojan-fragment-v1' >/dev/null
./api/bin/mihomo -v | grep -F 'Mihomo Meta v' >/dev/null
echo "Verified release archive, runtime imports and native cores: ${archive}"
