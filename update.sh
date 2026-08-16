#!/bin/bash
# ============================================================
#  Feint VPN Node — in-place updater
#  Usage:
#    sudo ./update.sh [--branch main] [--dir /opt/vpn-node] [--force]
#
#  What it does:
#    - validates the deployed repo and local runtime config
#    - preserves .env.local via backup
#    - fetches the target branch and fast-forwards via reset
#    - preserves declarative runtime values from .env.local
#    - pulls service images and restarts containers
#    - performs a local /health probe at the configured API port
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }
header()  {
    echo ""
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${CYAN}  $*${NC}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

INSTALL_DIR="/opt/vpn-node"
BRANCH=""
FORCE="false"

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --dir <path>      Install directory (default: /opt/vpn-node)
  --branch <name>   Git branch to update to (default: current branch)
  --force           Allow overwriting unexpected tracked changes
  -h, --help        Show this help

Examples:
  sudo ./update.sh
  sudo ./update.sh --branch main
  sudo ./update.sh --dir /opt/vpn-node --force
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            [[ $# -lt 2 ]] && die "--dir requires a value"
            INSTALL_DIR="$2"
            shift 2
            ;;
        --branch)
            [[ $# -lt 2 ]] && die "--branch requires a value"
            BRANCH="$2"
            shift 2
            ;;
        --force)
            FORCE="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

[[ $EUID -ne 0 ]] && die "Run as root: sudo bash $0 $*"

command -v git >/dev/null 2>&1 || die "git is required"
command -v docker >/dev/null 2>&1 || die "docker is required"
docker compose version >/dev/null 2>&1 || die "docker compose is required"

[[ -d "$INSTALL_DIR/.git" ]] || die "No git repository found at $INSTALL_DIR"
[[ -f "$INSTALL_DIR/docker-compose.yml" ]] || die "docker-compose.yml not found in $INSTALL_DIR"

ENV_FILE="$INSTALL_DIR/.env.local"
[[ -f "$ENV_FILE" ]] || warn ".env.local was not found in $INSTALL_DIR; defaults will be used where possible"

compose() {
    if [[ -f "$ENV_FILE" ]]; then
        docker compose --env-file "$ENV_FILE" "$@"
    else
        docker compose "$@"
    fi
}

env_get() {
    local key="$1" file="$2" default_value="${3:-}"

    if [[ -f "$file" ]]; then
        local raw
        raw=$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d '=' -f2- || true)
        raw="${raw%\"}"
        raw="${raw#\"}"
        raw="${raw%\'}"
        raw="${raw#\'}"
        if [[ -n "$raw" ]]; then
            echo "$raw"
            return 0
        fi
    fi

    echo "$default_value"
}

backup_file() {
    local source_file="$1"
    [[ -f "$source_file" ]] || return 0
    local stamp backup_path
    stamp=$(date -u '+%Y%m%d-%H%M%S')
    backup_path="${source_file}.bak.${stamp}"
    cp "$source_file" "$backup_path"
    echo "$backup_path"
}

detect_branch() {
    local repo_dir="$1"
    local current_branch
    current_branch=$(git -C "$repo_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
    if [[ -n "$current_branch" ]]; then
        echo "$current_branch"
        return 0
    fi

    local remote_head
    remote_head=$(git -C "$repo_dir" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
    if [[ -n "$remote_head" ]]; then
        echo "${remote_head#origin/}"
        return 0
    fi

    echo "main"
}

generate_secret() {
    local secret
    set +o pipefail
    secret="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
    set -o pipefail
    printf '%s' "$secret"
}

set_env_value() {
    local key="$1" value="$2" file="$3"
    touch "$file"
    if grep -qE "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

ensure_stats_runtime_env() {
    local env_file="$1"
    local clash_api_secret clash_api_url v2ray_api_address

    clash_api_secret="$(env_get "CLASH_API_SECRET" "$env_file" "")"
    if [[ -z "$clash_api_secret" ]]; then
        clash_api_secret="$(generate_secret)"
        set_env_value "CLASH_API_SECRET" "$clash_api_secret" "$env_file"
        success "Added CLASH_API_SECRET to .env.local"
    fi

    clash_api_url="$(env_get "CLASH_API_URL" "$env_file" "")"
    if [[ -z "$clash_api_url" ]]; then
        set_env_value "CLASH_API_URL" "http://host.docker.internal:9090" "$env_file"
        success "Added CLASH_API_URL to .env.local"
    fi

    v2ray_api_address="$(env_get "V2RAY_API_ADDRESS" "$env_file" "")"
    if [[ -z "$v2ray_api_address" ]]; then
        set_env_value "V2RAY_API_ADDRESS" "host.docker.internal:10085" "$env_file"
        success "Added V2RAY_API_ADDRESS to .env.local"
    fi

    if [[ -z "$(env_get "HIDE_ENDPOINTS" "$env_file" "")" ]]; then
        set_env_value "HIDE_ENDPOINTS" "true" "$env_file"
        success "Enabled hidden endpoints in .env.local"
    fi
}

migrate_stats_config() {
    local clash_api_secret="$1"
    local result

    result="$({ compose exec -T vpn-node-api python - "$clash_api_secret" <<'PY'
import json
import sys
from pathlib import Path

path = Path("/opt/sing-box/config.json")
secret = sys.argv[1]

if not path.exists():
    print("missing")
    raise SystemExit(2)

data = json.loads(path.read_text(encoding="utf-8"))
changed = False

experimental = data.get("experimental")
if not isinstance(experimental, dict):
    experimental = {}
    data["experimental"] = experimental
    changed = True

clash_api = experimental.get("clash_api")
if not isinstance(clash_api, dict):
    clash_api = {}
    experimental["clash_api"] = clash_api
    changed = True

if clash_api.get("external_controller") != "0.0.0.0:9090":
    clash_api["external_controller"] = "0.0.0.0:9090"
    changed = True

if clash_api.get("secret") != secret:
    clash_api["secret"] = secret
    changed = True

cache_file = experimental.get("cache_file")
if not isinstance(cache_file, dict):
    cache_file = {}
    experimental["cache_file"] = cache_file
    changed = True

if cache_file.get("enabled") is not True:
    cache_file["enabled"] = True
    changed = True

if cache_file.get("path") != "/opt/sing-box/cache.db":
    cache_file["path"] = "/opt/sing-box/cache.db"
    changed = True

v2ray_api = experimental.get("v2ray_api")
if not isinstance(v2ray_api, dict):
    v2ray_api = {}
    experimental["v2ray_api"] = v2ray_api
    changed = True

if v2ray_api.get("listen") != "0.0.0.0:10085":
    v2ray_api["listen"] = "0.0.0.0:10085"
    changed = True

stats = v2ray_api.get("stats")
if not isinstance(stats, dict):
    stats = {}
    v2ray_api["stats"] = stats
    changed = True

if stats.get("enabled") is not True:
    stats["enabled"] = True
    changed = True

usernames = sorted(
    {
        user.get("name")
        for inbound in data.get("inbounds", [])
        if isinstance(inbound, dict)
        for user in inbound.get("users", []) or []
        if isinstance(user, dict) and user.get("name")
    }
)
if stats.get("users") != usernames:
    stats["users"] = usernames
    changed = True

if changed:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

print("changed" if changed else "unchanged")
PY
    } 2>/dev/null)"

    case "$result" in
        changed)
            success "Migrated persisted sing-box config for clash_api and v2ray_api stats"
            return 10
            ;;
        unchanged)
            success "Persisted sing-box config already supports clash_api and v2ray_api stats"
            return 0
            ;;
        missing)
            warn "Skipping stats config migration because /opt/sing-box/config.json was not found"
            return 0
            ;;
        *)
            warn "Could not verify persisted sing-box config migration"
            return 0
            ;;
    esac
}

