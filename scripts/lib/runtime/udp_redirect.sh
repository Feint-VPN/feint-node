#!/usr/bin/env bash
# Keep an existing public UDP port forwarded to its Xray listener across reboot.
set -euo pipefail

action="${1:-}"
public_port="${2:-}"
runtime_port="${3:-}"

for port in "$public_port" "$runtime_port"; do
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || {
        printf 'Invalid UDP port: %s\n' "$port" >&2
        exit 2
    }
done

rule=(-p udp --dport "$public_port" -j REDIRECT --to-ports "$runtime_port")
case "$action" in
    ensure)
        if ! iptables -t nat -C PREROUTING "${rule[@]}" 2>/dev/null; then
            iptables -t nat -I PREROUTING 1 "${rule[@]}"
        fi
        ;;
    remove)
        while iptables -t nat -C PREROUTING "${rule[@]}" 2>/dev/null; do
            iptables -t nat -D PREROUTING "${rule[@]}"
        done
        ;;
    *)
        printf 'Usage: %s ensure|remove PUBLIC_UDP_PORT RUNTIME_UDP_PORT\n' "$0" >&2
        exit 2
        ;;
esac
