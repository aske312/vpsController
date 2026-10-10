#!/usr/bin/env bash
set -Eeuo pipefail
action="${1:-}"
[[ "${action}" == install || "${action}" == standalone ]] || { echo 'Unknown Agent installation mode' >&2; exit 2; }
shift
[[ ${EUID} -eq 0 ]] || { echo 'Agent installation requires root' >&2; exit 1; }
REPOSITORY="${VPS_CONTROL_AGENT_REPOSITORY:-https://github.com/aske312/vpsController}"
BRANCH=agent
export DEBIAN_FRONTEND=noninteractive
if ! command -v curl >/dev/null || ! command -v tar >/dev/null || ! command -v python3 >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=300 update
  apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl tar python3
fi
work="$(mktemp -d /tmp/vps-agent-source.XXXXXX)"
trap 'rm -rf -- "${work}"' EXIT
curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
  --connect-timeout 15 --max-time 180 --output "${work}/agent.tar.gz" \
  "${REPOSITORY}/archive/refs/heads/${BRANCH}.tar.gz"
tar -xzf "${work}/agent.tar.gz" -C "${work}"
roots=("${work}"/*/protocol-images/relay-agent)
[[ ${#roots[@]} -eq 1 && -f "${roots[0]}/manifest.json" && -f "${roots[0]}/install.sh" && -f "${roots[0]}/agent.py" && -f "${roots[0]}/credentials.py" ]] \
  || { echo 'Agent branch archive is incomplete' >&2; exit 1; }
python3 - "${roots[0]}" <<'PY'
import json, py_compile, re, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root / 'manifest.json').read_text())
if manifest.get('id') != 'relay-agent' or not re.fullmatch(r'\d+\.\d+\.\d+', manifest.get('version', '')):
    raise SystemExit('Invalid Agent manifest')
py_compile.compile(str(root / 'agent.py'), doraise=True)
version = subprocess.check_output([sys.executable, str(root / 'agent.py'), '--version'], text=True).strip()
if version != manifest['version']: raise SystemExit('Agent version does not match its manifest')
PY
if [[ "${action}" == standalone ]]; then
  VPS_CONTROL_BRANCH=agent VPS_CONTROL_REPOSITORY="${REPOSITORY}" bash "${roots[0]}/../../scripts/install-agent.sh" "$@"
else
  bash "${roots[0]}/install.sh" "$@"
fi
