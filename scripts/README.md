# Deployment scripts

## Port lifecycle

`ports.sh` is the supported interface for inspecting and changing deployment
ports. The single source of truth is `.env.local`; do not hand-edit
`docker-compose.yml` or the persisted sing-box JSON to change ports.

```bash
# Inspect the deployed configuration and listeners
bash scripts/ports.sh show
bash scripts/ports.sh check

# Choose individual ports, then apply the complete change
bash scripts/ports.sh set --api 8337 --vless 28473 --apply

# Or choose a new checked random plan
bash scripts/ports.sh randomize --apply
```

`set` and `randomize` validate port numbers, prevent duplicate assignments for
the same transport, and report a process already listening on a requested port.
They never kill a process to make a port available.

Without `--apply`, the command only prints the validated plan and leaves the
deployment unchanged. With `--apply`, it recreates the API container, updates
the persisted sing-box configuration, restarts sing-box, checks `/status`, and
restores the previous env and sing-box configuration if any step fails.

After every port change, update firewall rules and distribute fresh client
configuration to users:

```bash
sudo ./scripts/setup-firewall.sh
```

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/feint-node/main/install.sh | \
  sudo bash -s -- --domain vpn.example.com --email admin@example.com
```

You can select the API port during installation:

```bash
sudo bash install.sh --domain vpn.example.com --email admin@example.com --api-port 8337
```

SDK provisioning must select the SSH port up front. This makes installation
non-interactive and leaves the SDK with the exact port to persist:

```bash
sudo bash install.sh \
  --domain vpn.example.com \
  --email admin@example.com \
  --new-ssh-port 41035 \
  --ssh-public-key "$(cat ~/.ssh/id_ed25519.pub)"
```

The installer validates the chosen API port and selects available protocol
ports. It also requires TCP port 80 for the standalone Let's Encrypt HTTP
challenge. If port 80 is in use, installation stops and identifies the
listener; it does not interrupt the existing service. Free port 80 or use a
different certificate arrangement before installing. Webroot and DNS ACME
modes are not implemented yet.

## Firewall

Run the firewall setup only after `.env.local` contains the intended port plan:

```bash
sudo ./scripts/setup-firewall.sh
```

It replaces host firewall rules with the Feint allowlist and requires moving
SSH to a new free port. Manual use confirms a second session. SDK installation
passes an explicit port and uses the non-interactive mode. See the
[firewall guide](FIREWALL_SETUP.md) for the exact flows and recovery steps.

## Updates

```bash
sudo bash ./update.sh
sudo bash ./update.sh --branch main
```

`update.sh` backs up `.env.local` and the deployed sing-box config, pulls the
tracked code and published images, then synchronizes the deployed config with
the updated template while preserving every inbound's users. The rendered
candidate passes `sing-box check` before replacement. A failed rollout restores
the previous commit, images, env and config.

## Manual Docker Compose

Compose interpolation needs `.env.local` explicitly. Use this form for manual
operations:

```bash
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f vpn-node-api
docker compose --env-file .env.local restart sing-box
```

## Diagnostics

Run the read-only deployment checks before collecting individual logs:

```bash
sudo bash scripts/diagnose.sh
sudo bash scripts/diagnose.sh --logs 50
```

The command checks configuration, containers, authenticated `/status`, the
persisted sing-box config, listeners, SSH and UFW. Logs are opt-in, capped at
100 lines, and configured secrets are redacted.

## Endpoint hiding

New installs set `HIDE_ENDPOINTS=true`. Without the correct `X-API-Secret`,
the API returns an empty `404` for both known and unknown paths, including the
root and health endpoints. Keep this enabled unless public subscription URLs
are required; those URLs cannot include the secret header.

## Troubleshooting

### A requested port is occupied

```bash
bash scripts/ports.sh check
sudo ss -lntup
```

Leave the current owner running and choose another port:

```bash
bash scripts/ports.sh set --vless 28473 --apply
sudo ./scripts/setup-firewall.sh
```

### Certificate issuance fails

Confirm that the domain resolves to this server, TCP port 80 is reachable from
the internet, and no local service owns it. The current installer relies on the
standalone HTTP challenge, so an existing web server must be moved or
reconfigured before issuance.

### Inspect service health

```bash
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs --tail=100 vpn-node-api
API_PORT=$(grep '^API_PORT=' .env.local | cut -d '=' -f2)
API_SECRET=$(grep '^API_SECRET=' .env.local | cut -d '=' -f2)
curl -sk -H "X-API-Secret: ${API_SECRET}" "https://127.0.0.1:${API_PORT}/health"
```
