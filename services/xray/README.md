# Xray runtime

The image uses Xray 26.7.28 at commit
`5ca6f4b7d4dc20a881d4330e498892697627ec0c` and its matching geodata.

`patches/mtu-bbr.patch` keeps Hysteria2 initial packets at 1200 bytes for the existing
client/path compatibility and lets BBR rescale its windows when QUIC reports a
smaller datagram size. Upstream otherwise panics on the observed 1280 → 1200
transition, terminating the entire runtime rather than one connection.
Path MTU discovery and the congestion algorithm remain unchanged.

The build first reproduces that panic on the pinned upstream code, then runs
the congestion tests against the patch. `tests/bbr_mtu_test.go` checks all three BBR
profiles, window rescaling, decreases after discovery, and an unchanged MTU.

```sh
docker build -t feint-xray:26.7.28-mtu1200-bbrfix-reset ./services/xray
```

Building and unit tests do not establish VPN compatibility. Before deployment,
test the resulting image with real Hysteria2 clients over TCP and UDP, validate
the installed configuration and retain the previous image for rollback. Deploy
only the runtime service; do not restart the API, interconnect or Tailscale.

## Hysteria2 recovery after lost sessions

`patches/stateless-reset.patch` connects the server transport to `src/stateless_reset.go`.
Set `XRAY_HYSTERIA_STATELESS_RESET_KEY_FILE` to a read-only mounted file containing
exactly **32 raw random bytes**, generated once for that node. Keep it private,
back it up securely and preserve it across container replacement and host reboot.
Do not put its contents in an image, environment variable, repository or logs,
and do not reuse the key on another node. The runtime user must be able to read it.

Without this environment variable the upstream behavior is unchanged. An explicitly
configured missing, unreadable or malformed file prevents listener creation and
releases the packet socket. Reads are bounded to 33 bytes; keys are loaded at
listener startup, not on the per-packet path. No QUIC timers, windows, congestion
control, routing, authentication or client subscription formats are changed.

The build runs the file-key regression tests before and after applying the patch.
They cover the absent setting, persistent key reuse and invalid/missing files.
Runtime acceptance must additionally replace a server while keeping its key and
exercise an existing client connection: the stale request may fail immediately,
but the next request should establish a new session without the old idle wait.
Do not automatically replay application requests, especially writes.

Stateless reset only handles packets for sessions the server no longer knows.
It does not fix DNS failures or guarantee mobile wake/network-change latency.
Clients connected before the first deployment have old reset tokens and may still
need their normal timeout or one manual reconnect. An idle/pause test is not real
iOS sleep acceptance. See [quic-go's transport documentation](https://quic-go.net/docs/quic/transport/#stateless-resets).
