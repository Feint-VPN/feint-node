#!/bin/bash
# Feint VPN Node in-place updater.
set -Eeuo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}$*${NC}\n"; }

INSTALL_DIR="/opt/vpn-node"
BRANCH=""
FORCE=false

usage() {
    cat <<EOF
Usage: $0 [--dir DIR] [--branch BRANCH] [--force]

  --dir DIR        Install directory (default: /opt/vpn-node)
  --branch BRANCH  Target branch (default: current branch)
  --force          Overwrite tracked local changes
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir) [[ $# -ge 2 ]] || die "--dir requires a value"; INSTALL_DIR="$2"; shift 2 ;;
        --branch) [[ $# -ge 2 ]] || die "--branch requires a value"; BRANCH="$2"; shift 2 ;;
        --force) FORCE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0"
command -v git >/dev/null || die "git is required"
command -v docker >/dev/null || die "docker is required"
docker compose version >/dev/null || die "docker compose is required"
[[ -d "$INSTALL_DIR/.git" ]] || die "No installation found at $INSTALL_DIR"
[[ -f "$INSTALL_DIR/.env.local" ]] || die "Missing $INSTALL_DIR/.env.local"

cd "$INSTALL_DIR"
ENV_FILE="$INSTALL_DIR/.env.local"
source "$INSTALL_DIR/scripts/lib/ports.sh"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$INSTALL_DIR/docker-compose.yml")

detect_branch() {
    git symbolic-ref --quiet --short HEAD 2>/dev/null \
        || git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||' \
        || echo main
}

wait_for_status() {
    local scheme=http
    [[ "$(env_get API_USE_SSL "$ENV_FILE" true)" == true ]] && scheme=https
    local url="${scheme}://127.0.0.1:$(env_get API_PORT "$ENV_FILE" 8337)/status"
    local secret="$(env_get API_SECRET "$ENV_FILE")"
    for _ in {1..60}; do
        if curl -skf -H "X-API-Secret: ${secret}" "$url" \
            | grep -q '"status":"ok"'; then
            return 0
        fi
        sleep 1
    done
    return 1
}

restore_image() {
    local image_id="$1" image_ref="$2"
    [[ -z "$image_id" ]] || docker tag "$image_id" "$image_ref"
}

rollback() {
    local status=$?
    trap - ERR
    set +e
    error "Update failed; restoring the previous deployment"
    git reset --hard "$OLD_COMMIT"
    cp "$ENV_BACKUP" "$ENV_FILE"
    restore_image "$OLD_NODE_IMAGE" "$NODE_IMAGE"
    restore_image "$OLD_SINGBOX_IMAGE" "$SINGBOX_IMAGE"
    restore_image "$OLD_CERTBOT_IMAGE" "$CERTBOT_IMAGE"
    "${COMPOSE[@]}" up -d --no-build --remove-orphans
    "${COMPOSE[@]}" cp "$CONFIG_BACKUP" "vpn-node-api:${CONFIG_PATH:-/opt/sing-box/config.json}"
    "${COMPOSE[@]}" exec -T --user root vpn-node-api sh -c \
        "chown 1000:1000 '${CONFIG_PATH:-/opt/sing-box/config.json}' && chmod 600 '${CONFIG_PATH:-/opt/sing-box/config.json}'"
    "${COMPOSE[@]}" restart sing-box
    if wait_for_status; then
        success "Previous deployment restored"
    else
        error "Rollback incomplete; inspect $ENV_BACKUP and $CONFIG_BACKUP"
    fi
    exit "$status"
}

sync_template() {
    local helper=/tmp/feint-sync-singbox.py template=/tmp/feint-singbox-template.json
    "${COMPOSE[@]}" cp "$INSTALL_DIR/scripts/sync-singbox.py" "vpn-node-api:$helper"
    "${COMPOSE[@]}" cp "$INSTALL_DIR/templates/sing-box.json.tpl" "vpn-node-api:$template"
    "${COMPOSE[@]}" exec -T vpn-node-api python "$helper" \
        "$template" "${CONFIG_PATH:-/opt/sing-box/config.json}" \
        "${CONFIG_PATH:-/opt/sing-box/config.json}.next"
    "${COMPOSE[@]}" exec -T sing-box sing-box check \
        -c "${CONFIG_PATH:-/opt/sing-box/config.json}.next"
    "${COMPOSE[@]}" exec -T vpn-node-api mv \
        "${CONFIG_PATH:-/opt/sing-box/config.json}.next" \
        "${CONFIG_PATH:-/opt/sing-box/config.json}"
    "${COMPOSE[@]}" restart sing-box
}

header "Feint VPN Node Update"

BRANCH="${BRANCH:-$(detect_branch)}"
if [[ -n "$(git status --porcelain --untracked-files=no)" && "$FORCE" != true ]]; then
    git status --short --untracked-files=no
    die "Tracked changes detected; use --force to overwrite them"
fi

"${COMPOSE[@]}" ps -q vpn-node-api | grep -q . \
    || die "vpn-node-api must be running before an update"

OLD_COMMIT="$(git rev-parse HEAD)"
ENV_BACKUP="$(mktemp "${ENV_FILE}.update.XXXXXX")"
CONFIG_BACKUP="$(mktemp "${ENV_FILE}.config.XXXXXX")"
cp "$ENV_FILE" "$ENV_BACKUP"
"${COMPOSE[@]}" exec -T vpn-node-api cat \
    "${CONFIG_PATH:-/opt/sing-box/config.json}" > "$CONFIG_BACKUP"

NODE_IMAGE="$(env_get NODE_IMAGE "$ENV_FILE" ghcr.io/feint-vpn/feint-node:latest)"
SINGBOX_IMAGE="$(env_get SINGBOX_IMAGE "$ENV_FILE" ghcr.io/feint-vpn/feint-sing-box:v1.13.12-feint.1)"
CERTBOT_IMAGE="certbot/certbot:latest"
OLD_NODE_IMAGE="$("${COMPOSE[@]}" images -q vpn-node-api)"
OLD_SINGBOX_IMAGE="$("${COMPOSE[@]}" images -q sing-box)"
OLD_CERTBOT_IMAGE="$("${COMPOSE[@]}" images -q certbot)"

trap rollback ERR

info "Fetching origin/$BRANCH"
git fetch origin "$BRANCH" --prune
git reset --hard "origin/$BRANCH"

DOCKER_GID="$(getent group docker | cut -d: -f3)"
if [[ -z "$DOCKER_GID" ]]; then
    error "Could not determine docker group GID"
    false
fi
env_set DOCKER_GID "$DOCKER_GID" "$ENV_FILE"

info "Pulling service images"
"${COMPOSE[@]}" pull certbot sing-box vpn-node-api
"${COMPOSE[@]}" up -d --no-build --remove-orphans

info "Synchronizing the persisted sing-box config with the updated template"
sync_template

info "Waiting for node readiness"
wait_for_status

trap - ERR
rm -f "$ENV_BACKUP" "$CONFIG_BACKUP"
success "Update complete ($(git rev-parse --short HEAD))"
