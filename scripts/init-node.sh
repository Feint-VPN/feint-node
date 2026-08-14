#!/usr/bin/env bash
# Compatibility entrypoint for the former API-driven initializer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: scripts/init-node.sh <domain> <email> <server_ip>

This compatibility command delegates to install.sh, the supported installer.
The server IP is retained for callers using the old interface; install.sh
detects the host network configuration itself.

Example:
  sudo ./scripts/init-node.sh vpn.example.com admin@example.com 203.0.113.42
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -ne 3 ]]; then
    usage >&2
    exit 2
fi

DOMAIN="$1"
EMAIL="$2"
SERVER_IP="$3"

[[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] \
    || { printf 'Invalid domain: %s\n' "$DOMAIN" >&2; exit 2; }
[[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] \
    || { printf 'Invalid email: %s\n' "$EMAIL" >&2; exit 2; }
[[ "$SERVER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] \
    || { printf 'Invalid IPv4 address: %s\n' "$SERVER_IP" >&2; exit 2; }

printf 'The API-driven initializer is retired; running the supported installer.\n'
printf 'The supplied server IP (%s) is not needed by install.sh.\n' "$SERVER_IP"

if [[ "${EUID}" -ne 0 ]]; then
    exec sudo bash "$PROJECT_DIR/install.sh" --domain "$DOMAIN" --email "$EMAIL"
fi

exec bash "$PROJECT_DIR/install.sh" --domain "$DOMAIN" --email "$EMAIL"
