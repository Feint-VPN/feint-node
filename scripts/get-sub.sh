#!/usr/bin/env bash
# get-sub.sh — Fetch a Hiddify-compatible subscription URL for a VPN user.
#
# Usage:
#   ./scripts/get-sub.sh <username>
#   ./scripts/get-sub.sh <username> <server_domain_override>
#
# The script reads SERVER_DOMAIN and API_PORT from .env.local.
# The returned payload is base64-encoded; pass it to Hiddify as a subscription URL,
# or decode it locally to see the raw proxy URIs.
#
# Example:
#   ./scripts/get-sub.sh alice
#   ./scripts/get-sub.sh alice hy2.example.com
#
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.local}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: env file '$ENV_FILE' not found." >&2
    echo "Set ENV_FILE=... or run from the project root." >&2
    exit 1
fi

# Read values from env file
_get_env() {
    grep -E "^${1}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r'
}

API_SECRET="$(_get_env API_SECRET)"
SERVER_DOMAIN="$(_get_env SERVER_DOMAIN)"
API_PORT="$(_get_env API_PORT || true)"
API_PORT="${API_PORT:-8000}"
SUB_ENABLED="$(_get_env SUBSCRIPTION_ENABLED || true)"
SUB_ENABLED="${SUB_ENABLED:-false}"

USERNAME="${1:-}"
DOMAIN_OVERRIDE="${2:-}"
DISPLAY_DOMAIN="${DOMAIN_OVERRIDE:-$SERVER_DOMAIN}"

if [[ -z "$USERNAME" ]]; then
    echo "Usage: $0 <username> [server_domain_override]" >&2
    exit 1
fi

if [[ "$SUB_ENABLED" != "true" ]]; then
    echo "Error: SUBSCRIPTION_ENABLED is not 'true' in $ENV_FILE." >&2
    echo "Set SUBSCRIPTION_ENABLED=true and restart the API container." >&2
    exit 1
fi

if [[ -z "$SERVER_DOMAIN" ]]; then
    echo "Error: SERVER_DOMAIN not found in $ENV_FILE." >&2
    exit 1
fi

SUB_URL="https://${SERVER_DOMAIN}:${API_PORT}/sub/${USERNAME}"
if [[ -n "$DOMAIN_OVERRIDE" ]]; then
    SUB_URL="${SUB_URL}?server_domain=${DOMAIN_OVERRIDE}"
fi

echo "========================================="
echo "  Hiddify Subscription Info"
echo "========================================="
echo "User:   $USERNAME"
echo "Domain: $DISPLAY_DOMAIN"
echo ""
echo "Subscription URL (add to Hiddify):"
echo "  $SUB_URL"
echo "========================================="
echo ""
echo "Decoded proxy URIs:"
curl -fsSL "$SUB_URL" | base64 -d
echo ""
