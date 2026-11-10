#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export INSTALL_DIR="${INSTALL_DIR:-${ROOT_DIR}}"
exec "${INSTALL_DIR}/venv/bin/python" "${INSTALL_DIR}/api/assistant_worker.py" image-install nodera
