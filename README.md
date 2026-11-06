# Feint Node 🌌

<p align="center">
  <img src="static/banner.png" alt="Feint Node banner">
</p>

> 🌙 A quiet edge runtime for the Feint network: authenticated, atomic and deliberately difficult to discover.

`feint-node` is the small service installed beside one VPN runtime. It
manages protocol users, produces connection URLs, tracks traffic and exposes a
stable authenticated HTTP API. This repository owns the canonical node
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
Authenticated API request
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
 sing-box or Xray + rathole
VPN protocols · encrypted reverse transport
```

### Ownership

The node is authoritative for:

- the canonical local runtime configuration;
- users currently installed on this node;
- generated protocol URLs for those users;
- local traffic counters and runtime telemetry;
- certificate, port and container configuration for this server.

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
| VLESS Vision REALITY | `vless-reality-in` | UUID |
| VMess WebSocket | `vmess-ws-in` | UUID |
| Trojan | `trojan-in` | Password |
| Hysteria2 | `hysteria2-in` | Password |
| TUIC (optional companion) | `tuic-in` | UUID + password |
| Shadowsocks 2022 | `shadowsocks-in` | Base64 key |

One local user is added to every configured protocol. Protocol names returned
by runtime telemetry are intentionally strings because sing-box capabilities
may change independently of the node API.

See [TUIC companion setup and acceptance](docs/tuic.md) for an additive Xray
deployment that preserves existing clients and aggregates both runtimes' traffic.

## 🌙 Installation

Use the native Xray-core runtime for a VLESS REALITY + Hysteria2 node:

For opt-in user additions and route assignments without disconnecting live HY2
sessions, see [Xray hot registration](docs/xray-hot-registration.md). It requires
a private control listener; TUIC and hard credential revocation still restart.

```bash
bash install.sh \
  --domain vpn.example.com \
  --email admin@example.com \
  --template vless \
  --runtime xray
```

`sing-box` remains the default. Xray mode keeps the user, subscription, status,
traffic-statistics and SOCKS outbound contracts. This allows a VLESS Reality
entry node to route selected users through an encrypted reverse transport
without changing their credentials.

Xray 26.7.28 defaults to a REALITY minimum client version of `26.3.27`.
sing-box 1.13.19 identifies its REALITY client as `1.8.1`, so the default rejects it.
For an explicitly selected compatibility deployment, set
`XRAY_REALITY_MIN_CLIENT_VERSION=1.8.1` in `.env.local`, then run `update.sh`.
For a new installation, pass it in the installer's environment. Empty keeps
Xray's upstream default. This changes only the version gate, not keys or UUIDs.
**Tradeoff:** Xray warns that lowering this threshold increases blocking risk;
it is not equivalent to the upstream anti-probing policy. Compatibility with
one tested core does not establish compatibility with every GUI client.
See the [Xray version gate](https://github.com/XTLS/Xray-core/blob/v26.7.28/infra/conf/transport_security.go)
and [sing-box client version](https://github.com/SagerNet/sing-box/blob/v1.13.19/common/tls/reality_client.go).

The installer prepares Docker, validates ports, obtains the TLS certificate,
generates secrets and starts the node:

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/feint-node/main/install.sh | \
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
Before starting containers, it renders the selected canonical template and
validates the resulting sing-box or Xray configuration with the selected core.

With the Xray runtime, `--template vless` installs VLESS Vision REALITY on TCP
`38519` and prefers UDP `443` for Hysteria2. If UDP `443` is already occupied,
the installer selects a free UDP port without disturbing its owner. The VLESS handshake target is
`vkvideo.ru:443`. Every installation generates its own REALITY key pair and
short ID. A web server may independently use TCP `443`, but its HTTP/3 listener
must remain disabled because HTTP/3 also requires UDP `443`.

### Installer options

| Option | Default | Meaning |
| --- | --- | --- |
| `--domain` | required | Public FQDN pointing to the node. |
| `--email` | required | Let's Encrypt contact email. |
| `--secret` | generated | Explicit node API secret. |
| `--api-port` | `8337` | Public HTTPS API port. |
| `--dir` | `/opt/vpn-node` | Installation directory. |
| `--sub` | `true` | Enable the node subscription endpoint. |
| `--branch` | `main` | Repository branch installed on the server. |
| `--template` | `default` | Runtime profile: `default`, `vless`, or `hysteria2`. |
| `--runtime` | `sing-box` | VPN core: `sing-box` or `xray`. |
| `--new-ssh-port` | random | Use this fixed SSH port and skip interactive confirmation for SDK installation. May match the current SSH port to harden it in place. |
| `--ssh-public-key` | existing key | Public key installed before password SSH is disabled. Required with `--new-ssh-port`. |

For a non-interactive SDK installation, provide the SSH port that the SDK will
persist and use after provisioning:

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/feint-node/main/install.sh | \
  sudo bash -s -- \
  --domain vpn.example.com \
  --email admin@example.com \
  --new-ssh-port 220 \
  --ssh-public-key "$(cat ~/.ssh/id_ed25519.pub)"
```

