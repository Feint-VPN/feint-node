# Optional TUIC companion

TUIC runs in an additional sing-box container. Xray continues to serve existing
VLESS/Hysteria2 clients; their addresses, UUIDs and passwords do not change.
Published TUIC profiles include the direct entry-node route and existing
SDK-managed SOCKS cascades. Routing uses the existing per-user access IDs; a
cascade does not create a new interconnect per user. Its SOCKS transport must
support both TCP and UDP before publication.
TUIC is not a guarantee against UDP filtering or a substitute for client-side
Discord/game acceptance.

## Configuration

Add a canonical inbound with type `tuic`, tag `tuic-in`, a free UDP port, and the
existing valid TLS certificate/key paths. Each user needs both UUID and password.
Use `congestion_control: cubic`, `heartbeat: 10s`, `zero_rtt_handshake: false`, and
TLS ALPN `h3`. The TLS and inbound models must preserve additional fields.

Set `TUIC_CONTAINER_NAME`, `TUIC_CONFIG_PATH`, `TUIC_STATS_LISTEN`, and
`TUIC_STATS_ADDRESS`. The stats listener must bind only to a private host bridge
address that the API can reach. Never publish its unauthenticated gRPC port.
Start the optional `tuic` Compose profile. Do not run a global Compose restart.

User operations update both cores under the existing mutation lock and rollback
contract. Missing TUIC credentials are repaired from existing user UUID/password;
they are not rotated. A TUIC-only config change does not restart unchanged Xray.
Traffic counters are tracked separately per runtime before adding their deltas,
so resetting one core does not reset or duplicate the other core's traffic.

## Existing production versions

`scripts/tuic_overlay.py` builds a minimal overlay of the installed API, avoiding
deployment of unrelated development changes. `scripts/tuic_stage.py` snapshots
the node config, Compose, environment, traffic state and container metadata,
checks the candidate sing-box config, and stages TUIC without restarting services.
These are explicit operator tools for the current Feint Compose adapter, not a
general installer. Inspect their target parameters before use. Staging is a
one-time operation; it refuses an already configured TUIC inbound.

Provision existing authorized live users with `node.user.create_many(...)` using
Feint SDK. Do not provision paused users or mutate database documents directly.
Invalidate and rebuild subscription caches through the subscription SDK. Verify
the existing public HTTPS subscription URLs, not only locally generated content.

## Acceptance and rollback

Feed an actual SDK-generated technical TUIC URI to `scripts/probe_tuic.py` on
stdin; never put credentials in command arguments or logs. It verifies TLS,
expected exit IP, HTTPS, UDP DNS, and rejected credentials with `--reject`.
The client parser explicitly requests native QUIC UDP relay. Check SDK traffic
after the probe. Remove the technical user through SDK and delete URI artifacts.

Before rollout, a freshly rendered Xray config must be semantically identical to
the working one. After provisioning, compare primary-core/interconnect start
times, legacy inbounds and credentials with the snapshot. Verify user creation,
removal and partial-runtime rollback locally before publishing.

For rollback, restore the saved node Compose/environment/config and the original
API image, recreate only the node API, then stop/remove only the TUIC companion.
The primary core requires no restart if its config and credentials stayed intact.
Invalidate subscription caches again through SDK. Preserve Tailscale, certificates,
interconnect, application databases and unrelated containers.

On SPb, the 2026-10-03 rollout used UDP 8443 and the existing `vpn.feint.cloud`
certificate. All seven public subscription URLs returned TUIC without URL/key
rotation. The external GE client passed 3/3 HTTPS and 100/100 UDP DNS requests;
invalid and subsequently revoked technical credentials were rejected. SDK traffic
was available. Xray and the existing interconnect kept their original start times.
This is server acceptance, not proof of Discord quality from every Russian ISP.

The later RU-to-GE publication adds TUIC to the SOCKS route's supported-protocol
set. It does not change the interconnect, canonical auth rules or credentials.
Check that the chosen application's importer and active core support TUIC; a
protocol present in the HTTP subscription can still be omitted by the client.

Backups must include `tuic.json` and portable overlay sources/Dockerfile as well as
the canonical config. A local image tag alone is not sufficient for disaster recovery.

## SW rollout, 2026-10-04

SW publishes TUIC on UDP 8443 with the existing `emerald.service.feint.cloud`
certificate. The main SDK also publishes a separate TUIC RU-to-SW route using
an authenticated SOCKS/UoT link over the existing Tailscale network. The link is
externally managed; `make_cascade(..., socks=...)` registers its loopback endpoint.
`manage_interconnect.py --root INSTALL_PATH` can run from a private operations
directory without replacing the installed node's scripts.

Original public subscription profiles passed HTTPS and UDP DNS with TLS checking:
SW and RU-to-SW exited at `2.27.23.104`; existing RU and RU-to-GE profiles passed
again. All seven public subscription URLs remained valid. Existing canonical
credentials were unchanged; adding route users required a brief RU-core reload.
The API, bot, old RU-to-GE transport and Tailscale were not restarted.

Direct TUIC SW timed out from the RU server but worked from GE. This is a measured
path difference, not proof that every Russian subscriber can reach SW directly.
Discord, gaming and throughput still require representative client acceptance.
The current production SDK may display the label as `Tuic` rather than `TUIC`.

The rollout snapshot sent to Telegram includes SW configuration, certificates,
credentials and portable overlay sources. Daily SPb backups now include its
managed interconnect state/tools; automatic remote SW snapshots are not yet wired.
