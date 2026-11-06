# Xray hot registration

## Incident and scope

On 2026-10-04 at 16:56 MSK, one bot registration caused five RU Xray
restarts and one GE restart. User provisioning and cascade assignments
each committed a file and restarted the entire core. The containers exited
normally; this event was not an OOM or the previously fixed BBR/MTU panic.

The opt-in `XRAY_HOT_RELOAD=true` runtime applies HY2/VLESS user additions
through Xray HandlerService and existing-outbound routing assignments through
RoutingService. Existing streams stay attached to the running core.

This is not general zero-downtime configuration reload:

- Removing users or replacing credentials still restarts Xray, so hard
  revocation cannot leave an authenticated QUIC session usable.
- Changing listeners, certificates or outbound definitions still restarts it.
- The sing-box 1.13.19 TUIC companion still restarts on its own mutations.
- RPC errors are returned with their original failure after independent
  compensation attempts. Successful compensation does not restart the core.
  If any compensation cannot prove restoration, the applied snapshot is
  invalidated and the existing transaction rollback performs a full reload.
  This exceptional recovery can disconnect clients; failed full recovery is
  reported as `ConfigRollbackError`, never as a successful rollback.

## Private control endpoint

Enable HandlerService/RoutingService only on an internal listener. With host
networking, bind `XRAY_API_LISTEN` to the Docker bridge gateway; do not bind
it to `0.0.0.0`, a public address or a publicly forwarded port.
`V2RAY_API_ADDRESS` must point to the same listener from the API container.
The configuration generator rejects write-capable listeners unless the bind is
an explicit loopback IP, RFC1918 address or Tailscale address in `100.64.0.0/10`,
with a valid port. IPv6 loopback uses `[::1]:PORT`; wildcards, public addresses
and hostnames are rejected before writing the control configuration. Stats-only
defaults are unchanged. Private binding is not authorization for every peer on
that network; retain the deployment's existing network access restrictions.
The first enablement requires one deliberate Xray restart. StatsService is
retained and the Xray image, keys and public ports are unchanged.

`xray api adu` in 26.7.28 does not import Hysteria accounts. The adapter uses
the core's protobuf/gRPC UserManager directly, including the compatibility
HY2 inbound if configured.

## Verification

Run the project's pipeline `stage node --run --through test --fresh`.
Its contracts now include real gRPC user encoding, partial application
rollback, live routing restoration and preserved hard-revocation semantics.

The disposable `tests/acceptance/runtime.py --core xray --vless-client xray` adds
eight users and route assignments while holding one TCP DNS connection and
checking UDP. It asserts unchanged Xray StartedAt, then checks counters,
CRUD, route reassignment, restart persistence and configuration rollback.
This does not establish Discord quality on a representative client network.

## Narrow operational rollout

1. Confirm a fresh off-host backup.
2. `scripts/lib/runtime/hot_reload_overlay.py` builds an overlay of the installed API;
   it preserves the installed renderer except control services/CLI wiring.
3. Exercise that image in a disposable SW runtime stack first.
4. `scripts/lib/runtime/apply_hot_reload.py` saves rollback documents, freezes API writes,
   renders and validates the core configuration, restarts Xray once and
   recreates only the API. It does not change Tailscale or relay transports.
5. Use the Compose files recorded in the API container's labels for subsequent
   explicit `-f` operations. An absent standard override is also populated;
   do not accidentally omit the hot-reload override during later deployments.
6. `scripts/lib/runtime/hot_reload_canary.py` provisions only node-local technical users,
   checks existing credentials and StartedAt, and probes HTTPS/UDP. These
   probe accounts have no SDK wallet; credentials are not printed or saved.

Rollback snapshots and image IDs are stored in the selected private `--work`
directory. Keep them until manual acceptance; do not remove unrelated images.

Primary contracts: [Xray API](https://xtls.github.io/en/config/api.html),
[pinned Hysteria UserManager](https://github.com/XTLS/Xray-core/blob/5ca6f4b7d4dc20a881d4330e498892697627ec0c/proxy/hysteria/server.go).

## Verified rollout: 2026-10-04

- SW disposable acceptance: all runtime contracts passed; eight registrations
  and route assignments retained the same TCP stream, successful UDP replies
  and unchanged Xray StartedAt.
- API overlays installed on SW, GE and RU; control listens only on the private
  bridge gateway. Existing users/keys and the RU BBR/MTU-patched core image
  were retained. Tailscale, bot, database and interconnects were not restarted.
- GE technical registration: HTTPS + UDP passed, zero additional Xray restarts.
- RU technical registrations and both cascade assignments: zero additional
  Xray restarts and unchanged existing credentials. An independent GE client
  successfully exercised RU, RU → GE and RU → SW HTTPS + UDP, with their
  respective expected egress addresses.
- RU self-probing the public HY2 URL is not valid: UDP 443 is redirected to
  36454 by incoming PREROUTING DNAT, not by local OUTPUT. Keep an independent
  probe vantage; do not change public ports or NAT just to satisfy a self-test.
- Technical node-local users `ops-hot-probe-20261004-*` remain for monitoring.
  They have no SDK wallet and do not charge customers. Remove them in a planned
  maintenance window, because hard revocation still restarts Xray.
- Real Discord voice acceptance and TUIC zero-downtime user management remain
  unverified/unimplemented respectively. This rollout fixes the evidenced
  registration-triggered HY2 disconnect; it does not claim all jitter resolved.
