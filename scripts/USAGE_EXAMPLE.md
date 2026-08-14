# Deployment example

This is the normal deployment flow for a new node. Replace the example domain,
email, and server IP with your own values.

## 1. Prepare DNS and port 80

Create an A record for `vpn.example.com` pointing to the server. The installer
uses Let's Encrypt's standalone HTTP challenge, so TCP port 80 must be free on
the server and reachable from the internet. The installer reports the owning
process if it is occupied and never stops it.

## 2. Install

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/node/e1/install.sh | \
  sudo bash -s -- --domain vpn.example.com --email admin@example.com
```

To choose the HTTPS API port yourself:

```bash
sudo bash install.sh \
  --domain vpn.example.com \
  --email admin@example.com \
  --api-port 8337
```

The installer validates the requested API port, finds unused protocol ports,
writes them to `/opt/vpn-node/.env.local`, and starts the stack. It reuses
normal Docker build caching on later updates.

## 3. Configure the firewall

```bash
cd /opt/vpn-node
sudo ./scripts/setup-firewall.sh
```

The firewall script reads the selected values from `.env.local`.

## 4. Verify the node

```bash
bash scripts/ports.sh show
bash scripts/ports.sh check
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs --tail=100 vpn-node-api
```

Check the API locally:

```bash
API_PORT=$(grep '^API_PORT=' .env.local | cut -d '=' -f2)
API_SECRET=$(grep '^API_SECRET=' .env.local | cut -d '=' -f2)
curl -sk -H "X-API-Secret: ${API_SECRET}" "https://127.0.0.1:${API_PORT}/health"
```

## 5. Change ports later

Use `ports.sh`; never edit the Compose mapping or sing-box configuration by
hand.

```bash
# Show ports and any listeners that already use them
bash scripts/ports.sh check

# Apply a specific change
bash scripts/ports.sh set --api 8337 --vless 28473 --apply

# Or apply a completely new checked plan
bash scripts/ports.sh randomize --apply

# Synchronize UFW after the applied change
sudo ./scripts/setup-firewall.sh
```

`--apply` validates the proposed plan, updates `.env.local`, synchronizes the
persisted sing-box configuration, restarts the affected services, and rolls
back if the API health check fails. A port conflict must be resolved by choosing
another port or reconfiguring the service that owns it; no process is killed.

## 6. Routine operations

Run Compose with the runtime environment file so its variable substitutions use
the deployed values:

```bash
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f sing-box
sudo bash ./update.sh
```

`update.sh` keeps `.env.local`, including port assignments, API secret, and
Docker group ID. It does not require local edits to `docker-compose.yml`.