The installer always disables SSH password login. Without `--new-ssh-port`, it
requires an existing authorized key, selects a random port, and waits until a
second SSH connection is confirmed.

`main` installs `feint-node:latest`; the `dev` branch installs the matching
`feint-node:dev` image published by the repository workflow.

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
The node is a server-to-server API and intentionally does not emit browser CORS
headers.

### System

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/health` | Cheap compatibility probe: `status` and `api_version`. |
| `GET` | `/status` | Readiness of the config, sing-box and statistics, plus node telemetry. |

```bash
curl -H "X-API-Secret: $API_SECRET" \
  https://vpn.example.com:8337/status
```

Example response:

```json
{
  "status": "ok",
  "api_version": "2.4",
  "uptime": "02d 07h",
  "configuration": "available",
  "sing_box": "running",
  "statistics": "available",
  "user_count": 250,
  "protocols": [
    {"name": "VLESS", "port": 28473, "enabled": true}
  ]
}
```

`status` is `degraded` when the configuration cannot be read, the sing-box
container is stopped, or live statistics are unavailable. The endpoint still
returns the state of every component so an operator can identify the failed
part without additional probes.

### Users

| Method | Path | Result |
| --- | --- | --- |
| `POST` | `/user` | Create one user across all protocol inbounds. |
| `POST` | `/users` | Idempotently create up to 500 users in one config mutation. |
| `GET` | `/user/{username}` | Read one local user. |
| `GET` | `/users?limit=50&skip=0` | Read a paginated local user list. |
| `DELETE` | `/user/{username}` | Remove the user from every inbound. |
| `GET` | `/user/{username}/configs?server_domain=...` | Build protocol URLs. |

Usernames contain `3-50` ASCII letters, digits, `_` or `-`. A UUID and password
may be supplied in the request; otherwise the node generates them.

Bulk creation accepts `{"users": [...]}` with the same user objects as
`POST /user`. Existing usernames are skipped, so a maintainer can safely retry
the complete batch. One request saves the resulting configuration and reloads
sing-box at most once; an unchanged batch does neither.

### Outbounds

| Method | Path | Result |
| --- | --- | --- |
| `PUT` | `/outbound/{outbound_id}` | Creates or replaces one managed outbound. |
| `DELETE` | `/outbound/{outbound_id}` | Removes an unused managed outbound. |
| `PUT` | `/outbound/{outbound_id}/user/{user_id}` | Idempotently routes one existing user. |
| `POST` | `/outbound/{outbound_id}/users` | Idempotently routes up to 500 existing users. |
| `DELETE` | `/outbound/{outbound_id}/user/{user_id}` | Removes one user from the outbound. |
| `DELETE` | `/outbound/{outbound_id}/users` | Removes up to 500 users from the outbound. |

Bulk outbound access mutates one route rule and reloads the active runtime at
most once.
Every supplied user must already exist on the node. Repeating an unchanged
request performs no save or reload.

Managed outbounds accept `type=hysteria2` on sing-box and `type=socks` on both
sing-box and Xray. A reverse route uses a local SOCKS5 endpoint and therefore
does not couple user provisioning to transport setup.

SOCKS outbounds also accept optional paired `username` / `password` fields. The
password is redacted in API models and explicitly unwrapped only for the private
runtime configuration. Use authenticated remote SOCKS only over the private
Tailscale path, not the public internet. On a hot-reload Xray node, a changed
authenticated endpoint gets a versioned handler; routing switches only after
the handler is added. The old handler remains available for drain and rollback.
HY2 ingress stays unchanged. Preparation does not apply this migration; see
[resource budget and migration gates](docs/resource-budget.md).

### Reverse transport

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/reverse/key` | Returns this node's public Noise key. |
| `GET` | `/reverse` | Returns the active role and redacted settings. |
| `PUT` | `/reverse` | Atomically creates or replaces the node's one reverse link. |
| `DELETE` | `/reverse` | Disables the reverse link. |

