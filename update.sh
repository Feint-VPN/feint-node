#!/usr/bin/env bash
# Compatibility entry point for existing installations.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/scripts/lib/update/update.sh" "$@"
