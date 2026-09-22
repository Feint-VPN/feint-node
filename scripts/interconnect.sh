#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$INSTALL_DIR"
[[ -f .env.local ]] || { echo "Missing .env.local" >&2; exit 1; }
[[ -f interconnect.json ]] || { echo "Missing interconnect.json" >&2; exit 1; }

INTERCONNECT_NODE_VOLUME="$(
    docker compose --env-file .env.local -f docker-compose.yml config --format json |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["volumes"]["sing-box-data"]["name"])'
)"
docker volume inspect "$INTERCONNECT_NODE_VOLUME" >/dev/null
export INTERCONNECT_NODE_VOLUME

docker compose --env-file .env.local -p "$(basename "$INSTALL_DIR")-interconnect" \
    -f docker-compose.interconnect.yml "$@"
