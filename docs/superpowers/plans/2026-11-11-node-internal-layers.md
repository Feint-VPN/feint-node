# Node Internal Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task, inline after owner review. Steps use
> checkbox syntax to track independently verifiable increments.

**Goal:** Implement the approved node layers and explicit package imports without
changing public behavior or deployed servers.

**Architecture:** Keep packages directly under `services/node/src`; no surrounding
`feint_node`, top-level `abc` or `types`. Services own workflows, store/runtime own
I/O, contracts define their boundaries, API remains an HTTP adapter, and application
composition owns resources and the shared mutation lock.

**Tech Stack:** Existing Python 3.11+, FastAPI/Pydantic, asyncio, Docker SDK, gRPC,
uv, Ruff, basedpyright, unittest executable contracts and workspace pipeline.

**Spec:** `docs/node-internal-architecture.md`.

## Global Constraints

- No production, Tailscale, firewall, live-user or dependency changes. No direct
  image publication or deployment; the approved feature push may run existing CI.
- No worktrees, branch changes or main/dev integration. The owner's subsequent
  approval permits commits and push to the existing feature branch after checks.
- Preserve untracked `scripts/update_tailscale.sh` and unrelated sibling-repo edits.
- Preserve HTTP, environment/configuration semantics, protocols and secret redaction.
- Use explicit package exports; imports must not start background work or open I/O.
- One shared configuration lock; no change to hot/cold apply or rollback policy.
- Preserve persisted traffic totals and per-source checkpoints across restarts.
- Keep large-data behavior unchanged; do not introduce new unbounded retained queues.

## Review Focus

1. Old deployed overlay destinations must not be confused with relocated local sources.
2. Importing public packages must not create Docker/gRPC clients, tasks or read settings.
3. User and outbound mutations must remain serialized against the same configuration.
4. Counter reset, legacy checkpoint migration and failed writes must not lose/double totals.
5. Lifespan cancellation/failure must close owned tasks/channels without restarting VPN.

## Task 1: Baseline, contracts and structural relocation

**Files:** Move `domain/ports.py` to `contracts/`; `domain/errors.py` to
`exceptions/__init__.py`; `domain/{models,users,telemetry}.py` to `models/`;
`domain/{user_service,outbound_service,mutation}.py` to `services/`;
`adapters/singbox_file_store.py` to `store/configuration.py`;
`adapters/{docker_runtime,tuic_runtime,rathole_runtime}.py` to `runtime/`;
`adapters/xray_{config,control,socks}.py` to `runtime/xray/`;
`adapters/url_builder.py` to `services/links.py`; `utils/settings.py` to
`config.py`; `utils/logging_config.py` to `log.py`. Keep crypto's small existing
helper with the user service, not a new generic utils package.

**Interfaces:** Preserve existing operation signatures and result shapes. Export
`UserService`, `OutboundService`, `ConfigStore` and `ContainerRuntime` deliberately;
rename the existing I-prefixed contracts with their call sites, not duplicate them.

- [x] Run `uv run --no-project scripts/stage.py node --run --through test --fresh`
  from pipeline and record any pre-existing failure before changes.
- [x] Add `services/node/tests/contract/architecture_contract.py`: create users and
  assign an outbound through public exports against a real temporary file store;
  assert resulting saved state. In a subprocess deny external connection creation
  while importing services/contracts/models; assert no startup work occurred.
- [x] Run the new scenario with the service Python; expect missing new packages.
- [x] Perform the bounded move/import rewrite, add explicit `__init__.py` exports,
  and update Ruff/type-checker configuration paths without changing behavior.
- [x] Run the new scenario plus existing service, permissions and hot-reload contracts.
- [x] In the existing concurrency scenario retain both user and outbound writes;
  inject failed/cancelled apply and assert exact previous file state is recovered.

## Task 2: Application ownership and HTTP boundary

**Files:** `src/application.py`, `src/main.py`, `src/api/{services,startup}.py`,
`src/api/routers/{system,user,reverse,stats,sub}.py`, API dependencies and schemas,
`services/node/tests/integration/route_subscription.py`.

**Interfaces:** Application owns the existing service instances and one lock.
`main:app` remains the ASGI entry point. Existing controller dependency functions
resolve that application's services; HTTP paths and payloads stay unchanged.

- [x] Add repeated application start/stop coverage to the architecture contract;
  retain existing HTTP/status coverage and verify the poll task and channels close.
- [x] Verify the added behavior against the baseline; reproduce any new-boundary
  expectation that fails before introducing the application owner.
- [x] Move composition/startup orchestration to application; keep routers, API
  authentication and error mapping HTTP-only. Move health/status handlers to router.
- [x] Run the full HTTP scenario, including errors, user deletion/stat reset,
  disabled subscriptions, template updates and extra-field preservation.

## Task 3: Traffic responsibilities

