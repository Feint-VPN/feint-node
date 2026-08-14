# Feint Node 🌌

<p align="center">
  <img src="static/banner.png" alt="Feint Node banner">
</p>

> 🌙 A quiet edge runtime for the Feint network: authenticated, atomic and deliberately difficult to discover.

`feint-node` is the small service installed beside one sing-box runtime. It
manages protocol users, produces connection URLs, tracks traffic and exposes a
stable API contract to [`feint-sdk`](../feint-sdk).

The node does not own global users, wallets, billing or subscription policy.
Those remain in the core SDK. This repository owns only the local sing-box
configuration and the runtime state of one server. 👾

## 🌌 Contents

- [Architecture](#-architecture)
- [Protocols](#-protocols)
- [Installation](#-installation)
- [Security and endpoint hiding](#-security-and-endpoint-hiding)
- [API contract](#-api-contract)
- [User lifecycle](#-user-lifecycle)
- [Subscriptions](#-subscriptions)
- [Traffic statistics](#-traffic-statistics)
- [Port management](#-port-management)
- [Configuration](#-configuration)
- [Development](#-development)
- [Deployment operations](#-deployment-operations)
- [Project structure](#-project-structure)
- [Current boundaries](#-current-boundaries)
- [Quality](#-quality)

## ✨ Architecture

The runtime has one active implementation path:

```text
Feint SDK / maintainer
          │
          ▼
     FastAPI contract
   api/routers · schemas
          │
          ▼
      domain service
 users · protocol mapping · rollback
          │
          ▼
        adapters
sing-box file · Docker · traffic · URLs
          │
          ▼
       sing-box
VLESS · VMess · Trojan · Hysteria2 · Shadowsocks
```

### Ownership

The node is authoritative for:

- the local sing-box configuration;
- users currently installed on this node;
- generated protocol URLs for those users;
- local traffic counters and runtime telemetry;
- certificate, port and container configuration for this server.

The Feint core remains authoritative for global identity, access state,
wallets, billing, subscriptions and distribution across multiple nodes.

### Atomic mutations

Creating or deleting a user follows one transaction-like flow:

1. load the current sing-box configuration;
2. create a local backup;
3. update every supported inbound;
4. write the new configuration atomically;
5. restart sing-box;
6. restore the backup if saving or restart fails.

## 🪐 Protocols

| Protocol | Runtime tag | User credential |
| --- | --- | --- |
| VLESS Reality | `vless-reality-in` | UUID |
| VMess WebSocket | `vmess-ws-in` | UUID |
| Trojan | `trojan-in` | Password |
| Hysteria2 | `hysteria2-in` | Password |
| Shadowsocks 2022 | `shadowsocks-in` | Base64 key |

One local user is added to every configured protocol. Protocol names returned
by runtime telemetry are intentionally strings because sing-box capabilities
may change independently of the SDK.

## 🌙 Installation

The installer prepares Docker, validates ports, obtains the TLS certificate,
generates secrets and starts the node:

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/node/e1/install.sh | \
  sudo bash -s -- \
  --domain vpn.example.com \
  --email admin@example.com
```

Requirements:

- a Linux server with root access;
- a domain resolving to the server;
- Docker with Compose;
- TCP port `80` available for the standalone Let's Encrypt challenge.

The installer checks occupied and duplicated ports before changing the server.
It reports the owning process and never terminates another service
automatically.

### Installer options

| Option | Default | Meaning |
| --- | --- | --- |
| `--domain` | required | Public FQDN pointing to the node. |
| `--email` | required | Let's Encrypt contact email. |
| `--secret` | generated | Explicit node API secret. |
| `--api-port` | `8337` | Public HTTPS API port. |
| `--dir` | `/opt/vpn-node` | Installation directory. |
| `--sub` | `true` | Enable the node subscription endpoint. |
| `--branch` | `e1` | Repository branch installed on the server. |

## 🛸 Security and endpoint hiding

`HIDE_ENDPOINTS=true` is the production default.

When enabled, an unknown path and a declared path requested without the exact
`X-API-Secret` both receive the same empty `404`. This prevents casual scanners
from distinguishing the node API from an unused host.

```http
X-API-Secret: your-node-secret
```

Important behavior:

- there is intentionally no `/` endpoint;
- `/status` always requires authentication;
- `/health` is hidden from unauthenticated callers while endpoint hiding is enabled;
- docs and OpenAPI exist only in development mode and are still hidden without the secret;
- an unset or placeholder API secret never authenticates a request;
- public subscription links require both `SUBSCRIPTION_ENABLED=true` and
  `HIDE_ENDPOINTS=false`.

Set `HIDE_ENDPOINTS=false` only when public subscription URLs are an explicit
deployment requirement.

## 🔮 API contract

All administrative routes use `X-API-Secret`.

### System

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/health` | Cheap compatibility probe: `status` and `api_version`. |
| `GET` | `/status` | Uptime, local user count and enabled protocol ports. |
| `POST` | `/secrets` | Generate node, Reality and Shadowsocks secrets. |
| `POST` | `/initialize` | Run DNS, environment, config, certificate and container initialization. |

```bash
curl -H "X-API-Secret: $API_SECRET" \
  https://vpn.example.com:8337/status
```

Example response:

```json
{
  "status": "ok",
  "api_version": "2.0",
  "uptime": "02d 07h",
  "user_count": 250,
  "protocols": [
    {"name": "VLESS Reality", "port": 28473, "enabled": true}
  ]
}
```

### Users

| Method | Path | Result |
| --- | --- | --- |
| `POST` | `/user` | Create one user across all protocol inbounds. |
| `GET` | `/user/{username}` | Read one local user. |
| `GET` | `/users?limit=50&skip=0` | Read a paginated local user list. |
| `DELETE` | `/user/{username}` | Remove the user from every inbound. |
| `GET` | `/user/{username}/configs?server_domain=...` | Build protocol URLs. |

Usernames contain `3-50` ASCII letters, digits, `_` or `-`. A UUID and password
may be supplied by the maintainer; otherwise the node generates them.

### Statistics

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/user/{username}/stats` | Upload, download, total, availability and last activity. |
| `GET` | `/stats` | Current persisted counters for all observed users. |

### Subscription settings

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/sub/settings` | Current feature flag, domain and URI label template. |
| `PUT` | `/sub/settings` | Validate, persist and apply a new label template. |
| `GET` | `/sub/{username}` | Base64 bundle of available protocol URLs. |

## 👾 User lifecycle

Create a user:

```bash
curl -X POST https://vpn.example.com:8337/user \
  -H "X-API-Secret: $API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"username":"username"}'
```

The response contains the generated UUID, password and installed protocols.
The Feint SDK stores the resulting node-user credentials and uses them while
building the global subscription.

Delete a user:

```bash
curl -X DELETE \
  -H "X-API-Secret: $API_SECRET" \
  https://vpn.example.com:8337/user/username
```

Deletion removes the user from every inbound and clears their local traffic
counters on a best-effort basis.

## 🌌 Subscriptions

The node can produce a Hiddify-compatible Base64 payload, but this is optional.
The main Feint subscription is normally assembled by the maintainer from all
nodes.

```bash
curl https://vpn.example.com:8337/sub/username
```

The payload contains newline-separated connection URIs before Base64 encoding.
Labels use `SUB_URI_TEMPLATE` with these placeholders:

- `{protocol}` — lowercase protocol name;
- `{Protocol}` — display name;
- `{username}` — local username.

VMess stores the label inside its encoded `ps` field. Other protocols use a
URL fragment.

## 📡 Traffic statistics

`TrafficTracker` polls the sing-box V2Ray statistics API, accumulates counters
across runtime restarts and periodically persists them to
`/opt/sing-box/traffic.json`.

If the statistics backend is unavailable, the API returns cached totals with
`available=false`. Deleting a user also removes their cached counter record.

## 🚀 Port management

Never edit Compose mappings and the persisted sing-box JSON independently.
Use the canonical port command:

```bash
cd /opt/vpn-node
bash scripts/ports.sh show
bash scripts/ports.sh check
bash scripts/ports.sh randomize
```

Apply selected ports atomically:

```bash
bash scripts/ports.sh set --api 8337 --vless 28473 --apply
sudo ./scripts/setup-firewall.sh
```

`--apply` stages `.env.local`, validates conflicts and duplicates, updates the
persisted sing-box configuration, restarts affected services, checks health and
restores the previous configuration if the rollout fails.

## ⚙️ Configuration

Runtime values live in `.env.local`. Start from [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_SECRET` | unsafe placeholder | Shared maintainer secret; must be replaced. |
| `API_PORT` | `8000` | API listener and Compose port. |
| `HIDE_ENDPOINTS` | `true` | Hide every route from unauthenticated callers. |
| `DEV_MODE` | `false` | Enable docs and run uvicorn without TLS. |
| `SERVER_DOMAIN` | `example.com` | Public host used in generated URLs. |
| `SUBSCRIPTION_ENABLED` | `false` | Enable `/sub/{username}`. |
| `SUB_URI_TEMPLATE` | `🌌 Feint \| {Protocol}` | Display label for generated URIs. |
| `VLESS_PORT` | configurable | VLESS Reality listener. |
| `VMESS_PORT` | configurable | VMess WebSocket listener. |
| `TROJAN_PORT` | configurable | Trojan listener. |
| `HYSTERIA2_PORT` | configurable | Hysteria2 UDP listener. |
| `SHADOWSOCKS_PORT` | configurable | Shadowsocks listener. |
| `CONFIG_PATH` | `/opt/sing-box/config.json` | Persisted sing-box configuration. |
| `BACKUP_DIR` | `/opt/sing-box/backups` | Atomic rollback backups. |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Container control socket. |
| `SINGBOX_CONTAINER_NAME` | `sing-box` | Managed runtime container. |
| `CLASH_API_URL` | internal endpoint | Optional live statistics backend. |
| `V2RAY_API_ADDRESS` | internal endpoint | Per-user traffic counters. |
| `LOG_LEVEL` | `info` | Runtime log threshold. |
| `LOG_FORMAT` | `json` | Structured production logs. |

## 🌙 Development

Requirements:

- Python 3.11 or newer;
- [`uv`](https://docs.astral.sh/uv/);
- Docker for container and Linux acceptance checks.

Create the locked environment:

```powershell
uv sync --locked --extra dev
```

Run quality checks:

```powershell
uv run ruff format --check node/src node/tests
uv run ruff check node/src node/tests
cd node
uv run pytest -q --no-cov
```

Run the development container:

```bash
cp .env.example .env.local
docker compose -f docker-compose.dev.yml --env-file .env.local up --build
```

Production dependencies are in `node/requirements.txt`. Test and lint tools are
isolated in `node/requirements-dev.txt` and the `dev` uv extra; they are not
installed into the production image.

## 🛸 Deployment operations

Always pass the environment file to manual Compose commands:

```bash
docker compose --env-file .env.local ps
docker compose --env-file .env.local logs -f vpn-node-api sing-box
docker compose --env-file .env.local restart vpn-node-api
```

Update an installed node:

```bash
cd /opt/vpn-node
sudo ./update.sh
```

Additional operational references:

- [Deployment scripts](scripts/README.md)
- [Practical example](scripts/USAGE_EXAMPLE.md)
- [Firewall guide](scripts/FIREWALL_SETUP.md)
- [Quick start](QUICK_START.md)

## 🔭 Project structure

```text
feint-node/
├── node/
│   ├── src/
│   │   ├── api/          # FastAPI routers, dependencies and schemas
│   │   ├── domain/       # Local user rules and infrastructure ports
│   │   ├── adapters/     # sing-box, Docker, traffic and URL implementations
│   │   ├── utils/        # Settings, crypto and structured logging
│   │   └── main.py       # Application and lifespan
│   ├── tests/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/              # Ports, firewall and deployment helpers
├── sing-box/             # Runtime image assets
├── docker-compose.yml
├── install.sh
├── update.sh
├── pyproject.toml
└── uv.lock
```

There is no parallel legacy router or service tree. `api`, `domain` and
`adapters` are the only runtime path.

## 🌑 Current boundaries

- Authentication is a shared node secret, not user Bearer/JWT authentication.
- The node has no wallet, billing, global subscription or profile model.
- `/sub/{username}` is optional; cross-node subscriptions belong to the maintainer.
- Let's Encrypt installation currently uses the standalone HTTP challenge.
- sing-box is controlled through the mounted Docker socket.
- Traffic totals are local operational state, not billing authority.

## ✅ Quality

The current contract is checked on Windows and Linux:

- Ruff formatting and linting;
- unit, integration and property tests;
- production Docker image build;
- runtime import without development dependencies;
- port, installer, updater and firewall regression tests.

Current suite: **164 tests passing on Linux/Docker**.

## License

MIT