The public entry node runs the `server` role. The exit node runs the `client`
role and initiates the connection back to the entry node, so the exit does not
need a publicly reachable tunnel port. The link uses rathole TCP transport with
Noise encryption. Tokens and remote public keys are never returned by
`GET /reverse`.

Configure the entry node:

```json
{
  "mode": "server",
  "bind_host": "0.0.0.0",
  "bind_port": 42100,
  "expose_port": 42101,
  "token": "one-random-shared-secret-with-at-least-32-characters"
}
```

Configure the exit node with the entry node's `/reverse/key` value:

```json
{
  "mode": "client",
  "remote_host": "ru.example.com",
  "remote_port": 42100,
  "token": "one-random-shared-secret-with-at-least-32-characters",
  "remote_public_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
}
```

Then create a SOCKS outbound on the entry node. `REVERSE_PROXY_PORT` belongs to
the exit node; `server_port` below is the entry node's `expose_port`:

```json
{
  "type": "socks",
  "server": "127.0.0.1",
  "server_port": 42101,
  "auth_users": []
}
```

Assign users through the existing outbound user endpoints. Creating or
replacing the reverse link never creates users and never changes route grants.
The current contract intentionally supports one reverse link per node.

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

Provision a batch:

```bash
curl -X POST https://vpn.example.com:8337/users \
  -H "X-API-Secret: $API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"users":[{"username":"first"},{"username":"second"}]}'
```

Delete a user:

```bash
curl -X DELETE \
  -H "X-API-Secret: $API_SECRET" \
  https://vpn.example.com:8337/user/username
```

Deletion removes the user from every inbound and clears their local traffic
counters on a best-effort basis.

## 🌌 Subscriptions

The node can optionally produce a Hiddify-compatible Base64 payload from its
locally configured protocols.

```bash
curl -H "X-API-Secret: $API_SECRET" \
  https://vpn.example.com:8337/sub/username
```

The payload contains newline-separated connection URIs before Base64 encoding.
Labels use `SUB_URI_TEMPLATE` with these placeholders:

- `{protocol}` — lowercase protocol name;
- `{Protocol}` — display name;
- `{username}` — local username.

VMess stores the label inside its encoded `ps` field. Other protocols use a
URL fragment.

Set `PUBLISHED_PROTOCOLS` to a comma-separated allowlist such as
`hysteria2,vless` to hide unhealthy protocols without stopping their runtime
listeners. An empty value publishes every configured protocol. If the public
Hysteria2 port is forwarded to a different runtime port, set
`HYSTERIA2_PUBLIC_PORT` to the client-facing UDP port.
If a deployed node uses a NAT redirect instead of a native public listener,
keep that redirect persistent. `scripts/lib/runtime/udp_redirect.sh` and the deployment
example `deploy/systemd/feint-hysteria2-redirect.service` do this for the current
RU node's UDP `443 → 36454` mapping. Remove the unit when Xray itself moves
to UDP `443`.

### Private RU → GE interconnect (optional)

The SDK can manage separate per-route sidecars with `NodeTransport.TAILSCALE`.
It invokes `scripts/lib/interconnect/manage_interconnect.py` over trusted SSH, supplying peer
credentials through stdin. This helper requires an already running Tailscale IPv4
interface, picks free ports, authenticates the peer, and persists private state in
`.interconnect/`. It does not alter Tailscale, the main runtime or the legacy
`interconnect.json` deployment below. Repeated calls reuse the same credentials
and listener; removal checks the container's deployment label before deleting it.
Only its own container and files are removed. Include `.interconnect/` in backups.
The helper uses `INTERCONNECT_IMAGE`, falling back to the installed `SINGBOX_IMAGE`;
SDK-managed channels need this script on both nodes. No new HTTP API is required.

`deploy/compose/interconnect.yml` runs an independent sing-box process. It does
not replace or restart the node's Xray, rathole, or API containers. The exit
listens **only on its Tailscale IPv4 address** and requires SOCKS credentials;
the entry listens only on `127.0.0.1`. Entry-side UDP is carried over the
authenticated TCP connection using sing-box UDP-over-TCP v2. No SOCKS port is
published through Docker. Both nodes must have Tailscale running, the same
dedicated credential in a root-only file, and distinct unused ports. The
credential must not be placed in `.env.local` or on the command line.
Create it once with `sudo sh -c 'umask 077; openssl rand -base64 48 > /root/feint-interconnect-password'`
and transfer the same file to the other node over the existing private SSH connection.
After copying, run `sudo chmod 600 /root/feint-interconnect-password` on both
nodes; `scp` may create the destination with group/world-readable permissions,
which the configurator deliberately rejects.

