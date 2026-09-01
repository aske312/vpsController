#!/usr/bin/env bash
set -Eeuo pipefail
QUIC_MODULE=tuic QUIC_DEFAULT_PORT=10443 source "$(dirname "$0")/../transport-hysteria2/install.sh"
