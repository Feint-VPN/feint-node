#!/usr/bin/env bash
# Change deployed ports without editing Compose or sing-box JSON by hand.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/ports.sh"

INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$INSTALL_DIR/.env.local"
COMMAND="${1:-show}"
[[ $# -gt 0 ]] && shift
APPLY=false
RANDOMIZE=false
declare -A REQUESTED=()

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

usage() {
    cat <<'EOF'
Usage:
  scripts/ports.sh show [--dir DIR]
  scripts/ports.sh check [--dir DIR]
  scripts/ports.sh set [--api PORT] [--vless PORT] [--vmess PORT]
                       [--trojan PORT] [--hysteria2 PORT]
                       [--shadowsocks PORT] [--apply] [--dir DIR]
  scripts/ports.sh randomize [--apply] [--dir DIR]

`set` and `randomize` only stage `.env.local` unless `--apply` is given.
`--apply` updates the persisted sing-box config, recreates the API container,
restarts sing-box, health-checks the API, and restores the previous values if
that operation fails. No process is ever killed to free a port.
EOF
}

if [[ "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
    usage
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; ENV_FILE="$2/.env.local"; shift 2 ;;
        --api) REQUESTED[API_PORT]="$2"; shift 2 ;;
        --vless) REQUESTED[VLESS_PORT]="$2"; shift 2 ;;
        --vmess) REQUESTED[VMESS_PORT]="$2"; shift 2 ;;
        --trojan) REQUESTED[TROJAN_PORT]="$2"; shift 2 ;;
        --hysteria2) REQUESTED[HYSTERIA2_PORT]="$2"; shift 2 ;;
        --shadowsocks) REQUESTED[SHADOWSOCKS_PORT]="$2"; shift 2 ;;
        --apply) APPLY=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done

[[ -f "$ENV_FILE" ]] || die "Missing deployed configuration: $ENV_FILE"
port_check_tool_available || die "Port checks require iproute2 (ss) or net-tools (netstat)"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$INSTALL_DIR/docker-compose.yml")

show_ports() {
    local key value protocol
    for key in "${PORT_KEYS[@]}"; do
        value="$(env_get "$key" "$ENV_FILE")"
        protocol="$(port_protocol "$key")"
        printf '%-18s %5s/%s\n' "$key" "$value" "$protocol"
    done
}

check_ports() {
    local key value protocol details failed=0
    port_require_unique_config "$ENV_FILE" || failed=1
    for key in "${PORT_KEYS[@]}"; do
        value="$(env_get "$key" "$ENV_FILE")"
        protocol="$(port_protocol "$key")"
        details="$(port_listener_details "$protocol" "$value")"
        if [[ -n "$details" ]]; then
            printf '%s %s/%s is listening:\n%s\n' "$key" "$value" "$protocol" "$details"
        else
            printf '%s %s/%s has no host listener\n' "$key" "$value" "$protocol"
        fi
    done
    return "$failed"
}

randomize_ports() {
    local key protocol port used_key
    declare -A generated=()
    for key in "${PORT_KEYS[@]}"; do
        protocol="$(port_protocol "$key")"
        while :; do
            case "$key" in
                API_PORT) port="$(port_find_free tcp 8000 9000)" ;;
                *) port="$(port_find_free "$protocol" 10000 60000)" ;;
            esac
            used_key="${protocol}:${port}"
            [[ -z "${generated[$used_key]:-}" ]] && break
        done
        generated[$used_key]=1
        REQUESTED[$key]="$port"
    done
}

validate_requested() {
    local key old new protocol staged
    staged="$(mktemp "${ENV_FILE}.ports.XXXXXX")"
    cp "$ENV_FILE" "$staged"
    for key in "${!REQUESTED[@]}"; do
        new="${REQUESTED[$key]}"
        port_validate "$new" || { rm -f "$staged"; die "Invalid $key: $new"; }
        env_set "$key" "$new" "$staged"
    done
    port_require_unique_config "$staged" || { rm -f "$staged"; exit 1; }
    for key in "${!REQUESTED[@]}"; do
        old="$(env_get "$key" "$ENV_FILE")"; new="${REQUESTED[$key]}"; protocol="$(port_protocol "$key")"
        [[ "$old" == "$new" ]] || port_require_available "$protocol" "$new" "$key"
    done
    printf '%s\n' "$staged"
}