On the GE exit, from the installed repository directory:

```bash
sudo python3 scripts/cli.py configure-interconnect exit \
  --tailscale-address "$(tailscale ip -4)" --port 39085 \
  --username feint-interconnect \
  --password-file /root/feint-interconnect-password
python3 scripts/cli.py interconnect up -d
```

On the RU entry, use the **GE** Tailscale address and the same credential:

```bash
sudo python3 scripts/cli.py configure-interconnect entry \
  --tailscale-address GE_TAILSCALE_IP --port 39085 --local-port 39083 \
  --username feint-interconnect \
  --password-file /root/feint-interconnect-password
python3 scripts/cli.py interconnect up -d
```

The exit reuses the installed RU GeoIP rule set and rejects private and RU
destinations, matching the node's existing exit policy. The entry's local port
is the `server_port` for a managed SOCKS outbound on the RU node. Starting
these containers alone does not move any user traffic; the SDK must explicitly
assign users to that outbound. Keep the existing route until an isolated
acceptance check confirms both TCP and UDP egress through GE. Stop just this
optional component with `python3 scripts/cli.py interconnect stop`. It runs as a
separate Compose project, so a normal `update.sh` does not remove it as an
orphan. The script resolves the node's existing data volume for the GeoIP file.

For an existing managed SOCKS route, take a node-config backup before changing
its loopback port. Copy `scripts/lib/interconnect/switch_interconnect_route.py` into the node API
container and run it first with `--expected-port OLD_PORT --new-port NEW_PORT`
to inspect the change. Add `--apply` only after the new listener passes TCP and
UDP checks. The same command with the ports reversed is the rollback; it
preserves the route's current user set.

### Protocol connectivity probes

`scripts/lib/diagnostics/probe_protocols.py` fetches a real Feint subscription, starts an
isolated Xray client for each listed VLESS or Hysteria2 URI, and checks both
HTTPS egress and UDP DNS through its local SOCKS interface. It writes a JSON
report and exits unsuccessfully if a published profile fails either check.
Candidate URIs supplied with `--candidate-uri-file` are measured without
affecting the exit status. Use them to observe a disabled protocol before
considering it for publication. The probe never changes node configuration or
automatically republishes a protocol.

Run it from a separate host with Docker and a dedicated test subscription:

```bash
export FEINT_PROBE_SUBSCRIPTION_URL='https://vpn.example.com/userapi/v1/sub/TEST-ACCESS-ID'
python3 scripts/cli.py probe --status-file /var/lib/feint-probe/status.json --vantage external
```

