#!/usr/bin/env bash
# Shared port configuration helpers.  This file intentionally has no side
# effects when sourced so installers, update scripts, and admin commands use
# the same rules.

PORT_KEYS=(API_PORT VLESS_PORT VMESS_PORT TROJAN_PORT HYSTERIA2_PORT SHADOWSOCKS_PORT)

port_protocol() {
    case "$1" in
        HYSTERIA2_PORT) printf '%s\n' udp ;;
        *)              printf '%s\n' tcp ;;
    esac
}

port_validate() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}

port_check_tool_available() {
    command -v ss >/dev/null 2>&1 || command -v netstat >/dev/null 2>&1
}

env_get() {
    local key="$1" file="$2" fallback="${3:-}"
    local value
    value=$(sed -n "s/^${key}=//p" "$file" 2>/dev/null | tail -n 1 || true)
    printf '%s\n' "${value:-$fallback}"
}

env_set() {
    local key="$1" value="$2" file="$3" tmp
    tmp="$(mktemp "${file}.tmp.XXXXXX")"
    if [[ -f "$file" ]]; then
        awk -v key="$key" -v value="$value" '
            $0 ~ "^" key "=" { print key "=" value; seen=1; next }
            { print }
            END { if (!seen) print key "=" value }
        ' "$file" > "$tmp"
    else
        printf '%s=%s\n' "$key" "$value" > "$tmp"
    fi
    mv "$tmp" "$file"
}

port_listener_details() {
    local protocol="$1" port="$2" flags
    [[ "$protocol" == tcp ]] && flags='-ltnp' || flags='-lunp'
    if command -v ss >/dev/null 2>&1; then
        ss -H $flags "sport = :$port" 2>/dev/null || true
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tulnp 2>/dev/null | awk -v proto="$protocol" -v port=":$port" '$1 ~ proto && index($4, port) { print }'
    fi
}

port_is_in_use() {
    [[ -n "$(port_listener_details "$1" "$2")" ]]
}

port_require_available() {
    local protocol="$1" port="$2" label="${3:-port $2/$1}" details
    port_validate "$port" || { printf 'Invalid %s: %s\n' "$label" "$port" >&2; return 1; }
    port_check_tool_available || { printf 'Cannot check %s: install iproute2 (ss) or net-tools (netstat)\n' "$label" >&2; return 1; }
    if details="$(port_listener_details "$protocol" "$port")" && [[ -n "$details" ]]; then
        printf '%s is already in use:\n%s\n' "$label ($port/$protocol)" "$details" >&2
        return 1
    fi
}

port_find_free() {
    local protocol="$1" min="$2" max="$3" port attempts=0
    (( min >= 1 && max <= 65535 && min <= max )) || return 1
    while (( attempts < 200 )); do
        port=$(( min + RANDOM % (max - min + 1) ))
        if ! port_is_in_use "$protocol" "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
        ((attempts += 1))
    done
    return 1
}

port_find_free_unique() {
    local protocol="$1" min="$2" max="$3" port attempts=0 reserved="" collision=false
    shift 3
    while (( attempts < 200 )); do
        port="$(port_find_free "$protocol" "$min" "$max")" || return 1
        for reserved in "$@"; do
            if [[ "$port" == "$reserved" ]]; then
                collision=true
                break
            fi
        done
        if [[ "$collision" == false ]]; then
            printf '%s\n' "$port"
            return 0
        fi
        collision=false
        ((attempts += 1))
    done
    return 1
}

port_find_free_both() {
    local min="$1" max="$2" port attempts=0
    shift 2
    while (( attempts < 200 )); do
        port="$(port_find_free_unique tcp "$min" "$max" "$@")" || return 1
        if ! port_is_in_use udp "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
        ((attempts += 1))
    done
    return 1
}

port_require_unique_config() {
    local env_file="$1" key protocol value other other_protocol other_value
    for key in "${PORT_KEYS[@]}"; do
        value="$(env_get "$key" "$env_file")"
        port_validate "$value" || { printf '%s is invalid: %s\n' "$key" "$value" >&2; return 1; }
        protocol="$(port_protocol "$key")"
        for other in "${PORT_KEYS[@]}"; do
            [[ "$key" < "$other" ]] || continue
            other_protocol="$(port_protocol "$other")"
            [[ "$protocol" == "$other_protocol" ]] || continue
            other_value="$(env_get "$other" "$env_file")"
            if [[ "$value" == "$other_value" ]]; then
                printf '%s and %s both use %s/%s\n' "$key" "$other" "$value" "$protocol" >&2
                return 1
            fi
        done
    done
    if [[ "$(env_get HYSTERIA2_PORT "$env_file")" == "$(env_get SHADOWSOCKS_PORT "$env_file")" ]]; then
        printf 'HYSTERIA2_PORT and SHADOWSOCKS_PORT use the same UDP port\n' >&2
        return 1
    fi
}
