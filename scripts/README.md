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

Without `--apply`, the command only stages values in `.env.local`; use that
only before the stack's first start. On a running node, `--apply` recreates the
API container, updates the persisted sing-box configuration, restarts sing-box,
checks the API health endpoint, and restores the prior configuration if any
step fails.

After every port change, update firewall rules and distribute fresh client
configuration to users:

```bash
sudo ./scripts/setup-firewall.sh
```

`scripts/generate-ports.sh` remains as a compatibility wrapper for
`ports.sh randomize`; use `ports.sh` for all new operational documentation and
automation.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/feint-node/main/install.sh | \
  sudo bash -s -- --domain vpn.example.com --email admin@example.com
```

You can select the API port during installation:

```bash
sudo bash install.sh --domain vpn.example.com --email admin@example.com --api-port 8337
```

The installer validates the chosen API port and selects available protocol
ports. It also requires TCP port 80 for the standalone Let's Encrypt HTTP
challenge. If port 80 is in use, installation stops and identifies the
listener; it does not interrupt the existing service. Free port 80 or use a
different certificate arrangement before installing. Webroot and DNS ACME
modes are not implemented yet.

### Legacy `init-node.sh`

`init-node.sh` is retained only for callers that use its former three-argument
interface. It validates those arguments and delegates to `install.sh`; the
server-IP argument is informational because the installer discovers the host
network configuration. New automation should call `install.sh` directly.

## Firewall

Run the firewall setup only after `.env.local` contains the intended port plan:

```bash
sudo ./scripts/setup-firewall.sh
```

It reads `.env.local` and opens the API, ACME, and VPN listeners. If you choose
to change SSH, verify a new SSH session before ending the existing one. See the
[firewall guide](FIREWALL_SETUP.md) for recovery steps.

## Updates

```bash
sudo bash ./update.sh
sudo bash ./update.sh --branch main
```

`update.sh` preserves `.env.local`, including ports, secrets, and `DOCKER_GID`.
The Compose file is declarative and should stay tracked and unmodified. Updates
reuse Docker's normal layer cache; they do not force a clean image build.

## Manual Docker Compose

Compose interpolation needs `.env.local` explicitly. Use this form for manual
operations:

```bash
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f vpn-node-api
docker compose --env-file .env.local restart sing-box
```

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