verify_clash_api_access() {
    local clash_api_secret="$1"
    compose exec -T vpn-node-api python - "$clash_api_secret" <<'PY'
import os
import sys

import httpx

secret = sys.argv[1]
url = os.getenv("CLASH_API_URL", "http://host.docker.internal:9090").rstrip("/")
headers = {"Authorization": f"Bearer {secret}"} if secret else {}

response = httpx.get(f"{url}/connections", headers=headers, timeout=5.0)
response.raise_for_status()
PY
}

verify_v2ray_api_access() {
    compose exec -T vpn-node-api python - <<'PY'
import asyncio
import os

from adapters.traffic_tracker import fetch_v2ray_user_counters


async def main() -> None:
    address = os.getenv("V2RAY_API_ADDRESS", "host.docker.internal:10085")
    await fetch_v2ray_user_counters(address, timeout=5.0)


asyncio.run(main())
PY
}

header "Feint VPN Node Update"

BRANCH="${BRANCH:-$(detect_branch "$INSTALL_DIR")}"
API_PORT="$(env_get "API_PORT" "$ENV_FILE" "8337")"
TLS_ENABLED="$(env_get "TLS_ENABLED" "$ENV_FILE" "true")"
API_USE_SSL="$(env_get "API_USE_SSL" "$ENV_FILE" "true")"

