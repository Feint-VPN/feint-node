# Node acceptance

Run against disposable resources, never against a production database or client keys.

| Scenario | Evidence | Boundaries |
| --- | --- | --- |
| `service_contract.py` | File-backed bulk repair/idempotency/concurrency, failure/cancellation rollback, five URI formats, pure Xray transformation, settings and real loopback gRPC serialization/persistence | Runtime reload is a no-op or explicitly injected failure; no VPN-core connection |
| `route_subscription.py` | HTTP route assignment, URI publication, extra-field preservation, idempotent bulk creation, error responses and application lifespan cleanup | Development runtime; no VPN connection |
| `probe_stability_contract.py` | Real loopback SOCKS/UDP: one persistent association, missing replies and independent HTTPS failure reporting | Synthetic DNS replies; HTTPS is injected, no external VPN |
| `interconnect.py` | Real authenticated SOCKS/UoT TCP and UDP echo, unauthenticated rejection | Private Docker network, not a public subscriber network |
| `interconnect_load.py matrix` | Bare TCP/UDP baseline, native Xray SOCKS and sing-box SOCKS/UoT; integrity and UDP echo during bulk download; Linux cgroup v2 CPU totals | Defaults to 30 Mbit/s. Opt-in `--mbps 1000 --seconds 6` sends 750 MB per variant. Single host: no HY2 ingress, Tailscale or WAN performance claim |
| `interconnect_load.py matrix --hy2` | sing-box HY2 client → Xray HY2 relay → SOCKS/UoT, native SOCKS TCP/UDP or Shadowsocks 2022 exit; concurrent authenticated UDP echo and TCP integrity | Private Docker network, temporary pinned test CA, no public ports or production certificates. Maximum 100 Mbit/s and 60s per variant. No Tailscale/WAN or subscriber acceptance claim |
| `runtime.py --image IMAGE` | Published API image with real sing-box and Xray cores; subscription URI import, HTTPS/UDP, user CRUD, bulk/paging, stats persistence, route assignment and failed-mutation rollback | Disposable Docker stacks on Linux; not installer/update/SSH/ACME acceptance |

```bash
python acceptance/service_contract.py
python acceptance/route_subscription.py
python acceptance/interconnect.py
python acceptance/interconnect_load.py matrix
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

### Resource and competing-client evidence

`resource_contract.py` verifies proc/cgroup parsing, CPU units, quota throttling,
and daemon PID reuse against temporary filesystem fixtures. The real chain
runner also records five-second quiet windows and host/Tailscale/container
resources, with bulk TCP and low-rate UDP on separate technical HY2 accounts.
Container percentages use one core; host percentages use the whole machine.
Do not add overlapping CPU figures or interpret this bounded test as gigabit
capacity, public RF ingress, Discord acceptance, or per-user rate enforcement.