The sample systemd service and timer are in `ops/probe/`. Install the probe
script at `/opt/feint-probe/probe_protocols.py` and put the dedicated URL in
`/etc/feint/probe.env` with mode `0600`. Optional unpublished test URIs go in
`/etc/feint/probe-candidates.txt`, also mode `0600`. The timer runs every five minutes.
Check `systemctl status feint-protocol-probe.service` and the JSON report for
per-profile TCP, UDP, and observed egress IP. A successful datacenter probe
does not prove access from a residential or mobile network; keep a probe in
each target access network before using results to change publication policy.

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
python3 scripts/cli.py ports show
python3 scripts/cli.py ports check
python3 scripts/cli.py ports randomize
```

Apply selected ports atomically:

```bash
python3 scripts/cli.py ports set --api 8337 --vless 28473 --apply
```

Feint does not manage host firewall rules. Port changes only update the node
deployment.

Without `--apply`, the command only previews a validated port plan. `--apply`
updates `.env.local`, validates conflicts and duplicates, updates the
persisted sing-box configuration, restarts affected services, checks health and
restores the previous configuration if the rollout fails.

## ⚙️ Configuration

Runtime values live in `.env.local`. Start from [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_SECRET` | unsafe placeholder | Node API secret; must be replaced. |
| `API_PORT` | `8000` | API listener and Compose port. |
| `HIDE_ENDPOINTS` | `true` | Hide every route from unauthenticated callers. |
| `DEV_MODE` | `false` | Enable docs and run uvicorn without TLS. |
| `SERVER_DOMAIN` | `example.com` | Public host used in generated URLs. |
| `SUBSCRIPTION_ENABLED` | `false` | Enable `/sub/{username}`. |
| `SUB_URI_TEMPLATE` | `🌌 Feint \| {Protocol}` | Display label for generated URIs. |
| `PUBLISHED_PROTOCOLS` | empty | Optional comma-separated allowlist for generated connection URLs; empty publishes every configured protocol. |
| `NODE_IMAGE` | `ghcr.io/feint-vpn/feint-node:latest` | Published node API image. |
| `SINGBOX_IMAGE` | `ghcr.io/feint-vpn/feint-sing-box:v1.13.19-feint.2` | Feint sing-box runtime image; non-main installs use the branch image. |
| `XRAY_IMAGE` | `ghcr.io/feint-vpn/feint-xray:v26.7.28-feint.1` | Unmodified official Xray binary/assets, packaged with `NET_BIND_SERVICE` on the binary so UID 1000 can bind UDP/TCP 443. No host sysctl change or root runtime. |
| `XRAY_REALITY_MIN_CLIENT_VERSION` | empty | Optional REALITY version threshold; `1.8.1` permits the tested sing-box client, with the security tradeoff described above. |
| `RATHOLE_IMAGE` | `ghcr.io/feint-vpn/feint-rathole:v0.5.0-feint.1` | Pinned reverse-transport sidecar. |
| `VPN_RUNTIME` | `sing-box` | Selected VPN core: `sing-box` or `xray`. |
| `VLESS_PORT` | `443` | VLESS Vision REALITY listener. |
| `REALITY_PRIVATE_KEY` | generated | Server-only REALITY private key. |
| `REALITY_PUBLIC_KEY` | generated | Public key included in VLESS share URLs. |
| `REALITY_SHORT_ID` | generated | Per-node REALITY short ID. |
| `REALITY_SERVER_NAME` | `google.com` | TLS handshake camouflage name. |
| `VMESS_PORT` | configurable | VMess WebSocket listener. |
| `TROJAN_PORT` | configurable | Trojan listener. |
| `HYSTERIA2_PORT` | prefers `443` on a new Xray node | Hysteria2 UDP listener; a free port is selected if UDP `443` is occupied. |
| `HYSTERIA2_COMPAT_PORT` | empty | Optional second Xray Hysteria2 UDP listener using the same users; keep an old port working after moving `HYSTERIA2_PORT`. |
| `HYSTERIA2_PUBLIC_PORT` | empty | Optional client-facing UDP port used in generated Hysteria2 URLs without changing the runtime listener. |
| `SHADOWSOCKS_PORT` | configurable | Shadowsocks listener. |
| `REVERSE_PROXY_PORT` | generated | Loopback-only SOCKS5 exit exposed to the reverse client. |
| `RATHOLE_PRIVATE_KEY` | generated | Server-side Noise private key. |
| `RATHOLE_PUBLIC_KEY` | generated | Public Noise key returned by `/reverse/key`. |
| `CONFIG_PATH` | `/opt/sing-box/config.json` | Persisted canonical node configuration. |
| `XRAY_CONFIG_PATH` | `/opt/sing-box/xray.json` | Generated Xray runtime configuration. |
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
uv sync --project services/node --locked --extra dev
```

Run quality checks:

```powershell
uv run --project services/node ruff format --check --config services/node/pyproject.toml services/node/src services/node/tests tests scripts
uv run --project services/node ruff check --config services/node/pyproject.toml services/node/src services/node/tests tests scripts
```

Run the development container:

```bash
cp .env.example .env.local
python scripts/cli.py compose --dev up --build
```

Production dependencies are in `services/node/requirements.txt`. Lint tools are isolated
in `services/node/requirements-dev.txt` and the `dev` uv extra; they are not installed
into the production image.

## 🛸 Deployment operations

Use the operator CLI: it supplies the installation root and environment file,
preserving the existing Compose project and volume names:

```bash
python scripts/cli.py compose ps
python scripts/cli.py compose logs -f vpn-node-api sing-box
python scripts/cli.py compose restart vpn-node-api
```

During a planned sing-box stop or restart, `v1.13.19` may log
`sing-box did not closed properly: close v2ray server: ... use of closed network connection`.
This is a harmless upstream double-close message when the container exits with code `0`,
starts again and the node health check remains healthy. Investigate it only when shutdown
fails, the container stays stopped or `/health` becomes degraded.

Update an installed node:

```bash
cd /opt/vpn-node
sudo ./update.sh
```

Additional operational references:

- [Deployment scripts](scripts/README.md)
- [Practical example](scripts/USAGE_EXAMPLE.md)
- [Quick start](QUICK_START.md)

## 🔭 Project structure

```text
feint-node/
├── services/
│   ├── node/
│   │   ├── src/              # Existing API/domain/adapters/utils architecture
│   │   ├── tests/            # API contracts and HTTP integration
│   │   ├── templates/        # Canonical runtime templates and examples
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── uv.lock
│   ├── xray/                 # Image, patches, source additions and Go tests
│   ├── sing-box/             # Optional runtime image
│   └── rathole/              # Reverse transport image
├── tests/
│   ├── contract/             # Operator, resource and interconnect contracts
│   ├── integration/          # Disposable multi-component scenarios
│   ├── acceptance/           # Published-runtime and subscription checks
│   └── fixtures/configs/     # JSON test inputs, not executable scenarios
├── scripts/
│   ├── cli.py                # Single operator entry point
│   └── lib/                  # install/update/runtime/interconnect/diagnostics
├── deploy/
│   ├── compose/              # base.yml, dev.yml, interconnect.yml
│   └── systemd/              # Host probe and redirect units
├── docs/
├── static/
├── install.sh                # Compatibility bootstrap
└── update.sh                 # Compatibility entry point
```

The internal application architecture is unchanged. `ops/` has been split by
responsibility between deployment units and diagnostic implementations. See
[layout and compatibility](docs/repository-layout.md) and [operator commands](scripts/README.md).

## 🌑 Current boundaries

- Authentication is a shared node secret, not user Bearer/JWT authentication.
- `/sub/{username}` is an optional local subscription endpoint.
- Let's Encrypt installation currently uses the standalone HTTP challenge.
- sing-box is controlled through the mounted Docker socket.
- Traffic totals are persisted as local operational state.

## ✅ Quality

```bash
uv sync --project services/node --extra dev --frozen
uv run --project ../pipeline python ../pipeline/scripts/stage.py node --run --fresh
```

The sibling `pipeline` runs lint, types, formatting, service/operator contracts
and isolated Docker integrations using the node service's environment. It does
not deploy to a server. For standalone static checks:

```bash
uv run --project services/node ruff check --config services/node/pyproject.toml services/node/src services/node/tests tests scripts
uv run --project services/node basedpyright --project services/node
```

`utils/settings.py` is the process configuration boundary (`BaseSettings`):
adapters receive values rather than parsing the environment again. The Python
configuration accepts `DEV_MODE=true`, `1` and `yes`; use `true` with the existing
Docker shell entrypoint, whose TLS startup behavior is unchanged.
Subscription template edits are persisted separately by
`adapters/subscription_settings.py` and take effect immediately.

`api/services.py` creates application-scoped dependencies during lifespan and shares
one configuration mutation lock between users and outbounds. Docker connections remain
lazy so a missing daemon does not prevent the diagnostic API from starting.
`api/errors.py` translates domain failures while keeping runtime details out of responses.
`build_xray_config()` is a pure transformation; its file/CLI wrapper accepts explicit
options from process configuration. Neither this separation nor the user-service
typing changes the HTTP routes, credential format or rollback sequence.

See [acceptance scenarios and release gates](tests/README.md) for actual
commands and coverage. Lint, local contract checks, live runtime acceptance and
clean installation/update are separate gates; none implies the others passed.

## Resource overhead

The traffic tracker reuses one gRPC channel per stats endpoint for its lifecycle,
retaining the five-second poll interval, retry behavior and persisted byte totals.
Shutdown closes the channels; transient RPC failures do not discard traffic totals.
Atomic traffic snapshots include source checkpoints so restarting only the API
does not count the still-running core's bytes twice. Disk writes run outside the
API event loop; shutdown waits for the writer before saving its final snapshot.
Legacy snapshots retain their recorded totals and establish a live baseline on
the first successful poll. Bytes between the last legacy snapshot and that poll
cannot be reconstructed; this transition does not rewrite historical totals.
Xray enables upload/download counters but not its separate online-IP tracking map:
Feint's `last_seen` comes from traffic changes, not the online-IP RPCs.
This describes freshly generated configuration. A live legacy core keeps its
online-IP policy until a separately approved reconfiguration; API-only updates
do not disable it or restart HY2.

Transport canaries include whole-host and Tailscale resources and separate bulk
and UDP technical clients. This instrumentation is not a per-user bandwidth
scheduler: shared CPU caps must not be presented as account-level fairness.

Measurements, accepted overload behavior and rollout gates:
[Resource budget](docs/resource-budget.md).

## License

MIT
