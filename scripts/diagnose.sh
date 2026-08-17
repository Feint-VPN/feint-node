#!/usr/bin/env bash
# Read-only checks for an installed Feint node.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/ports.sh"

INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_LINES=0
FAILURES=0

usage() {
    cat <<'EOF'
Usage: scripts/diagnose.sh [--dir DIR] [--logs LINES]

Runs read-only deployment checks. Logs are omitted by default and limited to
100 lines when requested. Configured secrets are redacted from log output.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --logs) LOG_LINES="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ "$LOG_LINES" =~ ^[0-9]+$ ]] && (( LOG_LINES <= 100 )) \
    || { printf 'Log lines must be between 0 and 100.\n' >&2; exit 2; }

ENV_FILE="$INSTALL_DIR/.env.local"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.yml"
[[ -f "$ENV_FILE" ]] || { printf 'Missing %s\n' "$ENV_FILE" >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { printf 'Missing %s\n' "$COMPOSE_FILE" >&2; exit 1; }
command -v docker >/dev/null || { printf 'Docker is not installed.\n' >&2; exit 1; }
command -v curl >/dev/null || { printf 'curl is not installed.\n' >&2; exit 1; }

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

ok() { printf '  OK    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }

printf 'Feint node diagnostics\n\n'

printf 'Configuration\n'
if port_require_unique_config "$ENV_FILE"; then
    ok 'Configured ports are valid and unique'
else
    fail 'Configured ports are invalid'
fi

api_port="$(env_get API_PORT "$ENV_FILE")"
api_secret="$(env_get API_SECRET "$ENV_FILE")"
[[ -n "$api_secret" ]] && ok 'API secret is configured' || fail 'API secret is missing'

printf '\nContainers\n'
if "${COMPOSE[@]}" ps; then
    ok 'Docker Compose is reachable'
else
    fail 'Docker Compose status failed'
fi

if "${COMPOSE[@]}" ps -q vpn-node-api | grep -q .; then
    ok 'vpn-node-api container exists'
else
    fail 'vpn-node-api container is missing'
fi
if "${COMPOSE[@]}" ps -q sing-box | grep -q .; then
    ok 'sing-box container exists'
else
    fail 'sing-box container is missing'
fi

printf '\nRuntime\n'
scheme=http
[[ "$(env_get API_USE_SSL "$ENV_FILE" true)" == true ]] && scheme=https
status_url="${scheme}://127.0.0.1:${api_port}/status"
if [[ -n "$api_secret" ]] && curl -skf --max-time 10 \
    -H "X-API-Secret: ${api_secret}" "$status_url" \
    | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok 'Authenticated API status is healthy'
else
    fail 'Authenticated API status failed'
fi

if "${COMPOSE[@]}" exec -T sing-box \
    sing-box check -c /opt/sing-box/config.json >/dev/null 2>&1; then
    ok 'sing-box configuration is valid'
else
    fail 'sing-box configuration check failed'
fi

printf '\nListeners\n'
if port_check_tool_available; then
    for key in "${PORT_KEYS[@]}"; do
        port="$(env_get "$key" "$ENV_FILE")"
        protocol="$(port_protocol "$key")"
        if port_is_in_use "$protocol" "$port"; then
            ok "$key $port/$protocol is listening"
        else
            warn "$key $port/$protocol has no visible host listener"
        fi
        if [[ "$key" == SHADOWSOCKS_PORT ]]; then
            if port_is_in_use udp "$port"; then
                ok "$key $port/udp is listening"
            else
                warn "$key $port/udp has no visible host listener"
            fi
        fi
    done
else
    warn 'Install iproute2 or net-tools to inspect listeners'
fi

printf '\nHost security\n'
if command -v sshd >/dev/null 2>&1; then
    ssh_ports="$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2 }' | paste -sd, -)"
    if [[ -z "$ssh_ports" ]]; then
        fail 'Could not read the effective SSH port'
    elif [[ ",$ssh_ports," == *,22,* ]]; then
        fail 'SSH still listens on the default port 22'
    else
        ok "SSH port: $ssh_ports"
    fi
else
    fail 'sshd is not installed'
fi

if command -v ufw >/dev/null 2>&1; then
    ufw_status="$(ufw status 2>/dev/null | sed -n '1p')"
    if [[ "$ufw_status" == 'Status: active' ]]; then
        ok "$ufw_status"
    elif [[ -n "$ufw_status" ]]; then
        fail "$ufw_status"
    else
        fail 'Could not read UFW status; try sudo'
    fi
else
    fail 'UFW is not installed'
fi

if (( LOG_LINES > 0 )); then
    printf '\nRecent logs (secrets redacted)\n'
    secrets=()
    for key in API_SECRET SHADOWSOCKS_PASSWORD CLASH_API_SECRET; do
        value="$(env_get "$key" "$ENV_FILE")"
        [[ -n "$value" ]] && secrets+=("$value")
    done
    while IFS= read -r line; do
        for secret in "${secrets[@]}"; do
            line="${line//"$secret"/<redacted>}"
        done
        printf '%s\n' "$line"
    done < <("${COMPOSE[@]}" logs --tail "$LOG_LINES" vpn-node-api sing-box 2>&1)
fi

printf '\n'
if (( FAILURES > 0 )); then
    printf 'Diagnostics failed (%d critical checks).\n' "$FAILURES"
    exit 1
fi
printf 'Diagnostics passed.\n'
