#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
exec bash "${SOURCE}/agent-source.sh" standalone "$@"
