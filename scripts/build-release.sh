#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-${ROOT_DIR}/outputs/vps-control-release.tar.gz}"
WORK_DIR="$(mktemp -d)"
STAGE="${WORK_DIR}/vps-control-release"
trap 'rm -rf -- "${WORK_DIR}"' EXIT

for command_name in curl gzip node npm rsync sha256sum tar; do
  command -v "${command_name}" >/dev/null 2>&1 || { echo "Missing command: ${command_name}" >&2; exit 1; }
done
[[ "$(uname -s)" == "Linux" ]] || { echo "Release must be built on Linux." >&2; exit 1; }

mkdir -p "${STAGE}" "$(dirname -- "${OUTPUT}")"
rsync -a --delete \
  --exclude '.git/' --exclude '.idea/' --exclude '.runtime/' --exclude '.vinext/' \
  --exclude '.wrangler/' --exclude 'node_modules/' --exclude 'dist/' --exclude 'outputs/' \
  --exclude '.env*' --exclude 'venv/' \
  "${ROOT_DIR}/" "${STAGE}/"

# Mihomo is bundled into the prepared release. The server module uses the same
# pinned binary for config validation; client profiles never download an
# unverified core from the VPS.
MIHOMO_VERSION="1.19.28"
case "$(uname -m)" in
  x86_64|amd64)
    mihomo_asset="mihomo-linux-amd64-compatible-v${MIHOMO_VERSION}.gz"
    mihomo_sha256="70d01cfb8cb7bf7a92fd1af16cb4b9553d90bb4eecde3b5c4849103e27c80ddb"
    ;;
  aarch64|arm64)
    mihomo_asset="mihomo-linux-arm64-v${MIHOMO_VERSION}.gz"
    mihomo_sha256="2474450cd1c41dfa53036a54a4e85579f493d3af524d86c3d4b8e2b240b56cd2"
    ;;
  *)
    echo "Unsupported Mihomo release architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

mihomo_archive="${WORK_DIR}/${mihomo_asset}"
curl -fL --retry 4 --retry-all-errors \
  -o "${mihomo_archive}" \
  "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/${mihomo_asset}"
printf '%s  %s\n' "${mihomo_sha256}" "${mihomo_archive}" | sha256sum -c -

mkdir -p "${STAGE}/api/bin"
gzip -dc "${mihomo_archive}" >"${STAGE}/api/bin/mihomo"
chmod 0755 "${STAGE}/api/bin/mihomo"
"${STAGE}/api/bin/mihomo" -v | grep -F "Mihomo Meta v${MIHOMO_VERSION} " >/dev/null

python3 - "${STAGE}/protocol-images/mihomo/manifest.json" "${MIHOMO_VERSION}" <<'PY'
import json, sys
path, version = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    manifest = json.load(handle)
manifest["version"] = version
with open(path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

commit="${BUILD_COMMIT:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || printf manual)}"
app_version="${RELEASE_VERSION:-$(node -e 'const p=require(process.argv[1]); const v=String(p.version||"1.0.0").split("."); process.stdout.write("v"+[v[0]||"1",v[1]||"0",v[2]||"0"].join("."))' "${ROOT_DIR}/package.json")}"
[[ "${app_version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid release version: ${app_version}" >&2; exit 1; }

(
  cd "${ROOT_DIR}"
  export NEXT_PUBLIC_APP_VERSION="${app_version}"
  export NEXT_PUBLIC_BUILD_COMMIT="${commit}"
  npm ci --include=dev --include=optional --ignore-scripts
  npm run build
)

cp -a "${ROOT_DIR}/dist" "${STAGE}/dist"

node - "${ROOT_DIR}/package.json" "${STAGE}/package.json" <<'NODE'
const fs = require("fs");
const source = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const runtimeNames = [
  "next", "react", "react-dom", "vinext", "vite", "@cloudflare/vite-plugin",
  "@vitejs/plugin-react", "@vitejs/plugin-rsc", "react-server-dom-webpack",
];
const all = { ...(source.dependencies || {}), ...(source.devDependencies || {}) };
source.dependencies = Object.fromEntries(runtimeNames.map((name) => [name, all[name]]));
delete source.devDependencies;
fs.writeFileSync(process.argv[3], JSON.stringify(source, null, 2) + "\n");
NODE

(
  cd "${STAGE}"
  rm -f package-lock.json
  npm install --include=optional --ignore-scripts --package-lock=false
  node --input-type=module -e "await import('rolldown')"
)

printf 'version=%s\ncommit=%s\nbuilt_at=%s\nplatform=linux\narchitecture=%s\nmihomo=%s\n' \
  "${app_version}" "${commit}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(uname -m)" "${MIHOMO_VERSION}" \
  >"${STAGE}/.prebuilt-release"

(
  cd "${STAGE}"
  find . -type f ! -name release.sha256 -print0 | sort -z | xargs -0 sha256sum >release.sha256
)

tar -C "${WORK_DIR}" -czf "${OUTPUT}" vps-control-release
echo "Prepared release: ${OUTPUT}"
