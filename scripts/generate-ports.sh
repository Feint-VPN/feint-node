#!/usr/bin/env bash
# Backwards-compatible entry point. Keep port allocation in one place.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/ports.sh" randomize "$@"
