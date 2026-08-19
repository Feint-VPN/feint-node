#!/usr/bin/env bash
# Lock the host to Feint ports and move SSH to a verified non-default port.
set -Eeuo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$INSTALL_DIR/.env.local"
NEW_SSH_PORT=""
SSH_PUBLIC_KEY=""
NO_CONFIRM=false

usage() {
    cat <<EOF
Usage: sudo $0 [--ssh-port PORT] [--ssh-public-key KEY] [--no-confirm] [--dir DIR]

The SSH port is always changed. Without --ssh-port, a free random port is used.
Keep this terminal open and confirm a second SSH connection when prompted.
--no-confirm is intended for SDK installation and requires an explicit port.
--ssh-public-key installs the SDK-generated key before password login is disabled.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssh-port) [[ $# -ge 2 ]] || die "--ssh-port requires a value"; NEW_SSH_PORT="$2"; shift 2 ;;
        --ssh-public-key) [[ $# -ge 2 ]] || die "--ssh-public-key requires a value"; SSH_PUBLIC_KEY="$2"; shift 2 ;;
        --no-confirm) NO_CONFIRM=true; shift ;;
        --dir) [[ $# -ge 2 ]] || die "--dir requires a value"; INSTALL_DIR="$2"; ENV_FILE="$2/.env.local"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

if [[ "$NO_CONFIRM" == true && -z "$NEW_SSH_PORT" ]]; then
    die "--no-confirm requires --ssh-port"
fi
if [[ "$NO_CONFIRM" == true && -z "$SSH_PUBLIC_KEY" ]]; then
    die "--no-confirm requires --ssh-public-key"
fi

[[ $EUID -eq 0 ]] || die "Run as root"
[[ -f "$ENV_FILE" ]] || die "Missing runtime configuration: $ENV_FILE"
[[ -f /etc/ssh/sshd_config ]] || die "OpenSSH server is not installed"
command -v sshd >/dev/null || die "sshd is required"
command -v systemctl >/dev/null || die "systemd is required"

source "$INSTALL_DIR/scripts/lib/ports.sh"
source "$INSTALL_DIR/scripts/lib/firewall.sh"
port_check_tool_available || die "Port checks require iproute2 (ss)"
port_require_unique_config "$ENV_FILE" || exit 1

API_PORT="$(env_get API_PORT "$ENV_FILE")"
VLESS_PORT="$(env_get VLESS_PORT "$ENV_FILE")"
VMESS_PORT="$(env_get VMESS_PORT "$ENV_FILE")"
TROJAN_PORT="$(env_get TROJAN_PORT "$ENV_FILE")"
HYSTERIA2_PORT="$(env_get HYSTERIA2_PORT "$ENV_FILE")"
SHADOWSOCKS_PORT="$(env_get SHADOWSOCKS_PORT "$ENV_FILE")"
command -v docker >/dev/null || die "Docker is required"
docker inspect vpn-node-api >/dev/null 2>&1 || die "vpn-node-api must be running before firewall setup"
ssh_connection="${SSH_CONNECTION:-}"
OLD_SSH_PORT="${ssh_connection##* }"
if ! port_validate "$OLD_SSH_PORT"; then
    OLD_SSH_PORT="$(sshd_setting port)" || die "Could not determine the current SSH port"
fi
port_validate "$OLD_SSH_PORT" || die "Invalid current SSH port: $OLD_SSH_PORT"

reserved=(80 "$OLD_SSH_PORT" "$API_PORT" "$VLESS_PORT" "$VMESS_PORT" "$TROJAN_PORT" "$HYSTERIA2_PORT" "$SHADOWSOCKS_PORT")
if [[ -z "$NEW_SSH_PORT" ]]; then
    NEW_SSH_PORT="$(port_find_free_unique tcp 20000 60000 "${reserved[@]}")" \
        || die "Could not find a free SSH port"
else
    port_validate "$NEW_SSH_PORT" \
        || die "SSH port must be between 1 and 65535"
    [[ "$NEW_SSH_PORT" != "$OLD_SSH_PORT" ]] || die "SSH port must change"
    for port in "${reserved[@]}"; do
        [[ "$NEW_SSH_PORT" != "$port" ]] || die "SSH port conflicts with reserved port $port"
    done
    port_require_available tcp "$NEW_SSH_PORT" "SSH port" || exit 1
fi

if ! command -v ufw >/dev/null; then
    info "Installing UFW"
    apt-get update
    apt-get install -y --no-install-recommends ufw
fi
if grep -q '^IPV6=' /etc/default/ufw; then
    sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
else
    printf 'IPV6=yes\n' >> /etc/default/ufw
fi

SSH_DROPIN=/etc/ssh/sshd_config.d/00-feint-port.conf
SSH_BACKUP_DIR=/etc/ssh/feint-backups
SSH_BACKUP="$SSH_BACKUP_DIR/sshd-$(date -u '+%Y%m%d-%H%M%S').tar"
mkdir -p "$SSH_BACKUP_DIR"
if [[ -d /etc/ssh/sshd_config.d ]]; then
    tar -C / -cpf "$SSH_BACKUP" etc/ssh/sshd_config etc/ssh/sshd_config.d
else
    tar -C / -cpf "$SSH_BACKUP" etc/ssh/sshd_config
fi

SSH_CHANGED=false
FIREWALL_CHANGED=false

restart_ssh() {
    systemctl daemon-reload
    if systemctl is-active --quiet ssh.socket; then
        systemctl restart ssh.socket
    elif systemctl list-unit-files ssh.service >/dev/null 2>&1; then
        systemctl restart ssh.service
    else
        systemctl restart sshd.service
    fi
}

restore_ssh() {
    rm -f "$SSH_DROPIN"
    tar -C / -xpf "$SSH_BACKUP"
    sshd -t
}

rollback() {
    local status=$?
    (( status != 0 )) || status=1
    trap - ERR INT TERM HUP
    set +e
    error "Firewall setup failed; restoring SSH on port $OLD_SSH_PORT"
    [[ "$SSH_CHANGED" == false ]] || restore_ssh
    [[ "$FIREWALL_CHANGED" == false ]] || firewall_apply "$ENV_FILE" "$OLD_SSH_PORT"
    restart_ssh
    exit "$status"
}
trap rollback ERR INT TERM HUP

info "Moving SSH from $OLD_SSH_PORT to $NEW_SSH_PORT"
mkdir -p /etc/ssh/sshd_config.d
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if [[ -n "$SSH_PUBLIC_KEY" ]] && ! grep -qxF "$SSH_PUBLIC_KEY" /root/.ssh/authorized_keys; then
    printf '%s\n' "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
[[ -s /root/.ssh/authorized_keys ]] || die "Password authentication cannot be disabled without an authorized key"
SSH_CHANGED=true
while IFS= read -r -d '' config; do
    sed -i -E 's/^([[:space:]]*)Port([[:space:]]+)/# Feint disabled Port /' "$config"
done < <(find /etc/ssh -maxdepth 2 -type f \( -name sshd_config -o -path '/etc/ssh/sshd_config.d/*.conf' \) -print0)
if ! grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config; then
    sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
fi
cat > "$SSH_DROPIN" <<EOF
Port $NEW_SSH_PORT
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF

sshd -t
mapfile -t effective_ports < <(sshd -T | awk '$1 == "port" { print $2 }')
if [[ ${#effective_ports[@]} -ne 1 || "${effective_ports[0]}" != "$NEW_SSH_PORT" ]]; then
    error "The effective SSH configuration did not select only $NEW_SSH_PORT"
    false
fi
[[ "$(sshd_setting passwordauthentication)" == no ]] \
    || die "Password authentication is still enabled"

info "Closing host ports outside the Feint allowlist"
FIREWALL_CHANGED=true
firewall_apply "$ENV_FILE" "$NEW_SSH_PORT" "$OLD_SSH_PORT"
restart_ssh

for _ in {1..10}; do
    port_is_in_use tcp "$NEW_SSH_PORT" && break
    sleep 1
done
if ! port_is_in_use tcp "$NEW_SSH_PORT"; then
    error "SSH is not listening on $NEW_SSH_PORT"
    false
fi

if [[ "$NO_CONFIRM" == false ]]; then
    warn "Keep this terminal open. In a second terminal, connect with:"
    echo "  ssh -p $NEW_SSH_PORT <user>@<server>"
    echo
    printf 'Type CONFIRM after the new SSH session works: '
    read -r confirmation </dev/tty
    if [[ "$confirmation" != CONFIRM ]]; then
        error "The new SSH connection was not confirmed"
        false
    fi
fi

ufw --force delete allow "$OLD_SSH_PORT/tcp" >/dev/null
trap - ERR INT TERM HUP

success "SSH moved to $NEW_SSH_PORT"
success "All non-Feint host ports are closed"
ufw status verbose
echo
warn "SSH backup: $SSH_BACKUP"
