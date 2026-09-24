# Node acceptance

Run against disposable resources, never against a production database or client keys.

| Scenario | Evidence | Boundaries |
| --- | --- | --- |
| `route_subscription.py` | HTTP route assignment, URI publication, extra-field preservation, idempotent bulk creation | Development runtime; no VPN connection |
| `interconnect.py` | Real authenticated SOCKS/UoT TCP and UDP echo, unauthenticated rejection | Private Docker network, not a public subscriber network |
| `runtime.py --image IMAGE` | Published API image with real sing-box and Xray cores; subscription URI import, HTTPS/UDP, user CRUD, bulk/paging, stats persistence, route assignment and failed-mutation rollback | Disposable Docker stacks on Linux; not installer/update/SSH/ACME acceptance |

```bash
python acceptance/route_subscription.py
python acceptance/interconnect.py
python acceptance/runtime.py --image ghcr.io/feint-vpn/feint-node:sha-COMMIT
```

The runtime scenario requires Docker Compose, Python 3.11+, OpenSSL and curl on the
test host. It uses unique Compose/container names, a temporary self-signed test
certificate (explicitly trusted by its client), and loopback-only API/SOCKS ports.
It imports the API-issued URIs without replacing their credentials or target ports.
It does not change host SSH, firewall, Tailscale or unrelated containers.
Public probes use HTTPS `example.com` and UDP DNS `1.1.1.1`; these do not measure
subscriber reachability, Discord voice quality or sustained throughput.

Set `SINGBOX_IMAGE` to the candidate sing-box image when testing runtime changes;
otherwise the last stable image is used. In pipeline targets this is `singbox_image`.
`--core` selects a server runtime. `--vless-client xray` explicitly tests native
Xray VLESS clients; it does not establish sing-box client compatibility.
For the opt-in compatibility mode, use `--core xray --reality-min-client-version 1.8.1`
with the default sing-box client. Also run the native Xray case with an empty override.

Use the workspace pipeline `server-test node --target NAME` with an explicitly
configured `scenario = "runtime"` and published `image`. Logs and failures belong
to pipeline's ignored `.state`, not Git. Prefer an immutable image digest.

## Release gate

Feature branch from `dev` → publish image → SW acceptance → merge/push `dev` →
repeat SW acceptance. `main` requires the owner's separate manual approval.
Runtime acceptance alone is **not** permission to skip clean installation, update,
port changes, backup restoration or deployment rollback. Those must have their own
recorded passing results before declaring the full gate complete.

Updates preserve configured protocol ports. New installations do not install a
global TCP MSS override; any network-specific tuning is a separate operator action.
Development images do not overwrite the stable sing-box/rathole version tags.
