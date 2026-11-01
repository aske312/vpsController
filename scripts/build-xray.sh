#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:?Usage: build-xray.sh output-binary}"
OUTPUT="$(realpath -m -- "${OUTPUT}")"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf -- "${WORK_DIR}"' EXIT
for command_name in curl go python3 sha256sum tar; do
  command -v "${command_name}" >/dev/null || { echo "Missing command: ${command_name}" >&2; exit 1; }
done

# Official v26.3.27 source, plus the reviewed first-read compatibility change.
COMMIT=d2758a023cd7f4174a5a5fa4ff66e487d4342ba0
SHA256=14fa566ee0a801d3d51144c67018b449f5dcf462ddfacadd032da069787e61f9
cache=""
if [[ -n "${XRAY_CACHE_DIR:-}" ]]; then
  fingerprint="$(
    { go version; uname -sm; sha256sum "${BASH_SOURCE[0]}" "${ROOT_DIR}/scripts/xray/"*.go | awk '{print $1}'; } | sha256sum | awk '{print $1}'
  )"
  cache="${XRAY_CACHE_DIR}/${fingerprint}"
  if [[ -f "${cache}/checksums.sha256" ]] && (
    cd "${cache}"
    sha256sum -c checksums.sha256 &&
      printf '%s  source.tar.gz\n' "${SHA256}" | sha256sum -c - &&
      ./xray version | grep -F 'vpsController-trojan-fragment-v1' >/dev/null
  ); then
    mkdir -p -- "$(dirname -- "${OUTPUT}")"
    cp -- "${cache}/xray" "${OUTPUT}"
    cp -- "${cache}/source.tar.gz" "${OUTPUT}.source.tar.gz"
    cp -- "${cache}/LICENSE" "${OUTPUT}.LICENSE"
    (cd -- "$(dirname -- "${OUTPUT}")"; sha256sum "$(basename -- "${OUTPUT}")" >"${OUTPUT}.sha256")
    echo "Reused verified Xray build: ${fingerprint}"
    exit 0
  fi
fi
curl -fL --retry 4 --retry-all-errors -o "${WORK_DIR}/source.tar.gz" \
  "https://codeload.github.com/XTLS/Xray-core/tar.gz/${COMMIT}"
printf '%s  %s\n' "${SHA256}" "${WORK_DIR}/source.tar.gz" | sha256sum -c -
tar -xzf "${WORK_DIR}/source.tar.gz" -C "${WORK_DIR}"
SOURCE="${WORK_DIR}/Xray-core-${COMMIT}"
python3 - "${SOURCE}/proxy/trojan/server.go" <<'PY'
import pathlib,sys
path=pathlib.Path(sys.argv[1])
source=path.read_text()
old='firstLen, err := first.ReadFrom(conn)'
assert source.count(old)==1, 'Upstream Trojan handler changed; review patch before building'
path.write_text(source.replace(old, 'firstLen, err := readFirstTrojanRequest(conn, first)', 1))
PY
cp -- "${ROOT_DIR}/scripts/xray/trojan_first_read.go" "${ROOT_DIR}/scripts/xray/trojan_first_read_test.go" "${SOURCE}/proxy/trojan/"
mkdir -p -- "$(dirname -- "${OUTPUT}")"
(
  cd -- "${SOURCE}"
  export GOTOOLCHAIN=local CGO_ENABLED=0
  go test -p "${XRAY_BUILD_PARALLELISM:-2}" ./proxy/trojan -run '^TestVpsControllerTrojan' -count=1
  go build -p "${XRAY_BUILD_PARALLELISM:-2}" -trimpath -buildvcs=false \
    -ldflags='-s -w -X github.com/xtls/xray-core/core.build=vpsController-trojan-fragment-v1' \
    -o "${OUTPUT}" ./main
)
"${OUTPUT}" version | grep -F 'vpsController-trojan-fragment-v1' >/dev/null
(
  cd -- "$(dirname -- "${OUTPUT}")"
  sha256sum "$(basename -- "${OUTPUT}")" >"${OUTPUT}.sha256"
)
cp -- "${WORK_DIR}/source.tar.gz" "${OUTPUT}.source.tar.gz"
cp -- "${SOURCE}/LICENSE" "${OUTPUT}.LICENSE"
if [[ -n "${cache}" ]]; then
  mkdir -p -- "${cache}"
  cp -- "${OUTPUT}" "${cache}/xray"
  cp -- "${OUTPUT}.source.tar.gz" "${cache}/source.tar.gz"
  cp -- "${OUTPUT}.LICENSE" "${cache}/LICENSE"
  (cd "${cache}"; sha256sum xray source.tar.gz LICENSE >checksums.sha256)
fi
