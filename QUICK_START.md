# ⚡ Quick Start — Feint VPN Node

Get a fully working 5-protocol VPN node running in under 10 minutes.

---

## ⚡ One-command install (recommended)

The fastest path — paste one line on a fresh Ubuntu 22.04/24.04 VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/Feint-VPN/feint-node/main/install.sh | \
  sudo bash -s -- --domain vpn.example.com --email admin@example.com
```

Replace `vpn.example.com` with your domain (must already have an **A record** pointing to the server IP) and `admin@example.com` with your email for Let's Encrypt.

The script handles everything with **zero prompts**:

- Installs Docker
- Clones the repo to `/opt/vpn-node`
- Generates all cryptographic keys (Reality x25519, Shadowsocks PSK, random ports)
- Issues a Let's Encrypt TLS certificate
- Builds and starts all containers
- Prints the API URL, secret, ports, and ready-to-use subscription URL

**Additional flags:**

| Flag         | Default         | Description                  |
| ------------ | --------------- | ---------------------------- |
| `--domain`   | _(required)_    | FQDN pointing to this server |
| `--email`    | _(required)_    | Let's Encrypt contact email  |
| `--secret`   | auto-generated  | API secret key               |
| `--api-port` | `8337`          | HTTPS API port               |
| `--dir`      | `/opt/vpn-node` | Install directory            |
| `--sub`      | `true`          | Enable subscription endpoint |
| `--branch`   | `main`          | Git branch to clone          |

When it finishes you'll see the API URL, all ports, and the Hiddify subscription URL.

## Update an installed node

To refresh code on a node that is already running in `/opt/vpn-node`:

```bash
cd /opt/vpn-node
sudo bash ./update.sh
```

To switch the deployed branch during update:

```bash
cd /opt/vpn-node
sudo bash ./update.sh --branch main
```

The updater preserves `.env.local` and existing users, applies the latest
sing-box template, validates it, pulls the published images, and waits for the
authenticated `/status` readiness check. A failed rollout restores the previous
commit, images and runtime configuration.

---

## Verify everything is running

```bash
cd /opt/vpn-node
sudo bash scripts/diagnose.sh
```

---

## 4 · Create your first user

```bash
curl -sk -X POST https://vpn.example.com:8337/user \
  -H "X-API-Secret: change-me-now" \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}'
```

Response contains the UUID + password used across all 5 protocols.

---

## 5 · Get the subscription link

Enable subscription in `.env.local`:

```bash
SUBSCRIPTION_ENABLED=true
SUB_URI_TEMPLATE=🌌 Feint | {Protocol}   # optional label template
```

Or update the label template over the API later, without restarting the node API:

```bash
curl -sk -X PUT https://vpn.example.com:8337/sub/settings \
  -H "X-API-Secret: change-me-now" \
  -H "Content-Type: application/json" \
  -d '{"sub_uri_template":"🌌 Feint | {Protocol} | {username}"}'
```

If you changed `.env.local` directly, restart the API:

```bash
docker compose up -d --force-recreate vpn-node-api
```

Subscription URL for the user:

```
https://vpn.example.com:8337/sub/alice
```

Paste this URL directly into **Hiddify → Add Profile → From URL**.  
All 5 protocols will appear automatically: VLESS, VMess, Trojan, Hysteria2, Shadowsocks.

`/sub/{username}` now uses the node's own `SERVER_DOMAIN` by default. Pass `?server_domain=...` only when you intentionally want generated share URLs to use a different public hostname.

---

## Protocols & ports (defaults)

| Protocol        | Port     | Transport                 |
| --------------- | -------- | ------------------------- |
| VLESS + Reality | 8552 TCP | Direct (xtls-rprx-vision) |
| VMess           | 489 TCP  | WebSocket + TLS           |
| Trojan          | 2267 TCP | TLS                       |
| Hysteria2       | 2294 UDP | QUIC                      |
| Shadowsocks     | 8654 TCP | 2022-blake3-aes-256-gcm   |

Override any port in `.env.local`:

```bash
VLESS_PORT=443
VMESS_PORT=8080
# etc.
```

---

## API reference (quick)

All write endpoints require the `X-API-Secret` header.

| Method   | Path                       | Description                                         |
| -------- | -------------------------- | --------------------------------------------------- |
| `GET`    | `/health`                  | Liveness check                                      |
| `POST`   | `/user`                    | Create user                                         |
| `GET`    | `/user/{username}`         | Get user info                                       |
| `DELETE` | `/user/{username}`         | Delete user                                         |
| `GET`    | `/user/{username}/configs` | Per-protocol share URLs                             |
| `GET`    | `/sub/{username}`          | Hiddify subscription (public, uses `SERVER_DOMAIN`) |
| `GET`    | `/sub/settings`            | Read subscription settings                          |
| `PUT`    | `/sub/settings`            | Update `SUB_URI_TEMPLATE`                           |
| `GET`    | `/stats`                   | Traffic statistics                                  |
| `GET`    | `/user/{username}/stats`   | Traffic statistics for one user                     |

---

## Troubleshooting

**Container won't start**

```bash
docker compose logs vpn-node-api --tail 50
```

**sing-box config rejected**

```bash
docker compose logs sing-box --tail 50
```

**Certificate not issued**

```bash
docker compose logs certbot
# Make sure port 80 is open and the domain resolves to this IP
```

**Subscription shows wrong IPs / wrong paths**

- By default `/sub/{username}` uses `SERVER_DOMAIN` from the node config
- Pass `?server_domain=your-domain.com` only if you intentionally want generated URLs to use another public hostname
- The public hostname must still match the TLS certificate the client will validate

---

## Updating

```bash
cd /opt/vpn-node
git pull
docker compose pull sing-box vpn-node-api
docker compose up -d --force-recreate sing-box vpn-node-api
```

sing-box config and user data are stored in a Docker volume — they survive rebuilds.