**Files:** `src/contracts/{statistics,traffic}.py`, `src/models/traffic.py`,
`src/runtime/v2ray.py`, `src/store/traffic.py`, `src/services/traffic.py`,
traffic parts of `service_contract.py`, new focused traffic contracts.

**Interfaces:** `TrafficTracker` keeps start/stop/get_user/get_all/is_available/
reset_user behavior. Inject a counter source and checkpoint store; preserve file
schema, polling/flush timing, per-source reset detection and cancellation handling.

- [x] Add/retain real-file/loopback-gRPC tests: two sources, core counter reset, restart
  from persisted checkpoints, legacy totals without checkpoints, corrupt input,
  failed write then retry, and stop during an in-progress write.
- [x] Record current expected outcomes before moving code; assert final totals
  and saved checkpoints, not just helper calls or source text.
- [x] Extract gRPC/message handling and file reads/writes; retain accumulation,
  scheduling, cache ownership and accounting rules in the service.
- [x] Run traffic, lifecycle, permissions and HTTP scenarios; compare checkpoint
  fixtures and assert no duplicate channels/tasks remain after shutdown.

## Task 4: Subscription, telemetry and reverse boundaries

**Files:** `src/services/{subscription,telemetry,reverse}.py`,
`src/store/{settings,reverse}.py`, `src/runtime/{system,rathole}.py`, required
specific contracts/models, API routes and existing service/HTTP contracts.

**Interfaces:** Preserve current subscription output bytes, VMess JSON labels,
public keys, reverse configuration models, disabled endpoint behavior and errors.
Services receive dependencies/settings values explicitly, not global HTTP objects.

- [x] Cover label escaping/format validation/order, settings persistence and
  disabled/unknown subscriptions through existing real HTTP/file scenarios.
- [x] Cover failed reverse apply restoring both configuration and state, plus
  status when configuration or host telemetry is unavailable.
- [x] Move content/workflow decisions into services and actual I/O into store/runtime;
  add interfaces only where a concrete tested I/O boundary requires them.
- [x] Keep secret-bearing raw models out of public status/response exports; run
  error/redaction and unchanged HTTP-response scenarios.

## Task 5: Operator consumers, pipeline and final verification

**Files:** `scripts/lib/{install,update,runtime,interconnect,diagnostics}/`,
root/service contracts, README, `docs/repository-layout.md`, `tests/README.md`,
sibling pipeline node adapter and its existing acceptance fixture where required.

**Interfaces:** Current conversion commands use `python -m runtime.xray.configuration`.
Bundle allowlists include every new imported dependency and package initializer.
Legacy overlay destination paths retain their explicit supported compatibility.

- [x] Add regression scenarios that execute current conversion CLI from an unrelated
  directory, load the exact remote source bundle in isolation, and generate overlays
  against representative legacy-layout fixtures; validate resulting configuration.
- [x] Update current consumers and pipeline path references together; do not edit
  ignored incident artifacts or historical records just to eliminate old strings.
- [x] Run node stage through verify/test, then isolated Docker acceptance if available.
  If pipeline code changed, run its full fresh stage and report unrelated blockers.
- [x] Build the API image and exercise development-runtime HTTP inside that image,
  without production volumes/socket; confirm main:app startup and lifecycle cleanup.
- [x] Review the diff and dependency graph for cycles, catch-all exports, unrelated
  edits, changed protocol/defaults and credential artifacts. Report exact open gates.

## Execution ledger

- Owner approved the proposed layers, then explicitly rejected the outer package.
- Current node branch: `feature/repository-layout`; baseline HEAD `19b3a49`.
- Owner reviewed the plan and authorized inline implementation and feature push.
- Baseline pipeline verify and all fifteen test scenarios passed before source edits.
- Review found a corrected fixture still unstaged; include the tested working-copy
  correction in the commit rather than claiming the old index was verified.
- Review found legacy/new exception identity mismatch in generated overlays.
  Added a subprocess regression for both overlay modes: real runtime failure must
  remain catchable by legacy handlers. Observed RED, then GREEN after explicit
  legacy exception re-exports. Current source does not retain legacy packages.
- Review boundary: partial-start/first-close failure gaps also reproduce in the
  baseline. Do not combine unrelated lifecycle hardening with this refactor.
- Local node verify, eighteen scenario entry points and isolated Docker TCP/UDP/auth
  acceptance passed again after the final compatibility correction (source
  fingerprint `56726ff0172294ed`). Both actionable review findings are resolved.
- API image built locally; isolated in-image HTTP passed without external networking
  or Docker socket. The compatibility correction changes tooling/tests, not image source.
- Direct pipeline acceptance passed. Its full fresh stage is blocked by pre-existing
  formatting in `scripts/adapters/clients.py`; unrelated dirty edits remain intact.
- Pipeline adapter/fixture updates remain local in the already-dirty sibling repo;
  they are not included in the node feature push.
- Server installation/update/rollback/ACME, deployed overlay variants, public HY2,
  WAN throughput, Discord and real-client acceptance remain separate release gates.
