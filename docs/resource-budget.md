# Resource budget and overload isolation

## Accepted behavior

Keep HY2 ingress and existing subscriptions. Reduce unnecessary idle work and
transport copies. Do not impose a permanent per-user rate cap: when spare capacity
exists, bulk transfers may use it; under contention, protect other clients and
latency-sensitive traffic. Keep administrative access independent of experiments.

## Observed baseline, 2026-10-10

Production SPb has two vCPUs, about 1.85 GiB RAM and no swap. A read-only snapshot
showed about 626 MiB available; Xray was about 33 MiB. API, bot and MongoDB also
consume the same budget. This was not a controlled idle or capacity measurement.
The physical interface uses `pfifo_fast`; Tailscale uses `fq_codel`. This is a
possible queuing issue, not a proven explanation for the reported outages.

The bounded RU–SW canary used separate HY2 credentials/connections for bulk TCP
and 50 small UDP echoes per second. Each variant transferred 112.5 MB at a
requested 30 Mbit/s. Ingress was Tailscale-only, not a public RF client.

| Entry transport | Measured TCP | Relay + entry sidecar CPU, one core | UDP loss | RTT p95 |
| --- | ---: | ---: | ---: | ---: |
| SOCKS/UoT | 29.42 Mbit/s | 21.33% | 4/1550 | 74.20 ms |
| Native UDP, same sidecar | 29.81 Mbit/s | 19.78% | 29/1550 | 82.38 ms |
| Native Xray SOCKS, no entry sidecar | 29.82 Mbit/s | 14.98% | 18/1550 | 75.65 ms |

Tailscale separately consumed 26.5–28% of one SPb core during this **double
Tailscale traversal** fixture. Whole-host CPU was 28.4–33%, including live traffic;
quiet-window CPU varied from 7.4% to 20.5%, so these host figures cannot establish
an A/B improvement. SW steal time varied from 3.7% to 9.1%. The native-sidecar
variant had one TCP pause over 500 ms. No variant is accepted as a voice fix.
Container peak memory was about 20.8 + 19.1 MiB for UoT relay/sidecar and 15.7 MiB
for the native relay without a sidecar. These are short-canary lifetime peaks,
not production subscriber memory bounds.

A reversed-order repeat (native, then UoT) kept both at 29.81 Mbit/s without TCP
pauses over 500 ms. Native used 15.15% of one SPb core with 4/1550 lost echoes,
p95 73.49 ms; UoT used 14.80% + 5.08% with 6/1550 losses, p95 82.42 ms.
Whole-host CPU was 29.34% versus 29.89%, with different background load and SW
steal time. This supports testing sidecar removal, but **does not establish a
25–30% whole-server CPU saving** or overload isolation.

## Small changes prepared in code

- Reuse one stats gRPC channel per endpoint; preserve poll frequency, cumulative
  byte accounting, RPC timeouts and retry backoff. Close channels on shutdown.
- Stop enabling Xray's unused online-IP map. `last_seen` continues to follow byte
  activity; uplink/downlink accounting remains enabled.
- Disable per-connection access logging in fresh Xray configurations; warning/error
  logging remains enabled. Live user/route updates preserve the running core's
  access-log setting, including legacy stdout behavior, to avoid an implicit restart.
- Include host/Tailscale CPU, softirq/steal, cgroup throttling and memory in tests.
  These counters overlap: never add host, process and container percentages.

The online-IP policy change requires runtime reconfiguration and may restart
Xray with the current reload rules. Nothing in this work authorizes that restart.
Production configuration and links remain unchanged. No measured production
idle CPU/RAM reduction is claimed before a controlled rollout.

Hot route updates preserve an already-running legacy online-IP policy so it cannot
accidentally force a HY2 restart during migration. Fresh configurations no longer
enable it; removing it from a live core remains a separately approved operation.

## Remaining implementation, before production migration

1. Confirm native transport quality with repeated measurements and public-client
   voice acceptance; do not pick a protocol from one short sequential run.
2. Remove the entry sidecar through the canonical node/SDK configuration. Direct
   Xray SOCKS needs credential rendering and safe outbound lifecycle first;
   prepared versioned SOCKS handlers now allow route-only hot updates and rollback
   while retaining old handlers. The native endpoint and credentials also pass
   through SDK reconciliation. Publish/review this exact code before applying it;
   retain UoT as an exact rollback. Old sidecars cannot be stopped until their
   existing flows drain. Preserve the legacy online-IP policy during this cutover;
   its removal remains a separate restartful migration.
3. Test a fair queue/AQM in disposable SW network namespaces, not the host NIC.
   A scheduler per flow or source IP is not per-account fairness: multiple QUIC
   connections and shared NAT must be covered before making that guarantee.
4. Define the shared overload budget from measured CPU/latency headroom. A global
   Docker CPU quota just throttles every user together; it is not the scheduler.
   Test one bulk user against another user's UDP, then multiple bulk connections,
   including latency recovery after load ends. A 400-Mbit/s bulk scenario remains
   untested; 30-Mbit/s integrity success does not prove it.
5. Only after those gates: backup, explicit scoped rollout, public TCP/UDP and
   manual voice checks, with independently verified rollback. Do not alter
   Tailscale, shared firewall or host queue settings without separate approval.

Primary references: [Xray 26.7.28 online-IP tracking](https://github.com/XTLS/Xray-core/blob/v26.7.28/app/dispatcher/default.go),
[Xray access/error logging](https://xtls.github.io/en/config/log.html),
[gRPC channel reuse](https://grpc.io/docs/guides/performance/),
[Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/),
[CAKE flow and host isolation](https://man7.org/linux/man-pages/man8/tc-cake.8.html).
