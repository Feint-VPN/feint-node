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

Without `--apply`, `set` and `randomize` only print the validated port plan.
`--apply` atomically updates `.env.local` and the persisted sing-box config,
restarts the affected services, and restores both files if the operation fails.
No process is ever killed to free a port.
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
    local file="${1:-$ENV_FILE}" key value protocol
    for key in "${PORT_KEYS[@]}"; do
        value="$(env_get "$key" "$file")"
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
    handle.write("\n")
    temporary = pathlib.Path(handle.name)
temporary.replace(path)
PY
}

wait_for_status() {
    local scheme=http response=""
    [[ "$(env_get API_USE_SSL "$ENV_FILE" true)" == true ]] && scheme=https
    local url="${scheme}://127.0.0.1:$(env_get API_PORT "$ENV_FILE")/status"
    local api_secret="$(env_get API_SECRET "$ENV_FILE")"
    for _ in {1..60}; do
        response="$(curl -skf -H "X-API-Secret: ${api_secret}" "$url" || true)"
        if grep -q '"status":"ok"' <<< "$response"; then
            return 0
        fi
        sleep 1
    done
    printf 'Last API status: %s\n' "${response:-unavailable}" >&2
    return 1
}

restore_ports() {
    local env_backup="$1" config_backup="$2" failed=false
    cp "$env_backup" "$ENV_FILE" || failed=true
    "${COMPOSE[@]}" up -d --force-recreate vpn-node-api || failed=true
    "${COMPOSE[@]}" cp "$config_backup" "vpn-node-api:${CONFIG_PATH:-/opt/sing-box/config.json}" || failed=true
    "${COMPOSE[@]}" exec -T --user root vpn-node-api sh -c \
        "chown 1000:1000 '${CONFIG_PATH:-/opt/sing-box/config.json}' && chmod 600 '${CONFIG_PATH:-/opt/sing-box/config.json}'" || failed=true
    "${COMPOSE[@]}" restart sing-box || failed=true
    [[ "$failed" == false ]]
}

apply_ports() {
    local staged="$1" env_backup config_backup
    "${COMPOSE[@]}" ps -q vpn-node-api | grep -q . || die "vpn-node-api is not running; start the stack before applying port changes"
    env_backup="$(mktemp "${ENV_FILE}.backup.XXXXXX")"
    config_backup="$(mktemp "${ENV_FILE}.config.XXXXXX")"
    cp "$ENV_FILE" "$env_backup"
    "${COMPOSE[@]}" exec -T vpn-node-api cat "${CONFIG_PATH:-/opt/sing-box/config.json}" > "$config_backup"
    if ! cp "$staged" "$ENV_FILE" \
        || ! "${COMPOSE[@]}" up -d --force-recreate vpn-node-api \
        || ! render_singbox_ports \
        || ! "${COMPOSE[@]}" exec -T sing-box sing-box check -c /opt/sing-box/config.json \
        || ! "${COMPOSE[@]}" restart sing-box \
        || ! wait_for_status; then
        if restore_ports "$env_backup" "$config_backup"; then
            rm -f "$staged" "$env_backup" "$config_backup"
            die "Port change failed; previous configuration was restored"
        fi
        die "Port change failed and rollback was incomplete; backups were preserved"
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
            apply_ports "$staged"
        else
            info "Proposed ports (not applied):"
            show_ports "$staged"
        fi
        rm -f "$staged"
        if [[ "$APPLY" == true ]]; then
            show_ports
        fi
        ;;
    *) usage; exit 1 ;;
esac