echo -e "  ${BOLD}Install:${NC}  $INSTALL_DIR"
echo -e "  ${BOLD}Branch:${NC}   $BRANCH"
echo -e "  ${BOLD}API port:${NC} $API_PORT"
echo ""

header "Step 1 / 6 — Preflight"

cd "$INSTALL_DIR"

status_output=$(git status --porcelain --untracked-files=no)
if [[ -n "$status_output" ]]; then
    unexpected_changes=$(echo "$status_output" | awk '{print $2}')
    if [[ -n "$unexpected_changes" && "$FORCE" != "true" ]]; then
        echo "$status_output"
        die "Tracked changes detected outside docker-compose.yml. Re-run with --force if you want update.sh to overwrite them."
    fi

    if [[ -n "$unexpected_changes" ]]; then
        warn "Proceeding with --force; tracked changes will be overwritten."
    fi
fi

ENV_BACKUP="$(backup_file "$ENV_FILE")"
if [[ -n "$ENV_BACKUP" ]]; then
    success "Backed up .env.local to $(basename "$ENV_BACKUP")"
fi

DOCKER_GID=$(getent group docker | cut -d: -f3 || true)
[[ -n "$DOCKER_GID" ]] || die "Could not determine docker group GID"
success "Detected docker group GID: $DOCKER_GID"
set_env_value "DOCKER_GID" "$DOCKER_GID" "$ENV_FILE"

header "Step 2 / 6 — Refresh repository"

info "Fetching origin/$BRANCH ..."
git fetch origin "$BRANCH" --prune

info "Resetting worktree to origin/$BRANCH ..."
git reset --hard "origin/$BRANCH"
success "Repository updated to $(git rev-parse --short HEAD)"

header "Step 3 / 6 — Reapply local runtime settings"

if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" && ! -f "$ENV_FILE" ]]; then
    cp "$ENV_BACKUP" "$ENV_FILE"
    warn ".env.local was restored from backup"
fi

ensure_stats_runtime_env "$ENV_FILE"

CLASH_API_SECRET="$(env_get "CLASH_API_SECRET" "$ENV_FILE" "")"

header "Step 4 / 6 — Refresh containers"

info "Pulling service images ..."
compose pull certbot sing-box vpn-node-api

info "Restarting containers ..."
compose up -d --no-build --remove-orphans
success "Containers are up"

header "Step 5 / 6 — Migrate persisted config"

set +e
migrate_stats_config "$CLASH_API_SECRET"
migration_status=$?
set -e

if [[ "$migration_status" -eq 10 ]]; then
    info "Restarting sing-box to apply stats migration ..."
    compose restart sing-box >/dev/null
    success "sing-box restarted with migrated config"
fi

if verify_clash_api_access "$CLASH_API_SECRET" >/dev/null 2>&1; then
    success "clash_api is reachable from vpn-node-api"
else
    warn "clash_api connectivity check failed; live connection inspection may remain unavailable"
    warn "Inspect logs with: cd $INSTALL_DIR && docker compose logs --tail 100 sing-box vpn-node-api"
fi

if verify_v2ray_api_access >/dev/null 2>&1; then
    success "v2ray_api is reachable from vpn-node-api"
else
    warn "v2ray_api connectivity check failed; per-user traffic stats will remain unavailable"
    warn "Inspect logs with: cd $INSTALL_DIR && docker compose logs --tail 100 sing-box vpn-node-api"
fi

header "Step 6 / 6 — Verify health"

if [[ "$TLS_ENABLED" == "true" && "$API_USE_SSL" == "true" ]]; then
    HEALTH_URL="https://127.0.0.1:${API_PORT}/health"
    CURL_FLAGS=(-sk)
else
    HEALTH_URL="http://127.0.0.1:${API_PORT}/health"
    CURL_FLAGS=(-s)
fi

API_SECRET="$(env_get "API_SECRET" "$ENV_FILE" "")"
if curl "${CURL_FLAGS[@]}" --fail -H "X-API-Secret: ${API_SECRET}" "$HEALTH_URL" >/dev/null; then
    success "Health check passed: $HEALTH_URL"
else
    warn "Health check failed: $HEALTH_URL"
    warn "Inspect logs with: cd $INSTALL_DIR && docker compose logs --tail 100"
fi

compose ps

echo ""
success "Update complete"
echo -e "${BOLD}Current commit:${NC} $(git rev-parse --short HEAD)"
if [[ -n "$ENV_BACKUP" ]]; then
    echo -e "${BOLD}Backup:${NC}         $ENV_BACKUP"
fi
