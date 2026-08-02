#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-${ROOT_DIR}/outputs/vps-control-release.tar.gz}"
WORK_DIR="$(mktemp -d)"
STAGE="${WORK_DIR}/vps-control-release"
trap 'rm -rf -- "${WORK_DIR}"' EXIT

for command_name in node npm rsync sha256sum tar; do
  command -v "${command_name}" >/dev/null 2>&1 || { echo "Missing command: ${command_name}" >&2; exit 1; }
done
[[ "$(uname -s)" == "Linux" ]] || { echo "Release must be built on Linux." >&2; exit 1; }

mkdir -p "${STAGE}" "$(dirname -- "${OUTPUT}")"
rsync -a --delete \
  --exclude '.git/' --exclude '.idea/' --exclude '.runtime/' --exclude '.vinext/' \
  --exclude '.wrangler/' --exclude 'node_modules/' --exclude 'dist/' --exclude 'outputs/' \
  --exclude '.env*' --exclude 'venv/' \
  "${ROOT_DIR}/" "${STAGE}/"

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

# Install only packages needed by `vinext start` into the prepared Linux release.
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
  # The runtime stack uses platform-specific native packages (Rolldown, Sharp,
  # Lightning CSS). Omitting optional dependencies produces a release that
  # builds successfully but cannot start on the target Linux host.
  npm install --include=optional --ignore-scripts --package-lock=false
  node --input-type=module -e "await import('rolldown')"
)

printf 'version=%s\ncommit=%s\nbuilt_at=%s\nplatform=linux\n' "${app_version}" "${commit}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${STAGE}/.prebuilt-release"
(
  cd "${STAGE}"
  find . -type f ! -name release.sha256 -print0 | sort -z | xargs -0 sha256sum >release.sha256
)
tar -C "${WORK_DIR}" -czf "${OUTPUT}" vps-control-release
echo "Prepared release: ${OUTPUT}"
