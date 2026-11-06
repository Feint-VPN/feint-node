#!/usr/bin/env bash
# Compatibility bootstrap: usable both from a checkout and as a downloaded file.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$PWD/install.sh}")" && pwd)"
IMPLEMENTATION="$ROOT/scripts/lib/install/install.sh"
if [[ -f "$IMPLEMENTATION" ]]; then
    exec bash "$IMPLEMENTATION" "$@"
fi
BRANCH=main
ARGS=("$@")
for ((i=0; i<${#ARGS[@]}; i++)); do
    if [[ "${ARGS[i]}" == --branch ]]; then
        ((i+1<${#ARGS[@]})) || { echo '--branch requires a value' >&2; exit 2; }
        BRANCH="${ARGS[i+1]}"
    fi
done
[[ "$BRANCH" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ ]] || { echo 'Invalid branch' >&2; exit 2; }
TEMP="$(mktemp)"
trap 'rm -f "$TEMP"' EXIT
curl --fail --silent --show-error --location --proto '=https' \
    "https://raw.githubusercontent.com/Feint-VPN/feint-node/$BRANCH/scripts/lib/install/install.sh" \
    --output "$TEMP"
bash "$TEMP" "$@"