render_singbox_ports() {
    "${COMPOSE[@]}" exec -T vpn-node-api python - <<'PY'
import json, os, pathlib, tempfile

env = os.environ
ports = {
    "vless-reality-in": int(env["VLESS_PORT"]),
    "vmess-ws-in": int(env["VMESS_PORT"]),
    "trojan-in": int(env["TROJAN_PORT"]),
    "hysteria2-in": int(env["HYSTERIA2_PORT"]),
    "shadowsocks-in": int(env["SHADOWSOCKS_PORT"]),
}
path = pathlib.Path(env.get("CONFIG_PATH", "/opt/sing-box/config.json"))
config = json.loads(path.read_text())
found = set()
for inbound in config.get("inbounds", []):
    if inbound.get("tag") in ports:
        inbound["listen_port"] = ports[inbound["tag"]]
        found.add(inbound["tag"])
missing = set(ports) - found
if missing:
    raise SystemExit("persisted config is missing inbounds: " + ", ".join(sorted(missing)))
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
    json.dump(config, handle, separators=(",", ":"))
    handle.write("\\n")
    temporary = pathlib.Path(handle.name)
temporary.replace(path)
PY
}

apply_ports() {
    local env_backup config_backup health_url curl_args api_secret
    "${COMPOSE[@]}" ps -q vpn-node-api | grep -q . || die "vpn-node-api is not running; start the stack before applying port changes"
    env_backup="$(mktemp "${ENV_FILE}.backup.XXXXXX")"
    config_backup="$(mktemp "${ENV_FILE}.config.XXXXXX")"
    cp "$ENV_FILE" "$env_backup"
    "${COMPOSE[@]}" exec -T vpn-node-api cat "${CONFIG_PATH:-/opt/sing-box/config.json}" > "$config_backup"
    if ! "${COMPOSE[@]}" up -d --force-recreate vpn-node-api \
        || ! render_singbox_ports \
        || ! "${COMPOSE[@]}" restart sing-box; then
        cp "$env_backup" "$ENV_FILE"
        "${COMPOSE[@]}" cp "$config_backup" "vpn-node-api:${CONFIG_PATH:-/opt/sing-box/config.json}" || true
        "${COMPOSE[@]}" up -d --force-recreate vpn-node-api || true
        "${COMPOSE[@]}" restart sing-box || true
        die "Port change failed; previous configuration was restored"
    fi
    if [[ "$(env_get API_USE_SSL "$ENV_FILE" true)" == true ]]; then
        health_url="https://127.0.0.1:$(env_get API_PORT "$ENV_FILE")/health"; curl_args=(-sk)
    else
        health_url="http://127.0.0.1:$(env_get API_PORT "$ENV_FILE")/health"; curl_args=(-s)
    fi
    api_secret="$(env_get API_SECRET "$ENV_FILE")"
    if ! curl "${curl_args[@]}" --fail -H "X-API-Secret: ${api_secret}" "$health_url" >/dev/null; then
        cp "$env_backup" "$ENV_FILE"
        "${COMPOSE[@]}" cp "$config_backup" "vpn-node-api:${CONFIG_PATH:-/opt/sing-box/config.json}" || true
        "${COMPOSE[@]}" up -d --force-recreate vpn-node-api || true
        "${COMPOSE[@]}" restart sing-box || true
        die "Health check failed; previous configuration was restored"
    fi
    rm -f "$env_backup" "$config_backup"
    info "Port change applied successfully"
}

case "$COMMAND" in
    show) show_ports ;;
    check) check_ports ;;
    set|randomize)
        [[ "$COMMAND" == randomize ]] && randomize_ports
        (( ${#REQUESTED[@]} > 0 )) || die "No port values were provided"
        staged="$(validate_requested)"
        if [[ "$APPLY" == true ]]; then
            cp "$staged" "$ENV_FILE"; rm -f "$staged"; apply_ports
        else
            cp "$staged" "$ENV_FILE"; rm -f "$staged"
            info "Ports staged in $ENV_FILE. Run '$0 apply --dir $INSTALL_DIR' to render and restart."
        fi
        show_ports
        ;;
    apply) apply_ports ;;
    *) usage; exit 1 ;;
esac
